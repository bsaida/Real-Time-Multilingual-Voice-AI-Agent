# main.py
#
# FastAPI entry point. Defines all HTTP and WebSocket endpoints.
#
# Routes:
#   GET  /              → health check / info
#   GET  /health        → simple alive check
#   POST /api/sessions  → create a new session
#   GET  /api/sessions/{id}  → inspect a session
#   POST /api/chat/text      → text-based chat (no audio, good for testing)
#   POST /api/chat/voice     → upload an audio file, get text + audio back
#   GET  /api/tts            → convert text to audio directly
#   GET  /api/appointments/{patient_id}
#   GET  /api/availability
#   GET  /api/doctors
#   POST /api/campaigns/trigger
#   WS   /ws/voice/{session_id}   → real-time WebSocket voice pipeline
#   GET  /demo          → browser-based demo UI

import asyncio
import base64
import json
import os
import sys
import time
import uuid
from typing import Optional

from fastapi import (
    FastAPI, WebSocket, WebSocketDisconnect,
    Depends, HTTPException, UploadFile, File, Form
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

sys.path.append(os.path.dirname(__file__))
from config import settings
from backend.database import get_db, init_db
from memory.session_memory.session_manager import session_memory
from memory.persistent_memory.persistent_memory import persistent_memory


app = FastAPI(
    title="Voice AI Appointment Agent",
    description="Real-time multilingual voice AI for clinical appointment booking",
    version="1.0.0"
)

# allow all origins for the demo — tighten this in production
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def on_startup():
    print("Starting Voice AI Appointment Agent...")
    init_db()
    print(f"Ready. Latency target: {settings.latency_target_ms}ms")


# ──────────────────────────────────────────────────────────
# Health / Info
# ──────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {
        "service":          "Voice AI Appointment Agent",
        "status":           "running",
        "supported_langs":  ["en", "hi", "ta"],
        "latency_target_ms": settings.latency_target_ms,
        "docs":             "/docs"
    }


@app.get("/health")
def health_check():
    return {"status": "ok", "ts": time.time()}


# ──────────────────────────────────────────────────────────
# Session management
# ──────────────────────────────────────────────────────────

class NewSessionRequest(BaseModel):
    patient_id: Optional[int] = None
    language:   Optional[str] = "en"


@app.post("/api/sessions")
def create_session(req: NewSessionRequest):
    sid = session_memory.create_session(
        patient_id=req.patient_id,
        language=req.language or "en"
    )
    return {"session_id": sid, "language": req.language}


@app.get("/api/sessions/{session_id}")
def get_session(session_id: str):
    return session_memory.get_session(session_id)


@app.delete("/api/sessions/{session_id}")
def end_session(session_id: str):
    session_memory.delete_session(session_id)
    return {"deleted": True}


# ──────────────────────────────────────────────────────────
# Text chat — handy for testing without a microphone
# ──────────────────────────────────────────────────────────

class TextChatRequest(BaseModel):
    text:         str
    session_id:   Optional[str] = None
    patient_id:   Optional[int] = 1
    language:     Optional[str] = None
    return_audio: Optional[bool] = False


@app.post("/api/chat/text")
async def text_chat(req: TextChatRequest, db: Session = Depends(get_db)):
    from agent.reasoning.agent_engine import AgentOrchestrator

    sid = req.session_id or session_memory.create_session(req.patient_id)
    orch = AgentOrchestrator(db)
    result = await orch.handle_text(
        text=req.text,
        session_id=sid,
        patient_id=req.patient_id or 1,
        language=req.language
    )

    out = {
        "session_id": sid,
        "user_text":  result["user_text"],
        "agent_text": result["agent_text"],
        "language":   result["language"],
        "latency":    result["latency"]
    }

    if req.return_audio and result.get("audio"):
        out["audio_base64"] = base64.b64encode(result["audio"]).decode()
        out["audio_format"] = result.get("audio_format", "mp3")

    return out


# ──────────────────────────────────────────────────────────
# Voice upload
# ──────────────────────────────────────────────────────────

@app.post("/api/chat/voice")
async def voice_chat(
    audio:      UploadFile = File(...),
    session_id: str        = Form(default=None),
    patient_id: int        = Form(default=1),
    db: Session = Depends(get_db)
):
    """Upload an audio file, receive text response + audio response."""
    from agent.reasoning.agent_engine import AgentOrchestrator

    audio_bytes = await audio.read()
    sid         = session_id or session_memory.create_session(patient_id)
    orch        = AgentOrchestrator(db)

    # detect format from filename
    fname = audio.filename or "audio.webm"
    fmt   = fname.rsplit(".", 1)[-1] if "." in fname else "webm"

    result = await orch.handle_voice(
        audio_bytes=audio_bytes,
        session_id=sid,
        patient_id=patient_id,
        audio_fmt=fmt
    )

    if "error" in result:
        raise HTTPException(status_code=422, detail=result["error"])

    return {
        "session_id":   sid,
        "user_text":    result["user_text"],
        "agent_text":   result["agent_text"],
        "language":     result["language"],
        "audio_base64": base64.b64encode(result["audio"]).decode() if result.get("audio") else None,
        "audio_format": result.get("audio_format", "mp3"),
        "latency":      result["latency"]
    }


# ──────────────────────────────────────────────────────────
# TTS endpoint — just convert any text to audio
# ──────────────────────────────────────────────────────────

@app.get("/api/tts")
async def tts_endpoint(text: str, language: str = "en"):
    from services.text_to_speech.tts_service import tts_service
    result = await tts_service.synthesize(text, language=language)
    return Response(content=result.get("audio", b""), media_type="audio/mpeg")


# ──────────────────────────────────────────────────────────
# Appointment data endpoints
# ──────────────────────────────────────────────────────────

@app.get("/api/appointments/{patient_id}")
def get_appointments(patient_id: int, db: Session = Depends(get_db)):
    from scheduler.appointment_engine.appointment_service import AppointmentService
    svc = AppointmentService(db)
    return svc.get_patient_appointments(patient_id)


@app.get("/api/availability")
def check_availability(specialization: str, date: str, db: Session = Depends(get_db)):
    from scheduler.appointment_engine.appointment_service import AppointmentService
    from datetime import datetime
    svc = AppointmentService(db)
    try:
        parsed = datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(400, "Date must be YYYY-MM-DD format")
    return svc.check_availability(specialization, parsed)


@app.get("/api/doctors")
def list_doctors(specialization: str = None, db: Session = Depends(get_db)):
    from scheduler.appointment_engine.appointment_service import AppointmentService
    svc     = AppointmentService(db)
    doctors = svc.list_doctors(specialization)
    return [
        {"id": d.id, "name": d.name, "specialization": d.specialization, "hospital": d.hospital}
        for d in doctors
    ]


# ──────────────────────────────────────────────────────────
# Outbound campaign
# ──────────────────────────────────────────────────────────

class CampaignRequest(BaseModel):
    patient_id:     int
    campaign_type:  str            # "reminder", "follow_up", "vaccination"
    appointment_id: Optional[int] = None
    message:        Optional[str] = None


@app.post("/api/campaigns/trigger")
async def trigger_campaign(req: CampaignRequest, db: Session = Depends(get_db)):
    """
    Simulates an outbound campaign call to a patient.
    In production you'd dial out via Twilio/SIP here.
    """
    from agent.reasoning.agent_engine import AgentOrchestrator

    sid = session_memory.create_session(req.patient_id)
    lang = persistent_memory.get_language_preference(req.patient_id)

    default_msgs = {
        "reminder":    "Hello! This is a reminder about your upcoming appointment. Would you like to confirm, reschedule or cancel?",
        "follow_up":   "Hello! Following up on your recent visit — how are you feeling? Do you need to book a follow-up?",
        "vaccination": "Hello! It's time for your scheduled vaccination. Would you like to book an appointment?"
    }
    msg = req.message or default_msgs.get(req.campaign_type, default_msgs["reminder"])

    orch   = AgentOrchestrator(db)
    result = await orch.handle_text(
        text=msg, session_id=sid, patient_id=req.patient_id, language=lang
    )

    return {
        "session_id":       sid,
        "campaign_type":    req.campaign_type,
        "outbound_message": msg,
        "agent_response":   result["agent_text"],
        "language":         lang
    }


# ──────────────────────────────────────────────────────────
# WebSocket — real-time voice pipeline
# ──────────────────────────────────────────────────────────

@app.websocket("/ws/voice/{session_id}")
async def ws_voice(websocket: WebSocket, session_id: str, patient_id: int = 1):
    """
    Real-time bidirectional voice stream.

    Client sends:
        {"type": "audio", "data": "<base64>", "format": "webm"}
        {"type": "text",  "data": "hello"}
        {"type": "ping"}

    Server sends:
        {"type": "connected", ...}
        {"type": "response", "user_text": "...", "text": "...", "audio": "<base64>", "latency": {...}}
        {"type": "error", "message": "..."}
        {"type": "pong"}
    """
    await websocket.accept()
    print(f"WS connected — session={session_id}, patient={patient_id}")

    # make sure the session record exists
    sess = session_memory.get_session(session_id)
    if not sess.get("patient_id"):
        session_memory.save_session(session_id, {**sess, "patient_id": patient_id})

    # WebSocket can't use Depends(), so we create a db session manually
    from backend.database import SessionLocal
    db = SessionLocal()

    try:
        from agent.reasoning.agent_engine import AgentOrchestrator
        orch = AgentOrchestrator(db)

        await websocket.send_json({
            "type":       "connected",
            "session_id": session_id,
            "message":    "Voice AI ready. Speak or send text."
        })

        while True:
            try:
                msg  = await websocket.receive_json()
                kind = msg.get("type", "text")

                if kind == "ping":
                    await websocket.send_json({"type": "pong"})
                    continue

                if kind == "audio":
                    raw = msg.get("data", "")
                    fmt = msg.get("format", "webm")
                    try:
                        audio_bytes = base64.b64decode(raw)
                    except Exception:
                        await websocket.send_json({"type": "error", "message": "Bad audio encoding"})
                        continue

                    result = await orch.handle_voice(
                        audio_bytes=audio_bytes,
                        session_id=session_id,
                        patient_id=patient_id,
                        audio_fmt=fmt
                    )

                elif kind == "text":
                    text = msg.get("data", "").strip()
                    if not text:
                        continue
                    result = await orch.handle_text(
                        text=text,
                        session_id=session_id,
                        patient_id=patient_id,
                        language=msg.get("language")
                    )

                else:
                    continue

                if "error" in result:
                    await websocket.send_json({"type": "error", "message": result["error"]})
                    continue

                audio_b64 = (
                    base64.b64encode(result["audio"]).decode()
                    if result.get("audio") else None
                )

                await websocket.send_json({
                    "type":         "response",
                    "user_text":    result.get("user_text", ""),
                    "text":         result["agent_text"],
                    "language":     result["language"],
                    "audio":        audio_b64,
                    "audio_format": result.get("audio_format", "mp3"),
                    "latency":      result["latency"]
                })

            except WebSocketDisconnect:
                break
            except Exception as err:
                print(f"WS error: {err}")
                try:
                    await websocket.send_json({"type": "error", "message": str(err)})
                except Exception:
                    break

    finally:
        db.close()
        print(f"WS disconnected — session={session_id}")


# ──────────────────────────────────────────────────────────
# Demo UI
# ──────────────────────────────────────────────────────────

@app.get("/demo", response_class=HTMLResponse)
async def demo_page():
    html_path = os.path.join(os.path.dirname(__file__), "frontend", "index.html")
    with open(html_path) as f:
        return f.read()


# ──────────────────────────────────────────────────────────
# Run directly with: python main.py
# ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug
    )

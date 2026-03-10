# agent/reasoning/agent_engine.py
#
# The brain of the system. Two classes here:
#
# AppointmentAgent — takes text input, runs the LLM with tool calling,
#   executes any tool calls, returns a text response. Measures each stage.
#
# AgentOrchestrator — the full pipeline: audio in → STT → lang detect →
#   agent → TTS → audio out. This is what the WebSocket and REST endpoints call.
#
# On latency:
#   The 450ms target is tight. Main wins:
#   - gpt-4o is faster than gpt-4-turbo for short prompts
#   - We only pass the last 8 conversation turns, not the full history
#   - TTS-1 is used (not TTS-1-HD)
#   - Language detection is done locally (no API call)

import json
import time
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Tuple
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from config import settings
from agent.prompt.system_prompts import get_system_prompt
from agent.tools.tool_definitions import APPOINTMENT_TOOLS


class LatencyTracker:
    """
    Simple per-request stopwatch. Call .start("stage") and .end("stage")
    around each part of the pipeline. .report() gives you a dict you can
    log and return to the client.
    """

    def __init__(self):
        self._stages:     Dict[str, int]   = {}
        self._checkpoints: Dict[str, float] = {}
        self._start = time.monotonic()

    def start(self, stage: str):
        self._checkpoints[stage] = time.monotonic()

    def end(self, stage: str):
        if stage in self._checkpoints:
            self._stages[stage] = int((time.monotonic() - self._checkpoints[stage]) * 1000)

    def total_ms(self) -> int:
        return int((time.monotonic() - self._start) * 1000)

    def report(self) -> Dict[str, Any]:
        total = self.total_ms()
        status = "✅ within target" if total < settings.latency_target_ms else "⚠️ exceeded target"
        return {
            **self._stages,
            "total_ms":   total,
            "target_ms":  settings.latency_target_ms,
            "status":     status
        }


class AppointmentAgent:
    """
    Handles one turn of the conversation:
      1. Build messages array (system prompt + history + current input)
      2. Call LLM
      3. If LLM wants to use a tool → execute it → call LLM again
      4. Return the final text response
    """

    def __init__(self, db_session):
        self.db     = db_session
        self._llm   = None    # lazy init
        self._svc   = None    # appointment service, lazy too

    def _get_llm(self):
        if self._llm is None:
            import openai
            self._llm = openai.OpenAI(api_key=settings.openai_api_key)
        return self._llm

    def _get_service(self):
        if self._svc is None:
            from scheduler.appointment_engine.appointment_service import AppointmentService
            self._svc = AppointmentService(self.db)
        return self._svc

    def _parse_date(self, date_str: str) -> datetime:
        """
        Convert flexible date strings to actual datetimes.
        The LLM often returns "tomorrow" or "next_monday" instead of a date,
        so we handle those here.
        """
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

        if not date_str:
            return today + timedelta(days=1)   # default to tomorrow if unclear

        s = date_str.lower().strip()

        if s == "tomorrow":
            return today + timedelta(days=1)
        if s in ("day_after_tomorrow", "day after tomorrow"):
            return today + timedelta(days=2)
        if s.startswith("next"):
            # "next_monday", "next monday" etc.
            day_name = s.replace("next_", "").replace("next ", "").strip()
            weekdays = {
                "monday":0, "tuesday":1, "wednesday":2, "thursday":3,
                "friday":4, "saturday":5, "sunday":6
            }
            if day_name in weekdays:
                target = weekdays[day_name]
                days_ahead = (target - today.weekday() + 7) % 7 or 7
                return today + timedelta(days=days_ahead)

        try:
            return datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            # give up and use tomorrow
            return today + timedelta(days=1)

    def _run_tool(self, tool_name: str, args: Dict[str, Any],
                  patient_id: int, tracker: LatencyTracker) -> str:
        """Execute a tool call and return the result as a JSON string."""
        tracker.start("tool")
        svc = self._get_service()

        try:
            if tool_name == "check_availability":
                date   = self._parse_date(args.get("date", "tomorrow"))
                result = svc.check_availability(args["specialization"], date)

            elif tool_name == "book_appointment":
                date   = self._parse_date(args["date"])
                result = svc.book_appointment(
                    patient_id=patient_id,
                    doctor_id=int(args["doctor_id"]),
                    date=date,
                    time_slot=args["time_slot"],
                    notes=args.get("notes")
                )

            elif tool_name == "cancel_appointment":
                result = svc.cancel_appointment(
                    appointment_id=int(args["appointment_id"]),
                    patient_id=patient_id
                )

            elif tool_name == "reschedule_appointment":
                new_date = self._parse_date(args["new_date"])
                result   = svc.reschedule_appointment(
                    appointment_id=int(args["appointment_id"]),
                    patient_id=patient_id,
                    new_date=new_date,
                    new_time_slot=args["new_time_slot"]
                )

            elif tool_name == "list_patient_appointments":
                result = {"appointments": svc.get_patient_appointments(patient_id)}

            else:
                result = {"error": f"Unknown tool: {tool_name}"}

        except Exception as e:
            result = {"error": str(e)}

        tracker.end("tool")
        return json.dumps(result)

    def process(self, user_text: str, session_id: str, patient_id: int,
                language: str = "en",
                history: List[Dict] = None) -> Tuple[str, LatencyTracker]:
        """
        Main entry point. Returns (response_text, latency_tracker).
        """
        tracker = LatencyTracker()
        history = history or []

        today = datetime.now().strftime("%A, %Y-%m-%d")
        system_msg = get_system_prompt(
            language,
            today=today,
            session_info=f"Session: {session_id[:8]}... | Patient ID: {patient_id}"
        )

        # build message list — keep last 8 turns to limit token usage
        messages = [{"role": "system", "content": system_msg}]
        for turn in history[-8:]:
            messages.append({"role": turn["role"], "content": turn["content"]})
        messages.append({"role": "user", "content": user_text})

        # first LLM call
        tracker.start("llm_1")
        try:
            llm = self._get_llm()
            resp = llm.chat.completions.create(
                model=settings.llm_model,
                messages=messages,
                tools=APPOINTMENT_TOOLS,
                tool_choice="auto",
                temperature=0.3,     # lower temp = more consistent responses
                max_tokens=500
            )
        except Exception as err:
            tracker.end("llm_1")
            fallback = "I'm having a bit of trouble right now. Could you repeat that?"
            print(f"[Agent] LLM call failed: {err}")
            return fallback, tracker

        tracker.end("llm_1")
        agent_msg = resp.choices[0].message

        # handle tool calls if the model decided to use one
        if agent_msg.tool_calls:
            messages.append(agent_msg)   # include the assistant's tool_use message

            for tc in agent_msg.tool_calls:
                fn_name = tc.function.name
                try:
                    fn_args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    fn_args = {}

                tool_result = self._run_tool(fn_name, fn_args, patient_id, tracker)

                messages.append({
                    "role":         "tool",
                    "tool_call_id": tc.id,
                    "content":      tool_result
                })

            # second LLM call — model now has the tool results and writes a response
            tracker.start("llm_2")
            try:
                resp2   = llm.chat.completions.create(
                    model=settings.llm_model,
                    messages=messages,
                    temperature=0.3,
                    max_tokens=400
                )
                final = resp2.choices[0].message.content
            except Exception as err:
                final = "I've processed your request. Is there anything else I can help with?"
                print(f"[Agent] Second LLM call failed: {err}")
            tracker.end("llm_2")

        else:
            # no tool needed — direct conversational response
            final = agent_msg.content or "How can I help you today?"

        report = tracker.report()
        print(f"[Latency] {json.dumps(report)}")

        return final, tracker


class AgentOrchestrator:
    """
    High-level pipeline. Wires together:
    STT → language detection → agent → TTS → return everything.

    Both voice (audio bytes) and text inputs are supported.
    """

    def __init__(self, db_session):
        self.db = db_session

    async def handle_voice(self, audio_bytes: bytes, session_id: str,
                           patient_id: int = 1, audio_fmt: str = "webm") -> Dict[str, Any]:
        """Full pipeline: audio bytes in → audio bytes out."""
        from services.speech_to_text.stt_service import stt_service
        from services.language_detection.detector import detect_language
        from services.text_to_speech.tts_service import tts_service
        from memory.session_memory.session_manager import session_memory
        from memory.persistent_memory.persistent_memory import persistent_memory

        pipeline_start = time.monotonic()
        stages = {}

        # 1. Speech → Text
        t = time.monotonic()
        stt_result = await stt_service.transcribe(audio_bytes, audio_fmt=audio_fmt)
        stages["stt_ms"] = int((time.monotonic() - t) * 1000)

        text = stt_result.get("text", "").strip()
        if not text:
            return {"error": "Could not transcribe audio — please speak more clearly"}

        # 2. Language detection
        t = time.monotonic()
        sess = session_memory.get_session(session_id)
        lang, confidence = detect_language(text)

        # if we're not confident and the patient has a known preference, use that
        if confidence < 0.7 and patient_id:
            lang = persistent_memory.get_language_preference(patient_id)

        session_memory.update_language(session_id, lang)
        stages["lang_detect_ms"] = int((time.monotonic() - t) * 1000)

        # 3. Agent reasoning
        t = time.monotonic()
        hist  = session_memory.get_conversation_history(session_id)
        agent = AppointmentAgent(self.db)
        reply, agent_tracker = agent.process(text, session_id, patient_id, lang, hist)
        stages["agent_ms"] = int((time.monotonic() - t) * 1000)

        # save to memory
        session_memory.add_message(session_id, "user",      text,  lang)
        session_memory.add_message(session_id, "assistant", reply, lang)

        if patient_id:
            persistent_memory.update_language_preference(patient_id, lang)
            persistent_memory.increment_interactions(patient_id)

        # 4. Text → Speech
        t = time.monotonic()
        tts_result = await tts_service.synthesize(reply, language=lang)
        stages["tts_ms"] = int((time.monotonic() - t) * 1000)

        total = int((time.monotonic() - pipeline_start) * 1000)
        stages.update({
            "total_ms":      total,
            "target_ms":     settings.latency_target_ms,
            "within_target": total < settings.latency_target_ms
        })

        print(f"[Pipeline] {json.dumps(stages)}")

        return {
            "user_text":    text,
            "agent_text":   reply,
            "language":     lang,
            "audio":        tts_result.get("audio", b""),
            "audio_format": tts_result.get("format", "mp3"),
            "latency":      stages
        }

    async def handle_text(self, text: str, session_id: str, patient_id: int = 1,
                          language: str = None) -> Dict[str, Any]:
        """Text-only pipeline — for testing and the REST API."""
        from services.language_detection.detector import detect_language
        from services.text_to_speech.tts_service import tts_service
        from memory.session_memory.session_manager import session_memory
        from memory.persistent_memory.persistent_memory import persistent_memory

        t_start = time.monotonic()

        if not language:
            lang, _ = detect_language(text)
        else:
            lang = language

        session_memory.update_language(session_id, lang)
        hist  = session_memory.get_conversation_history(session_id)

        agent = AppointmentAgent(self.db)
        reply, tracker = agent.process(text, session_id, patient_id, lang, hist)

        session_memory.add_message(session_id, "user",      text,  lang)
        session_memory.add_message(session_id, "assistant", reply, lang)

        if patient_id:
            persistent_memory.update_language_preference(patient_id, lang)

        tts_result = await tts_service.synthesize(reply, language=lang)
        total = int((time.monotonic() - t_start) * 1000)

        return {
            "user_text":    text,
            "agent_text":   reply,
            "language":     lang,
            "audio":        tts_result.get("audio", b""),
            "audio_format": "mp3",
            "latency":      {**tracker.report(), "total_pipeline_ms": total}
        }

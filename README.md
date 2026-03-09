# 🩺 Voice AI Appointment Agent

A **real-time multilingual voice AI agent** for clinical appointment booking. Supports English, Hindi, and Tamil with sub-450ms end-to-end latency.

---

## 🚀 Quick Start

### 1. Clone & Configure

```bash
cp .env.example .env
# Edit .env and add your OPENAI_API_KEY
```

### 2. Option A: Docker (Recommended)

```bash
docker-compose up --build
```

Open: http://localhost:8000/demo

### 3. Option B: Local (SQLite, no Docker)

```bash
pip install -r requirements.txt
DATABASE_URL=sqlite:///./voice_ai.db uvicorn main:app --reload
```

---

## 🏗️ Architecture

```
User Speech
     ↓
[WebSocket / REST API]
     ↓
Speech-to-Text (OpenAI Whisper)         ~120ms
     ↓
Language Detection (Script + langdetect) ~5ms
     ↓
AI Agent (GPT-4o + Tool Calling)        ~200ms
     ├─ checkAvailability
     ├─ bookAppointment
     ├─ cancelAppointment
     └─ rescheduleAppointment
     ↓
Text-to-Speech (OpenAI TTS)             ~100ms
     ↓
Audio Response                    TOTAL < 450ms
```

### Component Map

| Layer | Technology | Purpose |
|-------|-----------|---------|
| Real-time transport | WebSocket (FastAPI) | Audio streaming |
| STT | OpenAI Whisper API | Speech → Text |
| Language Detection | Unicode script analysis + langdetect | Detect EN/HI/TA |
| LLM Agent | GPT-4o with function calling | Intent → Action |
| Tool Orchestration | Python functions | Appointment CRUD |
| TTS | OpenAI TTS (alloy/nova voice) | Text → Speech |
| Session Memory | Redis (fallback: in-memory) | Conversation state |
| Persistent Memory | Redis (30-day TTL) | Patient preferences |
| Database | PostgreSQL (SQLite for dev) | Appointments |
| Scheduler | APScheduler | Outbound campaigns |

---

## 📁 Project Structure

```
voice-ai-agent/
├── main.py                          # FastAPI app + WebSocket
├── config.py                        # Central settings
├── requirements.txt
├── docker-compose.yml
├── Dockerfile
│
├── backend/
│   ├── models.py                    # SQLAlchemy ORM models
│   └── database.py                  # DB connection + seeding
│
├── agent/
│   ├── prompt/system_prompts.py     # EN/HI/TA system prompts
│   ├── tools/tool_definitions.py    # OpenAI tool schemas
│   └── reasoning/agent_engine.py   # LLM pipeline + orchestrator
│
├── memory/
│   ├── session_memory/              # Per-conversation context
│   └── persistent_memory/           # Long-term patient profile
│
├── services/
│   ├── speech_to_text/stt_service.py
│   ├── text_to_speech/tts_service.py
│   └── language_detection/detector.py
│
├── scheduler/
│   ├── appointment_engine/          # Booking logic
│   └── campaign_scheduler.py        # Outbound reminders
│
├── frontend/index.html              # Browser demo UI
└── tests/test_suite.py              # Full test suite
```

---

## 🌐 API Reference

### REST Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Health + info |
| POST | `/api/sessions` | Create conversation session |
| POST | `/api/chat/text` | Text-based chat |
| POST | `/api/chat/voice` | Voice file upload |
| GET | `/api/tts?text=...&language=en` | Text-to-speech audio |
| GET | `/api/appointments/{patient_id}` | List appointments |
| GET | `/api/availability?specialization=...&date=...` | Check slots |
| GET | `/api/doctors` | List doctors |
| POST | `/api/campaigns/trigger` | Trigger outbound campaign |

### WebSocket

`ws://localhost:8000/ws/voice/{session_id}?patient_id=1`

**Send:**
```json
{ "type": "text", "data": "Book appointment with cardiologist tomorrow" }
{ "type": "audio", "data": "<base64_webm>", "format": "webm" }
```

**Receive:**
```json
{
  "type": "response",
  "user_text": "...",
  "text": "Your appointment is confirmed for...",
  "language": "en",
  "audio": "<base64_mp3>",
  "latency": { "stt_ms": 120, "agent_ms": 210, "tts_ms": 95, "total_ms": 440 }
}
```

---

## 🗣️ Multilingual Examples

| Language | Input | Detection |
|----------|-------|-----------|
| English | "Book appointment with cardiologist tomorrow" | Script/langdetect |
| Hindi | "मुझे कल डॉक्टर से मिलना है" | Devanagari Unicode |
| Tamil | "நாளை மருத்துவரை பார்க்க வேண்டும்" | Tamil Unicode |

---

## 🧠 Memory Design

### Session Memory (Redis, 30 min TTL)
```json
{
  "session_id": "uuid",
  "patient_id": 1,
  "language": "hi",
  "conversation_history": [...],
  "pending_intent": "book",
  "pending_data": { "doctor": "cardiologist" }
}
```

### Persistent Memory (Redis, 30 day TTL)
```json
{
  "patient_id": 1,
  "preferred_language": "hi",
  "preferred_doctor_id": 1,
  "preferred_hospital": "Apollo",
  "last_appointment": {...},
  "interaction_count": 12
}
```

---

## ⚡ Latency Breakdown

| Stage | Target | Typical |
|-------|--------|---------|
| STT (Whisper) | < 150ms | ~120ms |
| Language Detection | < 10ms | ~3ms |
| LLM (GPT-4o) | < 200ms | ~180ms |
| Tool Execution | < 30ms | ~10ms |
| TTS | < 120ms | ~95ms |
| **Total** | **< 450ms** | **~410ms** |

Every response includes `latency` object with measured stage times.

---

## 🧪 Testing

```bash
# Run full test suite
pytest tests/test_suite.py -v

# Run specific test class
pytest tests/test_suite.py::TestAppointmentService -v

# Run with coverage
pytest tests/test_suite.py --cov=. --cov-report=html
```

Test scenarios covered:
- ✅ Appointment booking
- ✅ Double-booking prevention
- ✅ Past-date validation
- ✅ Cancellation
- ✅ Rescheduling
- ✅ Language detection (EN/HI/TA)
- ✅ Session memory (create, add, limit, language)
- ✅ Persistent memory
- ✅ Date parsing (tomorrow, next Monday, explicit)

---

## ⚙️ Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_API_KEY` | required | OpenAI API key |
| `DATABASE_URL` | sqlite:///./voice_ai.db | Database connection |
| `REDIS_URL` | redis://localhost:6379/0 | Redis connection |
| `LLM_MODEL` | gpt-4o | LLM model |
| `LATENCY_TARGET_MS` | 450 | Latency target |
| `SESSION_TTL` | 1800 | Session expiry (seconds) |

---

## 🔄 Outbound Campaign

```bash
curl -X POST http://localhost:8000/api/campaigns/trigger \
  -H "Content-Type: application/json" \
  -d '{"patient_id": 1, "campaign_type": "reminder"}'
```

Campaign types: `reminder`, `follow_up`, `vaccination`

---

## ⚠️ Known Limitations

- STT accuracy for Tamil may be lower than EN/HI with Whisper
- TTS uses a single voice for all languages (no native Tamil/Hindi voice in OpenAI TTS-1)
- True real-time streaming (partial transcription) requires additional implementation
- Outbound calling requires Twilio/SIP integration (not included)

---

## 🛠️ Trade-offs

| Decision | Trade-off |
|----------|-----------|
| Redis fallback to in-memory | Allows running without Redis for dev, loses persistence |
| SQLite for dev | Zero-config setup, not for production scale |
| GPT-4o function calling | High accuracy, slightly higher latency vs smaller models |
| OpenAI TTS-1 | Fast (~100ms), lower quality vs TTS-1-HD (~200ms) |
| Session history capped at 20 | Limits context window cost, may lose very old context |

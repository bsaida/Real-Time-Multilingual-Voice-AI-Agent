# tests/test_suite.py
#
# Run all tests with:  pytest tests/test_suite.py -v
#
# These tests use SQLite in-memory so you don't need Postgres or Redis.
# We mock out OpenAI calls — testing the logic, not the API.

import pytest
import sys
import os
from datetime import datetime, timedelta

sys.path.append(os.path.dirname(os.path.dirname(__file__)))


# ──────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────

@pytest.fixture
def db():
    """In-memory SQLite with demo data seeded."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from backend.models import Base, Doctor, DoctorSchedule, Patient

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Sess = sessionmaker(bind=engine)
    session = Sess()

    # seed 3 doctors
    docs = [
        Doctor(id=1, name="Dr. Rajesh Sharma",  specialization="cardiologist",     hospital="Apollo"),
        Doctor(id=2, name="Dr. Priya Nair",      specialization="dermatologist",    hospital="Fortis"),
        Doctor(id=3, name="Dr. Anand Kumar",     specialization="general physician",hospital="Apollo"),
    ]
    session.add_all(docs)
    session.flush()

    # schedule for tomorrow
    tomorrow = (datetime.now() + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    for doc in docs:
        session.add(DoctorSchedule(
            doctor_id=doc.id,
            date=tomorrow,
            available_slots=["09:00", "10:00", "14:00", "15:00"],
            booked_slots=[]
        ))

    session.add(Patient(id=1, name="Test Patient", phone="+911111111111"))
    session.commit()

    yield session
    session.close()


@pytest.fixture
def svc(db):
    from scheduler.appointment_engine.appointment_service import AppointmentService
    return AppointmentService(db)


# ──────────────────────────────────────────
# Appointment logic
# ──────────────────────────────────────────

class TestBooking:

    def test_availability_found(self, svc):
        tomorrow = datetime.now() + timedelta(days=1)
        result = svc.check_availability("cardiologist", tomorrow)
        assert result["found"] is True
        assert result["results"][0]["doctor_name"] == "Dr. Rajesh Sharma"

    def test_availability_unknown_specialization(self, svc):
        tomorrow = datetime.now() + timedelta(days=1)
        result = svc.check_availability("neurologist", tomorrow)
        assert result["found"] is False   # we didn't seed any neurologists

    def test_book_success(self, svc):
        tomorrow = datetime.now() + timedelta(days=1)
        r = svc.book_appointment(patient_id=1, doctor_id=1,
                                  date=tomorrow, time_slot="09:00")
        assert r["success"] is True
        assert r["time_slot"] == "09:00"
        assert r["doctor_name"] == "Dr. Rajesh Sharma"

    def test_cant_book_past(self, svc):
        yesterday = datetime.now() - timedelta(days=1)
        r = svc.book_appointment(1, 1, yesterday, "09:00")
        assert r["success"] is False
        assert "past" in r["error"].lower()

    def test_double_book_blocked(self, svc):
        tomorrow = datetime.now() + timedelta(days=1)
        r1 = svc.book_appointment(1, 1, tomorrow, "10:00")
        assert r1["success"] is True

        r2 = svc.book_appointment(1, 2, tomorrow, "10:00")   # different patient, same slot
        assert r2["success"] is False
        assert "alternatives" in r2   # should suggest other slots

    def test_cancel(self, svc):
        tomorrow = datetime.now() + timedelta(days=1)
        book   = svc.book_appointment(1, 1, tomorrow, "14:00")
        assert book["success"]
        cancel = svc.cancel_appointment(book["appointment_id"], 1)
        assert cancel["success"]

    def test_cancel_frees_slot(self, svc):
        """After cancelling, the slot should be bookable again."""
        tomorrow = datetime.now() + timedelta(days=1)
        book   = svc.book_appointment(1, 1, tomorrow, "15:00")
        svc.cancel_appointment(book["appointment_id"], 1)
        rebook = svc.book_appointment(1, 1, tomorrow, "15:00")
        assert rebook["success"]

    def test_reschedule(self, svc, db):
        from backend.models import DoctorSchedule
        tomorrow  = datetime.now() + timedelta(days=1)
        day_after = datetime.now() + timedelta(days=2)

        # add schedule for day_after
        db.add(DoctorSchedule(
            doctor_id=1,
            date=day_after.replace(hour=0, minute=0, second=0, microsecond=0),
            available_slots=["09:00", "10:00"],
            booked_slots=[]
        ))
        db.commit()

        book = svc.book_appointment(1, 1, tomorrow, "09:00")
        assert book["success"]

        resch = svc.reschedule_appointment(book["appointment_id"], 1, day_after, "10:00")
        assert resch["success"]
        assert resch["rescheduled"] is True

    def test_list_appointments(self, svc):
        tomorrow = datetime.now() + timedelta(days=1)
        svc.book_appointment(1, 2, tomorrow, "09:00")
        appts = svc.get_patient_appointments(1)
        assert len(appts) >= 1
        assert "doctor_name" in appts[0]


# ──────────────────────────────────────────
# Language detection
# ──────────────────────────────────────────

class TestLanguageDetection:

    def test_english(self):
        from services.language_detection.detector import detect_language
        lang, conf = detect_language("Book appointment with cardiologist tomorrow")
        assert lang == "en"

    def test_hindi(self):
        from services.language_detection.detector import detect_language
        lang, conf = detect_language("मुझे डॉक्टर से मिलना है")
        assert lang == "hi"
        assert conf > 0.9

    def test_tamil(self):
        from services.language_detection.detector import detect_language
        lang, conf = detect_language("நாளை மருத்துவரை பார்க்க வேண்டும்")
        assert lang == "ta"
        assert conf > 0.9

    def test_empty_string(self):
        from services.language_detection.detector import detect_language
        lang, _ = detect_language("")
        assert lang == "en"

    def test_tts_codes(self):
        from services.language_detection.detector import get_tts_language_code
        assert get_tts_language_code("en") == "en-US"
        assert get_tts_language_code("hi") == "hi-IN"
        assert get_tts_language_code("ta") == "ta-IN"


# ──────────────────────────────────────────
# Session memory
# ──────────────────────────────────────────

class TestSessionMemory:

    def test_create_and_retrieve(self):
        from memory.session_memory.session_manager import SessionMemory
        mem = SessionMemory()
        sid = mem.create_session(patient_id=42, language="hi")
        sess = mem.get_session(sid)
        assert sess["patient_id"] == 42
        assert sess["language"] == "hi"

    def test_add_messages(self):
        from memory.session_memory.session_manager import SessionMemory
        mem = SessionMemory()
        sid = mem.create_session()
        mem.add_message(sid, "user",      "I want to book",     "en")
        mem.add_message(sid, "assistant", "Which doctor?",       "en")
        history = mem.get_conversation_history(sid)
        assert len(history) == 2
        assert history[0]["role"] == "user"

    def test_history_capped_at_20(self):
        from memory.session_memory.session_manager import SessionMemory
        mem = SessionMemory()
        sid = mem.create_session()
        for i in range(30):
            mem.add_message(sid, "user", f"message {i}", "en")
        assert len(mem.get_conversation_history(sid)) == 20

    def test_pending_intent(self):
        from memory.session_memory.session_manager import SessionMemory
        mem = SessionMemory()
        sid = mem.create_session()
        mem.set_pending_intent(sid, "book", {"doctor": "cardiologist"})
        sess = mem.get_session(sid)
        assert sess["pending_intent"] == "book"
        assert sess["pending_data"]["doctor"] == "cardiologist"

        mem.clear_pending_intent(sid)
        assert mem.get_session(sid)["pending_intent"] is None

    def test_language_update(self):
        from memory.session_memory.session_manager import SessionMemory
        mem = SessionMemory()
        sid = mem.create_session(language="en")
        mem.update_language(sid, "ta")
        assert mem.get_session(sid)["language"] == "ta"


# ──────────────────────────────────────────
# Persistent memory
# ──────────────────────────────────────────

class TestPersistentMemory:

    def test_default_profile(self):
        from memory.persistent_memory.persistent_memory import PersistentMemory
        mem     = PersistentMemory()
        profile = mem.get_profile(9001)
        assert profile["preferred_language"] == "en"
        assert profile["interaction_count"]  == 0

    def test_language_update(self):
        from memory.persistent_memory.persistent_memory import PersistentMemory
        mem = PersistentMemory()
        mem.update_language_preference(9002, "hi")
        assert mem.get_language_preference(9002) == "hi"

    def test_record_appointment(self):
        from memory.persistent_memory.persistent_memory import PersistentMemory
        mem = PersistentMemory()
        mem.record_appointment(9003, {"doctor": "Dr. Sharma", "date": "2025-12-01"})
        p = mem.get_profile(9003)
        assert p["last_appointment"]["doctor"] == "Dr. Sharma"

    def test_interaction_counter(self):
        from memory.persistent_memory.persistent_memory import PersistentMemory
        mem = PersistentMemory()
        mem.increment_interactions(9004)
        mem.increment_interactions(9004)
        assert mem.get_profile(9004)["interaction_count"] == 2


# ──────────────────────────────────────────
# Date parsing
# ──────────────────────────────────────────

class TestDateParsing:

    def test_tomorrow(self, db):
        from agent.reasoning.agent_engine import AppointmentAgent
        agent = AppointmentAgent(db)
        result = agent._parse_date("tomorrow")
        assert result.date() == (datetime.now() + timedelta(days=1)).date()

    def test_day_after_tomorrow(self, db):
        from agent.reasoning.agent_engine import AppointmentAgent
        agent = AppointmentAgent(db)
        result = agent._parse_date("day_after_tomorrow")
        assert result.date() == (datetime.now() + timedelta(days=2)).date()

    def test_explicit_date(self, db):
        from agent.reasoning.agent_engine import AppointmentAgent
        agent = AppointmentAgent(db)
        result = agent._parse_date("2026-09-15")
        assert result.year == 2026 and result.month == 9 and result.day == 15

    def test_invalid_date_falls_back(self, db):
        from agent.reasoning.agent_engine import AppointmentAgent
        agent = AppointmentAgent(db)
        # should not crash — returns tomorrow
        result = agent._parse_date("gobbledygook")
        assert result.date() >= datetime.now().date()


# ──────────────────────────────────────────
# Latency tracker
# ──────────────────────────────────────────

class TestLatencyTracker:

    def test_measures_stages(self):
        import time
        from agent.reasoning.agent_engine import LatencyTracker
        t = LatencyTracker()
        t.start("stt")
        time.sleep(0.015)
        t.end("stt")
        assert t._stages["stt"] >= 15

    def test_report_has_required_keys(self):
        from agent.reasoning.agent_engine import LatencyTracker
        t      = LatencyTracker()
        report = t.report()
        assert "total_ms"  in report
        assert "target_ms" in report
        assert "status"    in report

    def test_within_target_flag(self):
        from agent.reasoning.agent_engine import LatencyTracker
        t = LatencyTracker()
        r = t.report()
        # a freshly created tracker should be well within 450ms
        assert "within target" in r["status"]


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

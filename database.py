# backend/database.py
# Sets up the database engine and session factory.
# Also handles first-run seeding so you get demo doctors and a test patient
# without having to run any SQL manually.

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool
from contextlib import contextmanager
from typing import Generator
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from config import settings
from backend.models import Base


def _build_engine():
    url = settings.database_url

    if url.startswith("postgresql"):
        # pool_pre_ping makes sure stale connections get dropped cleanly
        return create_engine(url, pool_pre_ping=True)
    else:
        # SQLite needs check_same_thread=False because FastAPI uses threads
        # StaticPool keeps the in-memory db alive across requests
        return create_engine(
            url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )


engine = _build_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db():
    """Create tables and load demo data on first run."""
    Base.metadata.create_all(bind=engine)
    _seed_demo_data()


def _seed_demo_data():
    """
    Inserts a handful of demo doctors + one demo patient so the app works
    immediately without any manual setup. Only runs if the doctors table is empty.
    """
    db = SessionLocal()
    try:
        from backend.models import Doctor, DoctorSchedule, Patient
        from datetime import datetime, timedelta

        # bail out if already seeded
        if db.query(Doctor).count() > 0:
            return

        print("Seeding demo doctors and patient...")

        docs = [
            Doctor(name="Dr. Rajesh Sharma",   specialization="cardiologist",     hospital="Apollo Hospital"),
            Doctor(name="Dr. Priya Nair",       specialization="dermatologist",    hospital="Fortis Hospital"),
            Doctor(name="Dr. Anand Kumar",      specialization="general physician",hospital="Apollo Hospital"),
            Doctor(name="Dr. Meena Krishnan",   specialization="orthopedic",       hospital="AIIMS"),
            Doctor(name="Dr. Suresh Patel",     specialization="neurologist",      hospital="Fortis Hospital"),
        ]
        db.add_all(docs)
        db.flush()   # get the auto-assigned IDs

        # give each doctor slots for the next 7 days
        slot_times = [
            "09:00", "09:30", "10:00", "10:30", "11:00",
            "14:00", "14:30", "15:00", "15:30", "16:00"
        ]
        for doc in docs:
            for offset in range(1, 8):
                day = (datetime.now() + timedelta(days=offset)).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                sched = DoctorSchedule(
                    doctor_id=doc.id,
                    date=day,
                    available_slots=slot_times,
                    booked_slots=[]
                )
                db.add(sched)

        # one demo patient — phone is the lookup key in real usage
        demo_patient = Patient(
            name="Rahul Mehta",
            phone="+919876543210",
            preferred_language="en",
            preferred_hospital="Apollo Hospital"
        )
        db.add(demo_patient)
        db.commit()
        print("✓ Demo data ready (5 doctors, 1 patient, 7 days of slots each)")

    except Exception as err:
        db.rollback()
        print(f"Seed skipped — probably already exists ({err})")
    finally:
        db.close()


@contextmanager
def get_db_context() -> Generator[Session, None, None]:
    """Use this in non-FastAPI code (scripts, tests, scheduler)."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency — inject with Depends(get_db)."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

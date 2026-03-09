# backend/models.py
# SQLAlchemy models for the appointment system.
# Nothing fancy — just the tables we need to book, cancel and reschedule appointments.
# I'm using Enum for appointment status so we can't accidentally store garbage strings.

from sqlalchemy import (
    Column, Integer, String, DateTime, ForeignKey,
    Text, Boolean, Enum, JSON
)
from sqlalchemy.orm import relationship, declarative_base
from datetime import datetime
import enum

Base = declarative_base()


class AppointmentStatus(str, enum.Enum):
    SCHEDULED   = "scheduled"
    CANCELLED   = "cancelled"
    COMPLETED   = "completed"
    RESCHEDULED = "rescheduled"


class Patient(Base):
    __tablename__ = "patients"

    id                  = Column(Integer, primary_key=True, index=True)
    name                = Column(String(100), nullable=False)
    phone               = Column(String(20), unique=True, index=True)
    email               = Column(String(100), nullable=True)

    # stored so we can reply in their language without asking every time
    preferred_language  = Column(String(10), default="en")

    preferred_doctor_id = Column(Integer, nullable=True)
    preferred_hospital  = Column(String(100), nullable=True)
    created_at          = Column(DateTime, default=datetime.utcnow)

    appointments = relationship("Appointment", back_populates="patient")


class Doctor(Base):
    __tablename__ = "doctors"

    id              = Column(Integer, primary_key=True, index=True)
    name            = Column(String(100), nullable=False)
    specialization  = Column(String(100), nullable=False)
    hospital        = Column(String(100), nullable=False)
    phone           = Column(String(20), nullable=True)
    is_active       = Column(Boolean, default=True)
    created_at      = Column(DateTime, default=datetime.utcnow)

    schedules    = relationship("DoctorSchedule", back_populates="doctor")
    appointments = relationship("Appointment", back_populates="doctor")


class DoctorSchedule(Base):
    """
    One row per doctor per day.
    available_slots is a JSON list like ["09:00", "09:30", "10:00", ...]
    booked_slots grows as appointments are made and shrinks when they're cancelled.
    """
    __tablename__ = "doctor_schedule"

    id              = Column(Integer, primary_key=True, index=True)
    doctor_id       = Column(Integer, ForeignKey("doctors.id"), nullable=False)
    date            = Column(DateTime, nullable=False)
    available_slots = Column(JSON, default=list)
    booked_slots    = Column(JSON, default=list)

    doctor = relationship("Doctor", back_populates="schedules")


class Appointment(Base):
    __tablename__ = "appointments"

    id         = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    doctor_id  = Column(Integer, ForeignKey("doctors.id"), nullable=False)
    date       = Column(DateTime, nullable=False)
    time_slot  = Column(String(10), nullable=False)   # "10:30"
    status     = Column(Enum(AppointmentStatus), default=AppointmentStatus.SCHEDULED)
    notes      = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    patient = relationship("Patient", back_populates="appointments")
    doctor  = relationship("Doctor", back_populates="appointments")


class ConversationLog(Base):
    """
    Optional persistent log of every message.
    Useful for debugging and reviewing what the agent said.
    Not required for the core flow but good to have.
    """
    __tablename__ = "conversation_logs"

    id         = Column(Integer, primary_key=True, index=True)
    session_id = Column(String(100), index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=True)
    role       = Column(String(20))    # "user" or "assistant"
    content    = Column(Text)
    language   = Column(String(10), default="en")
    latency_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class OutboundCampaign(Base):
    """
    Tracks outbound calls/messages we want to send to patients.
    The scheduler picks these up and fires them off.
    """
    __tablename__ = "outbound_campaigns"

    id               = Column(Integer, primary_key=True, index=True)
    name             = Column(String(100), nullable=False)
    campaign_type    = Column(String(50))   # "reminder", "follow_up", "vaccination"
    patient_id       = Column(Integer, ForeignKey("patients.id"), nullable=False)
    appointment_id   = Column(Integer, ForeignKey("appointments.id"), nullable=True)
    scheduled_at     = Column(DateTime, nullable=False)
    status           = Column(String(20), default="pending")   # pending / sent / failed
    message_template = Column(Text, nullable=True)
    created_at       = Column(DateTime, default=datetime.utcnow)

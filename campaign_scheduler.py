"""
scheduler/campaign_scheduler.py
APScheduler-based outbound campaign scheduler.
Sends appointment reminders, follow-ups, vaccination reminders.
"""
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime, timedelta
from typing import List
import json
import sys, os

sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from config import settings


class CampaignScheduler:
    def __init__(self):
        self.scheduler = AsyncIOScheduler(timezone=settings.campaign_default_timezone)
        self._db_session_factory = None

    def init_app(self, db_session_factory):
        self._db_session_factory = db_session_factory

        if settings.campaign_scheduler_enabled:
            # Run reminder check every hour
            self.scheduler.add_job(
                self._send_appointment_reminders,
                CronTrigger(hour="*/1"),
                id="appointment_reminders",
                replace_existing=True
            )
            # Follow-up check daily at 9 AM
            self.scheduler.add_job(
                self._send_follow_ups,
                CronTrigger(hour=9, minute=0),
                id="follow_up_calls",
                replace_existing=True
            )
            self.scheduler.start()
            print("✅ Campaign scheduler started")

    async def _send_appointment_reminders(self):
        """Send reminders for appointments happening in next 24 hours."""
        if not self._db_session_factory:
            return

        db = self._db_session_factory()
        try:
            from backend.models import Appointment, AppointmentStatus, Patient, Doctor
            from memory.persistent_memory.persistent_memory import persistent_memory

            now = datetime.utcnow()
            tomorrow = now + timedelta(hours=24)

            upcoming = (
                db.query(Appointment)
                .filter(
                    Appointment.date.between(now, tomorrow),
                    Appointment.status == AppointmentStatus.SCHEDULED
                )
                .all()
            )

            for appt in upcoming:
                patient = db.query(Patient).filter(Patient.id == appt.patient_id).first()
                doctor = db.query(Doctor).filter(Doctor.id == appt.doctor_id).first()

                if patient and doctor:
                    lang = persistent_memory.get_language_preference(patient.id)
                    message = self._get_reminder_message(
                        patient.name, doctor.name, appt.date.strftime("%Y-%m-%d"), appt.time_slot, lang
                    )
                    print(f"[CAMPAIGN] Reminder → {patient.name} ({patient.phone}): {message[:80]}...")

        finally:
            db.close()

    async def _send_follow_ups(self):
        """Send follow-ups for appointments from yesterday."""
        if not self._db_session_factory:
            return

        db = self._db_session_factory()
        try:
            from backend.models import Appointment, AppointmentStatus, Patient, Doctor
            from memory.persistent_memory.persistent_memory import persistent_memory

            yesterday = datetime.utcnow() - timedelta(days=1)
            yesterday_start = yesterday.replace(hour=0, minute=0, second=0)
            yesterday_end = yesterday.replace(hour=23, minute=59, second=59)

            completed = (
                db.query(Appointment)
                .filter(
                    Appointment.date.between(yesterday_start, yesterday_end),
                    Appointment.status == AppointmentStatus.COMPLETED
                )
                .all()
            )

            for appt in completed:
                patient = db.query(Patient).filter(Patient.id == appt.patient_id).first()
                doctor = db.query(Doctor).filter(Doctor.id == appt.doctor_id).first()

                if patient and doctor:
                    lang = persistent_memory.get_language_preference(patient.id)
                    message = self._get_followup_message(patient.name, doctor.name, lang)
                    print(f"[CAMPAIGN] Follow-up → {patient.name}: {message[:80]}...")

        finally:
            db.close()

    def _get_reminder_message(self, patient_name: str, doctor_name: str,
                               date: str, time: str, language: str) -> str:
        messages = {
            "en": f"Hello {patient_name}, this is a reminder about your appointment with {doctor_name} tomorrow ({date}) at {time}. Please confirm, reschedule, or cancel.",
            "hi": f"नमस्ते {patient_name}, यह आपकी {doctor_name} के साथ कल ({date}) {time} बजे अपॉइंटमेंट की याद दिलाने के लिए है।",
            "ta": f"வணக்கம் {patient_name}, நாளை ({date}) {time} மணிக்கு {doctor_name} உடன் உங்கள் சந்திப்பு உள்ளது என்பதை நினைவூட்டுகிறோம்."
        }
        return messages.get(language, messages["en"])

    def _get_followup_message(self, patient_name: str, doctor_name: str, language: str) -> str:
        messages = {
            "en": f"Hello {patient_name}, we hope you're feeling better after your visit with {doctor_name}. How are you doing? Would you like to book a follow-up?",
            "hi": f"नमस्ते {patient_name}, हम आशा करते हैं कि {doctor_name} से मिलने के बाद आप बेहतर महसूस कर रहे हैं।",
            "ta": f"வணக்கம் {patient_name}, {doctor_name} உடன் உங்கள் சந்திப்பிற்குப் பிறகு நீங்கள் நலமாக இருப்பீர்கள் என நம்புகிறோம்."
        }
        return messages.get(language, messages["en"])

    def stop(self):
        if self.scheduler.running:
            self.scheduler.shutdown()


campaign_scheduler = CampaignScheduler()

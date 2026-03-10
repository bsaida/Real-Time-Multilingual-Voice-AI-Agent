# scheduler/appointment_engine/appointment_service.py
#
# Core booking logic. All the "business rules" live here:
#   - Can't book in the past
#   - Can't double-book a slot
#   - When a slot is taken, suggest alternatives
#   - Cancelling a slot frees it back up for others
#
# Every public method returns a dict with a "success" key so the agent
# can easily tell whether an action worked and what to tell the patient.

import sys
import os
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from backend.models import (
    Appointment, AppointmentStatus,
    Doctor, DoctorSchedule, Patient
)
from sqlalchemy.orm import Session


class AppointmentService:

    def __init__(self, db: Session):
        self.db = db

    # ──────────────────────────────────────────
    # Doctor lookup helpers
    # ──────────────────────────────────────────

    def find_doctor(self, specialization: str = None, name: str = None,
                    hospital: str = None) -> Optional[Doctor]:
        """Find the first matching doctor. All params are optional filters."""
        q = self.db.query(Doctor).filter(Doctor.is_active == True)
        if specialization:
            q = q.filter(Doctor.specialization.ilike(f"%{specialization}%"))
        if name:
            q = q.filter(Doctor.name.ilike(f"%{name}%"))
        if hospital:
            q = q.filter(Doctor.hospital.ilike(f"%{hospital}%"))
        return q.first()

    def list_doctors(self, specialization: str = None) -> List[Doctor]:
        q = self.db.query(Doctor).filter(Doctor.is_active == True)
        if specialization:
            q = q.filter(Doctor.specialization.ilike(f"%{specialization}%"))
        return q.all()

    # ──────────────────────────────────────────
    # Availability check
    # ──────────────────────────────────────────

    def get_slots_for_doctor(self, doctor_id: int, date: datetime) -> Dict[str, Any]:
        """Returns available (not yet booked) slots for one doctor on one day."""
        day = date.replace(hour=0, minute=0, second=0, microsecond=0)
        sched = (
            self.db.query(DoctorSchedule)
            .filter(DoctorSchedule.doctor_id == doctor_id,
                    DoctorSchedule.date == day)
            .first()
        )

        if not sched:
            return {"available": False, "slots": [], "message": "No schedule on this date"}

        already_booked = sched.booked_slots or []
        free_slots = [s for s in (sched.available_slots or []) if s not in already_booked]

        return {
            "available": len(free_slots) > 0,
            "slots":     free_slots,
            "date":      day.strftime("%Y-%m-%d"),
            "doctor_id": doctor_id
        }

    def check_availability(self, specialization: str, date: datetime) -> Dict[str, Any]:
        """Check all doctors of a specialization and return those with free slots."""
        matching_doctors = self.list_doctors(specialization)
        results = []

        for doc in matching_doctors:
            info = self.get_slots_for_doctor(doc.id, date)
            if info["available"]:
                results.append({
                    "doctor_id":       doc.id,
                    "doctor_name":     doc.name,
                    "hospital":        doc.hospital,
                    "specialization":  doc.specialization,
                    "available_slots": info["slots"]
                })

        return {
            "found":          len(results) > 0,
            "results":        results,
            "date":           date.strftime("%Y-%m-%d"),
            "specialization": specialization
        }

    # ──────────────────────────────────────────
    # Booking
    # ──────────────────────────────────────────

    def book_appointment(self, patient_id: int, doctor_id: int,
                         date: datetime, time_slot: str,
                         notes: str = None) -> Dict[str, Any]:
        """
        Book an appointment. Validates:
        - Not in the past
        - Schedule exists for the doctor on that date
        - Slot is in the available list
        - Slot isn't already taken
        """
        # parse the full datetime so we can compare with now()
        try:
            appt_dt = datetime.strptime(
                f"{date.strftime('%Y-%m-%d')} {time_slot}", "%Y-%m-%d %H:%M"
            )
        except ValueError:
            return {"success": False, "error": f"Invalid time format: {time_slot}. Use HH:MM"}

        if appt_dt <= datetime.now():
            return {"success": False, "error": "Can't book an appointment in the past"}

        day = date.replace(hour=0, minute=0, second=0, microsecond=0)
        sched = (
            self.db.query(DoctorSchedule)
            .filter(DoctorSchedule.doctor_id == doctor_id,
                    DoctorSchedule.date == day)
            .first()
        )

        if not sched:
            return {"success": False, "error": "Doctor has no schedule on this date"}

        available = sched.available_slots or []
        booked    = sched.booked_slots or []

        if time_slot not in available:
            return {"success": False, "error": f"Slot {time_slot} doesn't exist in this doctor's schedule"}

        if time_slot in booked:
            # tell the patient what's still free
            alternatives = [s for s in available if s not in booked][:3]
            return {
                "success":      False,
                "error":        f"Slot {time_slot} is already booked",
                "alternatives": alternatives
            }

        # check the patient doesn't already have this exact appointment
        dupe = (
            self.db.query(Appointment)
            .filter(
                Appointment.patient_id == patient_id,
                Appointment.doctor_id  == doctor_id,
                Appointment.date       == day,
                Appointment.time_slot  == time_slot,
                Appointment.status     == AppointmentStatus.SCHEDULED
            )
            .first()
        )
        if dupe:
            return {"success": False, "error": "You already have this appointment booked"}

        # all good — create the appointment and mark the slot taken
        new_appt = Appointment(
            patient_id=patient_id,
            doctor_id=doctor_id,
            date=day,
            time_slot=time_slot,
            status=AppointmentStatus.SCHEDULED,
            notes=notes
        )
        self.db.add(new_appt)

        sched.booked_slots = booked + [time_slot]
        self.db.commit()
        self.db.refresh(new_appt)

        doc = self.db.query(Doctor).filter(Doctor.id == doctor_id).first()
        return {
            "success":        True,
            "appointment_id": new_appt.id,
            "doctor_name":    doc.name     if doc else "Unknown",
            "hospital":       doc.hospital if doc else "Unknown",
            "date":           day.strftime("%Y-%m-%d"),
            "time_slot":      time_slot,
            "status":         new_appt.status.value
        }

    # ──────────────────────────────────────────
    # Cancellation
    # ──────────────────────────────────────────

    def cancel_appointment(self, appointment_id: int, patient_id: int) -> Dict[str, Any]:
        appt = (
            self.db.query(Appointment)
            .filter(Appointment.id == appointment_id,
                    Appointment.patient_id == patient_id)
            .first()
        )

        if not appt:
            return {"success": False, "error": "Appointment not found"}

        if appt.status == AppointmentStatus.CANCELLED:
            return {"success": False, "error": "This appointment is already cancelled"}

        appt.status = AppointmentStatus.CANCELLED

        # free the slot back up so someone else can book it
        day   = appt.date.replace(hour=0, minute=0, second=0, microsecond=0)
        sched = (
            self.db.query(DoctorSchedule)
            .filter(DoctorSchedule.doctor_id == appt.doctor_id,
                    DoctorSchedule.date == day)
            .first()
        )
        if sched and appt.time_slot in (sched.booked_slots or []):
            updated_booked = list(sched.booked_slots)
            updated_booked.remove(appt.time_slot)
            sched.booked_slots = updated_booked

        self.db.commit()
        return {"success": True, "appointment_id": appointment_id,
                "message": "Appointment cancelled successfully"}

    # ──────────────────────────────────────────
    # Rescheduling
    # ──────────────────────────────────────────

    def reschedule_appointment(self, appointment_id: int, patient_id: int,
                                new_date: datetime, new_time_slot: str) -> Dict[str, Any]:
        """
        Reschedule = cancel the old one + book a new one.
        If the new booking fails we reinstate the old appointment.
        """
        old_appt = (
            self.db.query(Appointment)
            .filter(Appointment.id == appointment_id,
                    Appointment.patient_id == patient_id)
            .first()
        )

        if not old_appt:
            return {"success": False, "error": "Appointment not found"}

        if old_appt.status == AppointmentStatus.CANCELLED:
            return {"success": False, "error": "Can't reschedule a cancelled appointment"}

        doctor_id = old_appt.doctor_id

        cancel_result = self.cancel_appointment(appointment_id, patient_id)
        if not cancel_result["success"]:
            return cancel_result

        book_result = self.book_appointment(
            patient_id=patient_id,
            doctor_id=doctor_id,
            date=new_date,
            time_slot=new_time_slot
        )

        if not book_result["success"]:
            # new slot didn't work — put the old appointment back
            old_appt.status = AppointmentStatus.SCHEDULED
            self.db.commit()
            return book_result

        # mark the old one as rescheduled (not cancelled) for cleaner history
        old_appt.status = AppointmentStatus.RESCHEDULED
        self.db.commit()

        return {**book_result, "rescheduled": True, "old_appointment_id": appointment_id}

    # ──────────────────────────────────────────
    # Listing
    # ──────────────────────────────────────────

    def get_patient_appointments(self, patient_id: int) -> List[Dict[str, Any]]:
        appts = (
            self.db.query(Appointment)
            .filter(Appointment.patient_id == patient_id)
            .order_by(Appointment.date.desc())
            .all()
        )

        result = []
        for a in appts:
            doc = self.db.query(Doctor).filter(Doctor.id == a.doctor_id).first()
            result.append({
                "appointment_id": a.id,
                "doctor_name":    doc.name           if doc else "Unknown",
                "specialization": doc.specialization if doc else "Unknown",
                "hospital":       doc.hospital       if doc else "Unknown",
                "date":           a.date.strftime("%Y-%m-%d"),
                "time_slot":      a.time_slot,
                "status":         a.status.value
            })
        return result

    def get_or_create_patient(self, phone: str, name: str = "Patient") -> Patient:
        """Find a patient by phone or create a new record if they're new."""
        p = self.db.query(Patient).filter(Patient.phone == phone).first()
        if not p:
            p = Patient(phone=phone, name=name)
            self.db.add(p)
            self.db.commit()
            self.db.refresh(p)
        return p

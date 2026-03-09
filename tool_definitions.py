# agent/tools/tool_definitions.py
#
# OpenAI function-calling tool schemas.
# The LLM reads these descriptions to decide which function to call.
# Good descriptions here = the model makes better decisions.
#
# Each tool maps to a method in AppointmentService.

APPOINTMENT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "check_availability",
            "description": (
                "Check which time slots are available for a doctor specialization on a given date. "
                "Use this before booking to show the patient their options."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "specialization": {
                        "type": "string",
                        "description": "Doctor type: cardiologist, dermatologist, general physician, orthopedic, or neurologist"
                    },
                    "date": {
                        "type": "string",
                        "description": "Date in YYYY-MM-DD format"
                    }
                },
                "required": ["specialization", "date"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "book_appointment",
            "description": (
                "Book an appointment for the patient. "
                "You must have a doctor_id (from check_availability), a date and a time slot first. "
                "Confirm these details with the patient before calling this."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "doctor_id": {
                        "type":        "integer",
                        "description": "The doctor's ID from the availability check result"
                    },
                    "date": {
                        "type":        "string",
                        "description": "Date in YYYY-MM-DD format"
                    },
                    "time_slot": {
                        "type":        "string",
                        "description": "Time in HH:MM format, e.g. 10:00 or 14:30"
                    },
                    "notes": {
                        "type":        "string",
                        "description": "Optional reason for visit or patient notes"
                    }
                },
                "required": ["doctor_id", "date", "time_slot"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_appointment",
            "description": "Cancel an existing appointment. Ask the patient to confirm before doing this.",
            "parameters": {
                "type": "object",
                "properties": {
                    "appointment_id": {
                        "type":        "integer",
                        "description": "The appointment ID to cancel"
                    }
                },
                "required": ["appointment_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "reschedule_appointment",
            "description": "Move an existing appointment to a new date and time.",
            "parameters": {
                "type": "object",
                "properties": {
                    "appointment_id": {
                        "type":        "integer",
                        "description": "The appointment ID to reschedule"
                    },
                    "new_date": {
                        "type":        "string",
                        "description": "New date in YYYY-MM-DD format"
                    },
                    "new_time_slot": {
                        "type":        "string",
                        "description": "New time in HH:MM format"
                    }
                },
                "required": ["appointment_id", "new_date", "new_time_slot"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_patient_appointments",
            "description": "Get all appointments (past and upcoming) for the current patient.",
            "parameters": {
                "type":       "object",
                "properties": {},
                "required":   []
            }
        }
    }
]

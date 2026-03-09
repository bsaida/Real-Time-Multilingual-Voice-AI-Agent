# memory/persistent_memory/persistent_memory.py
#
# Long-term storage for patient preferences that should survive across sessions.
# Currently stores:
#   - preferred_language (so we don't have to re-detect every call)
#   - preferred_doctor and hospital (learned from past bookings)
#   - last appointment details (useful for follow-up calls)
#   - interaction count (just for analytics)
#
# Same Redis/in-memory dual approach as session memory, but TTL is 30 days
# instead of 30 minutes.

import json
from datetime import datetime
from typing import Optional, Dict, Any
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from config import settings

try:
    import redis as redis_lib
    _REDIS_AVAILABLE = True
except ImportError:
    _REDIS_AVAILABLE = False

THIRTY_DAYS = 60 * 60 * 24 * 30


class PersistentMemory:

    def __init__(self):
        self._local: Dict[str, Any] = {}
        self._redis = None

        if _REDIS_AVAILABLE:
            try:
                self._redis = redis_lib.from_url(settings.redis_url, decode_responses=True)
                self._redis.ping()
            except Exception:
                self._redis = None

    def _rkey(self, patient_id: int) -> str:
        return f"patient_profile:{patient_id}"

    def _empty_profile(self, patient_id: int) -> Dict[str, Any]:
        return {
            "patient_id":         patient_id,
            "preferred_language": "en",
            "preferred_doctor_id":None,
            "preferred_hospital": None,
            "last_appointment":   None,
            "interaction_count":  0,
            "notes":              "",
            "created_at":         datetime.utcnow().isoformat(),
            "updated_at":         datetime.utcnow().isoformat()
        }

    def get_profile(self, patient_id: int) -> Dict[str, Any]:
        key = self._rkey(patient_id)
        if self._redis:
            raw = self._redis.get(key)
            if raw:
                return json.loads(raw)
        return self._local.get(key, self._empty_profile(patient_id))

    def save_profile(self, patient_id: int, data: Dict[str, Any]):
        data["updated_at"] = datetime.utcnow().isoformat()
        key = self._rkey(patient_id)
        if self._redis:
            self._redis.setex(key, THIRTY_DAYS, json.dumps(data))
        else:
            self._local[key] = data

    def update_language_preference(self, patient_id: int, language: str):
        """Called after every interaction so we remember the language for next time."""
        p = self.get_profile(patient_id)
        p["preferred_language"] = language
        self.save_profile(patient_id, p)

    def get_language_preference(self, patient_id: int) -> str:
        return self.get_profile(patient_id).get("preferred_language", "en")

    def update_preferred_doctor(self, patient_id: int, doctor_id: int):
        p = self.get_profile(patient_id)
        p["preferred_doctor_id"] = doctor_id
        self.save_profile(patient_id, p)

    def get_preferred_doctor(self, patient_id: int) -> Optional[int]:
        return self.get_profile(patient_id).get("preferred_doctor_id")

    def record_appointment(self, patient_id: int, appt_data: Dict[str, Any]):
        p = self.get_profile(patient_id)
        p["last_appointment"] = appt_data
        self.save_profile(patient_id, p)

    def increment_interactions(self, patient_id: int):
        p = self.get_profile(patient_id)
        p["interaction_count"] = p.get("interaction_count", 0) + 1
        self.save_profile(patient_id, p)


# module singleton
persistent_memory = PersistentMemory()

# memory/session_memory/session_manager.py
#
# Per-conversation context store. Backed by Redis when available,
# falls back to a plain Python dict when Redis isn't running.
#
# What gets stored per session:
#   - conversation history (last 20 messages — we trim older ones)
#   - the patient's current language
#   - any "pending intent" (e.g. user said "book appointment" but
#     hasn't told us which doctor yet — we park the intent here)
#
# Sessions expire after SESSION_TTL seconds (default 30 min).

import json
import uuid
from datetime import datetime
from typing import Dict, Any, List
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from config import settings

try:
    import redis as redis_lib
    _REDIS_AVAILABLE = True
except ImportError:
    _REDIS_AVAILABLE = False


class SessionMemory:

    def __init__(self):
        self._local: Dict[str, Any] = {}    # fallback store
        self._redis = None

        if _REDIS_AVAILABLE:
            try:
                self._redis = redis_lib.from_url(settings.redis_url, decode_responses=True)
                self._redis.ping()
                print("Session memory: connected to Redis")
            except Exception as e:
                print(f"Session memory: Redis unavailable, using in-memory fallback ({e})")
                self._redis = None

    def _rkey(self, session_id: str) -> str:
        return f"session:{session_id}"

    def _blank_session(self, session_id: str) -> Dict[str, Any]:
        return {
            "session_id":           session_id,
            "patient_id":           None,
            "language":             "en",
            "conversation_history": [],
            "pending_intent":       None,
            "pending_data":         {},
            "created_at":           datetime.utcnow().isoformat(),
            "updated_at":           datetime.utcnow().isoformat()
        }

    def get_session(self, session_id: str) -> Dict[str, Any]:
        if self._redis:
            raw = self._redis.get(self._rkey(session_id))
            if raw:
                return json.loads(raw)
        return self._local.get(session_id, self._blank_session(session_id))

    def save_session(self, session_id: str, data: Dict[str, Any]):
        data["updated_at"] = datetime.utcnow().isoformat()
        if self._redis:
            self._redis.setex(self._rkey(session_id), settings.session_ttl, json.dumps(data))
        else:
            self._local[session_id] = data

    def create_session(self, patient_id: int = None, language: str = "en") -> str:
        sid  = str(uuid.uuid4())
        data = self._blank_session(sid)
        data["patient_id"] = patient_id
        data["language"]   = language
        self.save_session(sid, data)
        return sid

    def add_message(self, session_id: str, role: str, content: str, language: str = "en"):
        """
        Append a message to the conversation history.
        We cap at 20 messages — beyond that the oldest ones drop off.
        20 turns is plenty of context for an appointment booking conversation.
        """
        sess = self.get_session(session_id)
        sess["conversation_history"].append({
            "role":      role,
            "content":   content,
            "language":  language,
            "timestamp": datetime.utcnow().isoformat()
        })
        # trim to 20 — keeps the Redis payload small too
        sess["conversation_history"] = sess["conversation_history"][-20:]
        self.save_session(session_id, sess)

    def update_language(self, session_id: str, language: str):
        sess = self.get_session(session_id)
        sess["language"] = language
        self.save_session(session_id, sess)

    def set_pending_intent(self, session_id: str, intent: str, data: Dict = None):
        """
        Park a partially-complete intent here while we wait for more info.
        Example: user says "book appointment" → intent = "book", data = {}
        Next turn they say "cardiologist" → data = {"doctor": "cardiologist"}
        """
        sess = self.get_session(session_id)
        sess["pending_intent"] = intent
        sess["pending_data"]   = data or {}
        self.save_session(session_id, sess)

    def clear_pending_intent(self, session_id: str):
        sess = self.get_session(session_id)
        sess["pending_intent"] = None
        sess["pending_data"]   = {}
        self.save_session(session_id, sess)

    def get_conversation_history(self, session_id: str) -> List[Dict]:
        return self.get_session(session_id).get("conversation_history", [])

    def delete_session(self, session_id: str):
        if self._redis:
            self._redis.delete(self._rkey(session_id))
        self._local.pop(session_id, None)


# one instance for the whole app
session_memory = SessionMemory()

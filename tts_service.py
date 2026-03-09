# services/text_to_speech/tts_service.py
#
# Converts the agent's text response into audio the user can hear.
# Using OpenAI's TTS-1 model which is fast (~80-120ms for short sentences).
# TTS-1-HD sounds better but takes ~2x longer — not worth it for a voice agent
# where latency matters more than studio quality.
#
# Voice selection:
#   - alloy → works fine for English
#   - nova  → handles Hindi and Tamil better (clearer pronunciation)

import sys
import os
import time

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from config import settings


class TextToSpeechService:

    def __init__(self):
        self.provider = settings.tts_provider
        self._client  = None

    def _get_client(self):
        if self._client is None:
            import openai
            self._client = openai.OpenAI(api_key=settings.openai_api_key)
        return self._client

    def _pick_voice(self, lang: str) -> str:
        """
        Different voices work better for different languages.
        Alloy is the default for English, nova handles Indic scripts more cleanly.
        """
        voice_by_lang = {
            "en": "alloy",
            "hi": "nova",
            "ta": "nova"
        }
        return voice_by_lang.get(lang, settings.tts_voice)

    async def synthesize(self, text: str, language: str = "en",
                         voice: str = None) -> dict:
        """
        Turn text into speech.

        Returns:
            {
                "audio":      bytes (mp3),
                "format":     "mp3",
                "latency_ms": int
            }
        """
        t_start = time.monotonic()

        if not text.strip():
            # nothing to say — return empty audio quickly
            return {"audio": b"", "format": "mp3", "latency_ms": 0}

        if self.provider == "openai":
            result = await self._openai_tts(text, language, voice)
        else:
            result = {"audio": b"", "format": "mp3"}

        result["latency_ms"] = int((time.monotonic() - t_start) * 1000)
        return result

    async def _openai_tts(self, text: str, lang: str, voice: str = None) -> dict:
        try:
            client       = self._get_client()
            chosen_voice = voice or self._pick_voice(lang)

            resp = client.audio.speech.create(
                model="tts-1",           # swap to "tts-1-hd" for better quality
                voice=chosen_voice,
                input=text,
                response_format="mp3"
            )

            return {"audio": resp.content, "format": "mp3"}

        except Exception as err:
            print(f"[TTS] Failed to synthesize: {err}")
            return {"audio": b"", "format": "mp3", "error": str(err)}


# module singleton
tts_service = TextToSpeechService()

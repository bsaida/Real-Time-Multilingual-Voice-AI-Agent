# services/speech_to_text/stt_service.py
#
# Wraps the OpenAI Whisper API for speech-to-text.
# Whisper is multilingual out of the box which is why we picked it —
# it handles Hindi and Tamil reasonably well without extra config.
#
# The flow:
#   1. Receive audio bytes (webm/wav/mp3 from the browser or a file)
#   2. Write to a temp file (Whisper API requires a file, not raw bytes)
#   3. Call the API, get back text + detected language
#   4. Clean up the temp file
#
# TODO: look into streaming transcription if latency becomes an issue

import os
import sys
import time
import tempfile

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from config import settings


class SpeechToTextService:

    def __init__(self):
        self.provider = settings.stt_provider
        self._client  = None   # lazy init — don't create until first use

    def _get_client(self):
        if self._client is None:
            import openai
            self._client = openai.OpenAI(api_key=settings.openai_api_key)
        return self._client

    async def transcribe(self, audio_bytes: bytes, hint_lang: str = None,
                         audio_fmt: str = "webm") -> dict:
        """
        Convert audio bytes to text.

        hint_lang: pass the session language if you already know it.
                   Whisper uses it as a hint, not a hard constraint.

        Returns:
            {
                "text":       str,
                "language":   "en" | "hi" | "ta",
                "confidence": float,
                "latency_ms": int
            }
        """
        t_start = time.monotonic()

        if self.provider == "openai_whisper":
            result = await self._whisper(audio_bytes, hint_lang, audio_fmt)
        else:
            # placeholder for other providers (Google, Azure, etc.)
            result = {"text": "", "language": hint_lang or "en", "confidence": 0.0}

        result["latency_ms"] = int((time.monotonic() - t_start) * 1000)
        return result

    async def _whisper(self, audio_bytes: bytes, hint_lang: str,
                       audio_fmt: str) -> dict:
        try:
            client = self._get_client()

            # write audio to a temp file — Whisper API doesn't accept raw bytes
            suffix = f".{audio_fmt}"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(audio_bytes)
                tmp_path = tmp.name

            try:
                with open(tmp_path, "rb") as audio_file:
                    params = {
                        "model":           "whisper-1",
                        "file":            audio_file,
                        "response_format": "verbose_json",  # gives us language info
                    }
                    # only pass language hint if we're confident about it
                    if hint_lang and hint_lang != "en":
                        lang_map = {"hi": "hi", "ta": "ta", "en": "en"}
                        params["language"] = lang_map.get(hint_lang, "en")

                    resp = client.audio.transcriptions.create(**params)

                # normalise the language name Whisper returns ("english" → "en")
                raw_lang = getattr(resp, "language", "english")
                lang_lookup = {"english": "en", "hindi": "hi", "tamil": "ta"}
                detected = lang_lookup.get(raw_lang.lower(), "en")

                return {
                    "text":       resp.text.strip(),
                    "language":   detected,
                    "confidence": 0.9
                }
            finally:
                os.unlink(tmp_path)   # always clean up

        except Exception as err:
            print(f"[STT] Whisper call failed: {err}")
            return {
                "text":       "",
                "language":   hint_lang or "en",
                "confidence": 0.0,
                "error":      str(err)
            }


# module-level singleton — import this everywhere you need STT
stt_service = SpeechToTextService()

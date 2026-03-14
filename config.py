# config.py
# Pulling all env vars into one place so I don't have os.getenv() scattered everywhere.
# pydantic-settings handles the .env file loading and type casting automatically.
# Just add stuff here when you need a new setting.

from pydantic_settings import BaseSettings

class Settings(BaseSettings):

    # ── OpenAI is the default for everything (LLM + Whisper + TTS)
    # Set ANTHROPIC_API_KEY too if you want to swap the LLM later
    openai_api_key: str = ""
    anthropic_api_key: str = ""

    # ── Database
    # Defaults to postgres but the app works fine with sqlite for local dev
    # sqlite:///./local.db  ← use this if you don't have postgres running
    database_url: str = "postgresql://postgres:Saida%40799@localhost:5432/voice_ai_db"

    # ── Redis is used for session memory + persistent patient profiles
    # If Redis isn't running we fall back to an in-memory dict (sessions lost on restart)
    redis_url: str = "redis://localhost:6379/0"

    # ── Server
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = True

    # ── Which LLM to use. "openai" works out of the box.
    # gpt-4o is fast enough to hit our 450ms target most of the time
    llm_provider: str = "openai"
    llm_model: str = "gpt-4o"

    # ── Speech to Text
    # openai_whisper = Whisper API (needs key, good for Hindi/Tamil too)
    stt_provider: str = "openai_whisper"

    # ── Text to Speech
    # openai tts-1 model — tts-1-hd is better quality but ~2x slower
    tts_provider: str = "openai"
    tts_voice: str = "alloy"

    # ── Latency target from the assignment spec
    # We log a warning if any pipeline run exceeds this
    latency_target_ms: int = 450

    # ── Session TTL in seconds (30 mins = 1800)
    # After this the Redis key expires and the session is gone
    session_ttl: int = 1800

    # ── Outbound campaign scheduler (reminders, follow-ups etc.)
    campaign_scheduler_enabled: bool = True
    campaign_default_timezone: str = "Asia/Kolkata"

    class Config:
        env_file = ".env"
        extra = "ignore"   # don't crash if .env has extra stuff


# single instance imported everywhere — don't create new Settings() elsewhere
settings = Settings()

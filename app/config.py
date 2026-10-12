"""Central configuration. Everything comes from the environment;
see .env.example for the full documented list. No secrets are
hard-coded anywhere in this codebase."""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    APP_NAME: str = "FrontDesk"
    APP_ENV: str = "dev"

    DATABASE_URL: str = "sqlite:///./frontdesk.db"

    OWNER_TOKEN: str = "dev-owner-token-change-me"

    # LLM (pluggable). Empty key => clearly-labeled demo mode.
    LLM_API_KEY: str = ""
    LLM_BASE_URL: str = "https://api.openai.com/v1"
    LLM_MODEL: str = "gpt-4o-mini"
    LLM_TIMEOUT_SECONDS: int = 20

    # WhatsApp Cloud API (Meta). Empty => routes report "not configured".
    WHATSAPP_VERIFY_TOKEN: str = ""
    WHATSAPP_TOKEN: str = ""
    WHATSAPP_PHONE_NUMBER_ID: str = ""

    # Payments: incoming payments POST to /api/payments/incoming (gateway
    # webhook, staff entry, or a UPI ref the customer typed). The matching
    # engine links each payment to its order; the secret authenticates
    # your gateway's webhook calls.
    PAYMENT_WEBHOOK_SECRET: str = ""

    # ── Voice: async-first ────────────────────────────────────────
    # WhatsApp voice notes are transcribed + answered with no telephony
    # cost. Any OpenAI-compatible /audio/transcriptions endpoint works
    # (OpenAI Whisper, Sarvam AI for Telugu/Hindi — see ROADMAP.md).
    # Empty key => voice notes get an honest "please type it" reply.
    STT_API_KEY: str = ""
    STT_BASE_URL: str = "https://api.openai.com/v1"
    STT_MODEL: str = "whisper-1"

    # Live telephony: PREMIUM roadmap module, not built — hooks only.
    # See ROADMAP.md for the ₹2–3/min cost target and the levers.
    TELEPHONY_ENABLED: bool = False
    TELEPHONY_PROVIDER: str = ""  # exotel | plivo (future)
    EXOTEL_SID: str = ""
    EXOTEL_TOKEN: str = ""
    EXOTEL_FROM: str = ""
    PLIVO_AUTH_ID: str = ""
    PLIVO_AUTH_TOKEN: str = ""
    PLIVO_FROM: str = ""

    DEFAULT_LANGUAGE: str = "en"
    DEFAULT_SLOT_MINUTES: int = 30
    DEFAULT_CURRENCY: str = "INR"

    @property
    def demo_mode(self) -> bool:
        return not self.LLM_API_KEY.strip()

    @property
    def whatsapp_configured(self) -> bool:
        return bool(self.WHATSAPP_VERIFY_TOKEN and self.WHATSAPP_TOKEN and self.WHATSAPP_PHONE_NUMBER_ID)

    @property
    def owner_token_is_default(self) -> bool:
        return self.OWNER_TOKEN == "dev-owner-token-change-me"


@lru_cache
def get_settings() -> Settings:
    return Settings()

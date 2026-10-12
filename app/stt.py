"""Pluggable speech-to-text — powers WhatsApp voice-note handling.

Async-first voice strategy: a customer sends a voice note, we transcribe
it and reply in text. No telephony cost, no phone-call infrastructure.

- Set STT_API_KEY (+ optional STT_BASE_URL / STT_MODEL) for any
  OpenAI-compatible /audio/transcriptions endpoint (OpenAI Whisper,
  Sarvam AI for Telugu/Hindi, …).
- Leave it empty: voice notes get an honest "please type it" reply and
  the message is logged for the owner.
"""
from __future__ import annotations

from typing import Protocol

import httpx


class STTProvider(Protocol):
    name: str

    async def transcribe(self, audio: bytes, mime: str, lang_hint: str = "") -> str: ...


class OpenAICompatibleSTTProvider:
    """Any OpenAI-compatible /v1/audio/transcriptions endpoint."""

    name = "openai-compatible-stt"

    def __init__(self, api_key: str, base_url: str, model: str, timeout: int = 60):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    async def transcribe(self, audio: bytes, mime: str, lang_hint: str = "") -> str:
        ext = {"audio/ogg": "ogg", "audio/mpeg": "mp3", "audio/mp4": "m4a"}.get(mime, "ogg")
        files = {"file": (f"voice.{ext}", audio, mime)}
        data = {"model": self.model}
        if lang_hint:
            data["language"] = lang_hint
        headers = {"Authorization": f"Bearer {self.api_key}"}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(f"{self.base_url}/audio/transcriptions",
                                     files=files, data=data, headers=headers)
            resp.raise_for_status()
            return resp.json().get("text", "").strip()


def get_stt(api_key: str, base_url: str, model: str) -> STTProvider | None:
    """None => voice notes are politely declined (honest, logged)."""
    if not api_key.strip():
        return None
    return OpenAICompatibleSTTProvider(api_key, base_url, model)

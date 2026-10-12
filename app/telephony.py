"""Live telephony hooks — PREMIUM ROADMAP MODULE, not built.

The founder's voice strategy is async-first: WhatsApp voice notes work
today (transcribe → engine → reply, zero telephony cost). Live phone
calls come later as a premium module, when the unit economics clear
the ₹2–3/min target (see ROADMAP.md for the levers: Exotel/Plivo,
Sarvam AI STT+TTS, small models, concise responses).

These hooks are the integration surface the telephony module will
implement. The engine never changes: a call is just another channel
feeding transcripts into ChatEngine.handle() and speaking replies.
"""
from __future__ import annotations

from typing import Protocol


class TelephonyNotEnabled(RuntimeError):
    pass


class TelephonyProvider(Protocol):
    """Implemented by the future Exotel/Plivo adapter."""
    name: str

    async def place_call(self, *, to: str, business_slug: str, reason: str) -> str:
        """Start an outbound AI call. Returns a call id."""
        ...

    async def speak(self, *, call_id: str, text: str) -> None:
        """Speak text into a live call (TTS)."""
        ...

    async def end_call(self, *, call_id: str, reason: str = "") -> None:
        ...


class StubTelephonyProvider:
    """Placeholder wired in until the premium module ships."""

    name = "stub"

    async def place_call(self, *, to: str, business_slug: str, reason: str) -> str:
        raise TelephonyNotEnabled(
            "Live telephony is a premium roadmap module (see ROADMAP.md). "
            "WhatsApp voice notes work today with zero telephony cost.")

    async def speak(self, *, call_id: str, text: str) -> None:
        raise TelephonyNotEnabled("Live telephony is a premium roadmap module (see ROADMAP.md).")

    async def end_call(self, *, call_id: str, reason: str = "") -> None:
        raise TelephonyNotEnabled("Live telephony is a premium roadmap module (see ROADMAP.md).")


def get_telephony(enabled: bool) -> TelephonyProvider:
    # Future: return ExotelTelephonyProvider / PlivoTelephonyProvider here.
    return StubTelephonyProvider()

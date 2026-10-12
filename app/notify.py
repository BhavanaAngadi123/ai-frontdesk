"""Outbound customer notifications. WhatsApp delivery is config-gated:
without Meta credentials this logs instead of sending, so every flow
can be tested end to end first."""
from __future__ import annotations

import logging

import httpx

from .config import Settings

log = logging.getLogger("frontdesk.notify")


async def send_whatsapp_text(settings: Settings, to: str, text: str) -> bool:
    """Deliver a WhatsApp message via the Cloud API. Returns True if a
    real send was attempted, False when credentials are absent (logged)."""
    if not settings.whatsapp_configured:
        log.info("[whatsapp:send:disabled] to=%s text=%s", to, text[:160])
        return False
    url = f"https://graph.facebook.com/v21.0/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            url,
            headers={"Authorization": f"Bearer {settings.WHATSAPP_TOKEN}"},
            json={"messaging_product": "whatsapp", "to": to,
                  "type": "text", "text": {"body": text[:4000]}},
        )
        resp.raise_for_status()
    return True

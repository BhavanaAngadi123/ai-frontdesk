"""WhatsApp Cloud API webhook.

Config-gated: with no WHATSAPP_* keys set, GET returns 404
{detail: not configured} and POST is rejected — the code paths are
complete and activate the moment her Meta credentials are added.

When configured:
  GET  /api/whatsapp/webhook  — Meta's verify handshake
  POST /api/whatsapp/webhook  — incoming message receiver; routes
       every text message through the ChatEngine and replies via
       the Graph API (or logs when WHATSAPP_TOKEN is absent).
       Voice notes are transcribed first (async-first voice: no
       telephony cost) when STT_API_KEY is set; otherwise the
       customer gets an honest "please type it" reply.
"""
from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..db import get_db
from ..engine import ChatEngine
from ..i18n import t
from ..llm import get_provider
from ..notify import send_whatsapp_text
from ..stt import get_stt

log = logging.getLogger("frontdesk.whatsapp")
router = APIRouter(prefix="/api/whatsapp", tags=["whatsapp"])


@router.get("/webhook")
def verify(mode: str = Query("", alias="hub.mode"),
           token: str = Query("", alias="hub.verify_token"),
           challenge: str = Query("", alias="hub.challenge"),
           settings: Settings = Depends(get_settings)):
    if not settings.WHATSAPP_VERIFY_TOKEN:
        return Response(status_code=404, content='{"detail":"WhatsApp not configured"}',
                        media_type="application/json")
    if mode == "subscribe" and token == settings.WHATSAPP_VERIFY_TOKEN:
        return Response(content=challenge, media_type="text/plain")
    return Response(status_code=403, content='{"detail":"verify token mismatch"}',
                    media_type="application/json")


@router.post("/webhook")
async def receive(request: Request, db: Session = Depends(get_db),
                  settings: Settings = Depends(get_settings)):
    # Meta requires a fast 200 even if we can't process; never 500 here.
    try:
        payload = await request.json()
    except Exception:
        return {"ok": True}
    if not settings.whatsapp_configured:
        log.info("whatsapp message received but integration not configured")
        return {"ok": True}

    llm = get_provider(settings.LLM_API_KEY, settings.LLM_BASE_URL,
                       settings.LLM_MODEL, settings.LLM_TIMEOUT_SECONDS)
    engine = ChatEngine(db, settings, llm)
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for msg in value.get("messages", []):
                wa_id = msg.get("from", "")
                profile = ((value.get("contacts") or [{}])[0].get("profile") or {}).get("name", "")
                text, voice_note = None, False
                if msg.get("type") == "text":
                    text = (msg.get("text") or {}).get("body", "")
                elif msg.get("type") == "audio":
                    # Async-first voice: transcribe the voice note, then run
                    # the normal engine on the transcript. No telephony cost.
                    voice_note = True
                    text = await _transcribe_voice_note(settings, msg)
                    if not text:
                        await send_whatsapp_text(
                            settings, wa_id, t("voice_note_no_stt", "en"))
                        continue
                else:
                    continue
                try:
                    result = engine.handle(business_slug="demo", channel="whatsapp", text=text,
                                           customer_name=profile, customer_phone=wa_id,
                                           external_id=wa_id)
                    prefix = "🎙️ " if voice_note else ""
                    if result.get("disclosure"):
                        await send_whatsapp_text(settings, wa_id, result["disclosure"])
                    await send_whatsapp_text(settings, wa_id, prefix + result["reply"])
                except Exception:
                    log.exception("whatsapp handling failed for %s", wa_id)
    return {"ok": True}


async def _transcribe_voice_note(settings: Settings, msg: dict) -> str | None:
    """Download a WhatsApp voice note and transcribe it. Returns the
    transcript, or None when STT isn't configured (caller replies
    honestly) or the download fails."""
    stt = get_stt(settings.STT_API_KEY, settings.STT_BASE_URL, settings.STT_MODEL)
    if not stt or not settings.WHATSAPP_TOKEN:
        log.info("[stt:disabled] voice note received, no STT_API_KEY")
        return None
    media_id = (msg.get("audio") or {}).get("id")
    if not media_id:
        return None
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            meta = await client.get(
                f"https://graph.facebook.com/v21.0/{media_id}",
                headers={"Authorization": f"Bearer {settings.WHATSAPP_TOKEN}"})
            meta.raise_for_status()
            url = meta.json().get("url")
            dl = await client.get(url, headers={"Authorization": f"Bearer {settings.WHATSAPP_TOKEN}"})
            dl.raise_for_status()
            audio = dl.content
        mime = (msg.get("audio") or {}).get("mime_type", "audio/ogg")
        text = await stt.transcribe(audio, mime)
        log.info("[stt] transcribed voice note: %s", text[:120])
        return text or None
    except Exception:
        log.exception("voice note transcription failed")
        return None

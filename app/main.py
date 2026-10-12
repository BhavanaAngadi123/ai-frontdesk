"""FrontDesk — the AI front desk for businesses of every size.

Run:  uvicorn app.main:app --reload
Docs: http://127.0.0.1:8000/docs
"""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .db import engine
from .models import Base
from .routers import bookings, catalog, chat, owner, pages, payments, telephony, whatsapp
from .seed import seed_demo
from .db import SessionLocal

log = logging.getLogger("frontdesk")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.APP_NAME,
                  description="AI receptionist for businesses of every size — chat, catalog, bookings, payments, owner escalation inbox.",
                  version="0.1.0")

    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        seed_demo(db)

    if settings.owner_token_is_default:
        log.warning("OWNER_TOKEN is the dev default — set a real one in .env for anything beyond local dev.")
    if settings.demo_mode:
        log.info("No LLM_API_KEY — running in clearly-labeled DEMO mode.")
    if not settings.whatsapp_configured:
        log.info("WhatsApp not configured — webhook routes report 'not configured' until keys are added.")

    app.mount("/static", StaticFiles(directory="static"), name="static")
    for r in (pages, chat, catalog, bookings, payments, whatsapp, telephony, owner):
        app.include_router(r.router)
    return app


app = create_app()

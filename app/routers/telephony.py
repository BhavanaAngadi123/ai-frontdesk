"""Live telephony — PREMIUM ROADMAP MODULE (not built).

Async-first voice (WhatsApp voice notes) works today with zero
telephony cost. These routes are the clean integration surface for
the future Exotel/Plivo module; until TELEPHONY_ENABLED is set they
answer 501 with a pointer to ROADMAP.md.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ..config import Settings, get_settings

router = APIRouter(prefix="/api/telephony", tags=["telephony"])

NOT_BUILT = {"detail": ("Live telephony is a premium roadmap module — see ROADMAP.md. "
                        "WhatsApp voice notes work today with zero telephony cost.")}


@router.post("/incoming")
async def incoming(request: Request, settings: Settings = Depends(get_settings)):
    """Carrier webhook for inbound calls (future: Exotel/Plivo)."""
    return JSONResponse(status_code=501, content=NOT_BUILT)


@router.post("/place-call")
async def place_call(request: Request, settings: Settings = Depends(get_settings)):
    """Start an outbound AI call (future)."""
    return JSONResponse(status_code=501, content=NOT_BUILT)


@router.get("/status")
async def status(settings: Settings = Depends(get_settings)):
    return {"enabled": settings.TELEPHONY_ENABLED,
            "provider": settings.TELEPHONY_PROVIDER or None,
            "note": "Async-first voice (WhatsApp voice notes) is the current voice path."}

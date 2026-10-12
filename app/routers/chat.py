"""Customer chat endpoint — the AI receptionist front door."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..db import get_db
from ..engine import ChatEngine
from ..llm import get_provider
from ..models import Business, Conversation
from ..schemas import ChatRequest, ChatResponse

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.post("", response_model=ChatResponse)
async def chat(req: ChatRequest, db: Session = Depends(get_db),
               settings: Settings = Depends(get_settings)):
    llm = get_provider(settings.LLM_API_KEY, settings.LLM_BASE_URL,
                       settings.LLM_MODEL, settings.LLM_TIMEOUT_SECONDS)
    engine = ChatEngine(db, settings, llm)
    try:
        result = engine.handle(business_slug=req.business, channel=req.channel, text=req.text,
                               customer_name=req.customer_name, customer_phone=req.customer_phone,
                               external_id="", lang=req.lang, conversation_id=req.conversation_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    if llm:
        # Best-effort LLM polish of the already-decided reply.
        business = db.scalars(select(Business).where(Business.slug == req.business)).first()
        conv = db.get(Conversation, result["conversation_id"])
        try:
            result["reply"] = await engine.apolish(
                result["reply"], business, conv, req.lang or (business.language if business else "en"))
        except Exception:
            pass
    return ChatResponse(**result)

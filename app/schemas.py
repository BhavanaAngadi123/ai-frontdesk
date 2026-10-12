"""Pydantic v2 request/response schemas."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


# ── Chat ──────────────────────────────────────────────────────────
class ChatRequest(BaseModel):
    business: str = Field(default="demo", description="Business slug")
    conversation_id: int | None = None
    channel: Literal["web", "whatsapp"] = "web"
    customer_name: str = ""
    customer_phone: str = ""
    text: str = Field(min_length=1, max_length=4000)
    lang: str = ""  # empty => business default


class ChatResponse(BaseModel):
    reply: str
    intent: str
    action: str = ""  # the brain's resolved action (see intents.ACTIONS)
    conversation_id: int
    demo_mode: bool
    escalated: bool = False
    escalation_id: int | None = None
    disclosure: str | None = None  # bot-disclosure text, present on a new conversation's first reply


class MessageOut(BaseModel):
    id: int
    role: str
    text: str
    intent: str
    meta: dict = {}
    created_at: datetime | None = None


# ── Catalog ───────────────────────────────────────────────────────
class CatalogItemIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = ""
    price: int = Field(default=0, ge=0, description="Minor units (paise/cents)")
    currency: str = "INR"
    category: str = ""
    image_url: str = ""
    in_stock: bool = True
    tags: str = ""


class CatalogItemOut(CatalogItemIn):
    id: int
    business_id: int


# ── Bookings ──────────────────────────────────────────────────────
class SlotOut(BaseModel):
    starts_at: datetime
    ends_at: datetime
    available: bool = True


class BookingIn(BaseModel):
    business: str = "demo"
    service: str = ""
    customer_name: str = Field(min_length=1, max_length=120)
    customer_phone: str = Field(min_length=4, max_length=32)
    starts_at: datetime
    notes: str = ""


class BookingOut(BaseModel):
    id: int
    service: str
    customer_name: str
    customer_phone: str
    starts_at: datetime
    ends_at: datetime
    status: str


# ── Escalations ───────────────────────────────────────────────────
class EscalationOut(BaseModel):
    id: int
    conversation_id: int
    reason: str
    brief_summary: str
    ai_tried: list[str]
    needs_decision: str
    status: str
    claimed_by: str
    context: dict[str, Any]
    created_at: datetime | None = None


class ResolveIn(BaseModel):
    resolution_note: str = ""


class OwnerReplyIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


# ── Setup wizard ──────────────────────────────────────────────────
class WizardStepIn(BaseModel):
    business: str = ""  # empty on step 1 => creates a new business
    step: int = Field(ge=1, le=5)
    data: dict[str, Any] = {}


class WizardStepOut(BaseModel):
    business: str
    step_completed: int
    next_step: int | None
    done: bool
    message: str


# ── Misc ──────────────────────────────────────────────────────────
class HealthOut(BaseModel):
    ok: bool
    app: str
    env: str
    demo_mode: bool
    whatsapp_configured: bool
    owner_token_is_default: bool
    db: str

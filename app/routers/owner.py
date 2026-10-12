"""Owner dashboard APIs: escalation inbox (with the owner brief),
one-tap claim/resolve, owner replies inside the SAME thread,
plain-language analytics, settings, and the 5-minute setup wizard."""
from __future__ import annotations

import re
import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..db import get_db
from ..i18n import available_locales, t
from ..models import Booking, Business, Conversation, Escalation, Message
from ..schemas import (EscalationOut, HealthOut, MessageOut, OwnerReplyIn,
                       ResolveIn, WizardStepIn, WizardStepOut)

router = APIRouter(tags=["owner"])


def _require_owner(x_owner_token: str = Header(""), settings: Settings = Depends(get_settings)) -> Settings:
    if x_owner_token != settings.OWNER_TOKEN:
        raise HTTPException(401, "Owner token required (X-Owner-Token header)")
    return settings


def _business(db: Session, slug: str) -> Business:
    b = db.scalars(select(Business).where(Business.slug == slug)).first()
    if not b:
        raise HTTPException(404, f"Unknown business: {slug}")
    return b


def _esc_out(e: Escalation) -> EscalationOut:
    return EscalationOut(id=e.id, conversation_id=e.conversation_id, reason=e.reason,
                         brief_summary=e.brief_summary, ai_tried=list(e.ai_tried or []),
                         needs_decision=e.needs_decision, status=e.status,
                         claimed_by=e.claimed_by, context=e.context_json or {},
                         created_at=e.created_at)


# ── Escalation inbox ──────────────────────────────────────────────
@router.get("/api/owner/escalations", response_model=list[EscalationOut])
def list_escalations(business: str = "demo", status: str = "open",
                     settings: Settings = Depends(_require_owner),
                     db: Session = Depends(get_db)):
    b = _business(db, business)
    stmt = select(Escalation).where(Escalation.business_id == b.id).order_by(Escalation.id.desc())
    if status != "all":
        stmt = stmt.where(Escalation.status == status)
    return [_esc_out(e) for e in db.scalars(stmt.limit(100)).all()]


@router.post("/api/owner/escalations/{esc_id}/claim", response_model=EscalationOut)
def claim(esc_id: int, by: str = "owner", settings: Settings = Depends(_require_owner),
          db: Session = Depends(get_db)):
    e = db.get(Escalation, esc_id)
    if not e:
        raise HTTPException(404, "Escalation not found")
    e.status, e.claimed_by = "claimed", by
    db.commit()
    return _esc_out(e)


@router.post("/api/owner/escalations/{esc_id}/resolve", response_model=EscalationOut)
def resolve(esc_id: int, payload: ResolveIn, settings: Settings = Depends(_require_owner),
            db: Session = Depends(get_db)):
    from ..learn import learn_from_escalation
    e = db.get(Escalation, esc_id)
    if not e:
        raise HTTPException(404, "Escalation not found")
    e.status, e.resolution_note, e.resolved_at = "resolved", payload.resolution_note, datetime.now()
    conv = db.get(Conversation, e.conversation_id)
    if conv:
        conv.status = "resolved"
    db.commit()
    # A resolution note teaches the shop too — but only when it's a real answer.
    if payload.resolution_note and len(payload.resolution_note.strip()) > 20:
        learn_from_escalation(db, e, payload.resolution_note, source="resolution")
    return _esc_out(e)


@router.post("/api/owner/escalations/{esc_id}/reply")
def owner_reply(esc_id: int, payload: OwnerReplyIn, settings: Settings = Depends(_require_owner),
                db: Session = Depends(get_db)):
    """The owner answers inside the SAME conversation — the thread never
    resets, the customer never repeats themselves. The answer is also
    logged back into this shop's knowledge (per-shop learning)."""
    from ..learn import learn_from_escalation
    e = db.get(Escalation, esc_id)
    if not e:
        raise HTTPException(404, "Escalation not found")
    db.add(Message(conversation_id=e.conversation_id, role="owner", text=payload.text, intent="owner_reply"))
    if e.status == "open":
        e.status, e.claimed_by = "claimed", "owner"
    conv = db.get(Conversation, e.conversation_id)
    if conv and conv.status == "escalated":
        conv.status = "open"
    db.commit()
    learned = learn_from_escalation(db, e, payload.text, source="owner_reply")
    return {"ok": True, "conversation_id": e.conversation_id,
            "learned": bool(learned), "learned_id": learned.id if learned else None}


# ── Conversations ─────────────────────────────────────────────────
@router.get("/api/owner/conversations")
def conversations(business: str = "demo", settings: Settings = Depends(_require_owner),
                  db: Session = Depends(get_db)):
    b = _business(db, business)
    rows = db.scalars(select(Conversation).where(Conversation.business_id == b.id)
                      .order_by(Conversation.updated_at.desc()).limit(50)).all()
    out = []
    for c in rows:
        last = db.scalars(select(Message).where(Message.conversation_id == c.id)
                          .order_by(Message.id.desc()).limit(1)).first()
        out.append({"id": c.id, "channel": c.channel, "status": c.status,
                    "demo_mode": c.demo_mode,
                    "last_message": last.text[:80] if last else "",
                    "updated_at": c.updated_at})
    return out


@router.get("/api/owner/conversations/{conv_id}/messages", response_model=list[MessageOut])
def messages(conv_id: int, settings: Settings = Depends(_require_owner), db: Session = Depends(get_db)):
    rows = db.scalars(select(Message).where(Message.conversation_id == conv_id)
                      .order_by(Message.id)).all()
    return [MessageOut(id=m.id, role=m.role, text=m.text, intent=m.intent,
                       meta=m.meta_json or {}, created_at=m.created_at)
            for m in rows]


# ── Analytics (plain language) ────────────────────────────────────
@router.get("/api/owner/analytics")
def analytics(business: str = "demo", settings: Settings = Depends(_require_owner),
              db: Session = Depends(get_db)):
    b = _business(db, business)
    since = datetime.now() - timedelta(days=7)
    chats = db.scalar(select(func.count()).select_from(Conversation)
                      .where(Conversation.business_id == b.id, Conversation.created_at >= since)) or 0
    open_escs = db.scalar(select(func.count()).select_from(Escalation)
                          .where(Escalation.business_id == b.id, Escalation.status.in_(["open", "claimed"]))) or 0
    resolved_escs = db.scalar(select(func.count()).select_from(Escalation)
                              .where(Escalation.business_id == b.id, Escalation.status == "resolved",
                                     Escalation.created_at >= since)) or 0
    opened_escs = db.scalar(select(func.count()).select_from(Escalation)
                            .where(Escalation.business_id == b.id, Escalation.created_at >= since)) or 0
    bookings_done = db.scalar(select(func.count()).select_from(Booking)
                              .where(Booking.business_id == b.id, Booking.status == "confirmed",
                                     Booking.created_at >= since)) or 0
    summary = [
        t("analytics_summary_chats", b.language, n=chats),
        t("analytics_summary_bookings", b.language, n=bookings_done),
        t("analytics_summary_escalations", b.language, n=opened_escs, m=resolved_escs),
    ]
    if open_escs:
        summary.append(t("analytics_summary_missed", b.language, n=open_escs))
    return {"chats_7d": chats, "bookings_7d": bookings_done, "escalations_opened_7d": opened_escs,
            "escalations_resolved_7d": resolved_escs, "escalations_waiting": open_escs,
            "plain_language_summary": summary}


# ── Settings ──────────────────────────────────────────────────────
@router.get("/api/owner/settings")
def get_settings_api(business: str = "demo", settings: Settings = Depends(_require_owner),
                     db: Session = Depends(get_db)):
    b = _business(db, business)
    return {"slug": b.slug, "name": b.name, "tagline": b.tagline, "phone": b.phone,
            "language": b.language, "available_languages": available_locales(),
            "currency": b.currency, "bot_name": b.bot_name, "hours": b.hours_json or {},
            "slot_minutes": b.slot_minutes, "require_approval_for": b.require_approval_for or [],
            "setup_completed": b.setup_completed,
            "demo_mode": settings.demo_mode, "whatsapp_configured": settings.whatsapp_configured}


@router.put("/api/owner/settings")
def put_settings_api(payload: dict, business: str = "demo",
                     settings: Settings = Depends(_require_owner), db: Session = Depends(get_db)):
    b = _business(db, business)
    for key in ("name", "tagline", "phone", "language", "currency", "bot_name",
                "hours_json", "slot_minutes", "require_approval_for"):
        if key in payload:
            setattr(b, key, payload[key])
    if "hours" in payload:
        b.hours_json = payload["hours"]
    db.commit()
    return {"ok": True}


# ── Per-shop learned knowledge ────────────────────────────────────
@router.get("/api/owner/knowledge")
def list_knowledge(business: str = "demo", settings: Settings = Depends(_require_owner),
                   db: Session = Depends(get_db)):
    """What this shop's AI has learned from its owner's answers —
    the compounding moat, visible."""
    from ..models import LearnedKnowledge
    b = _business(db, business)
    rows = db.scalars(select(LearnedKnowledge).where(LearnedKnowledge.business_id == b.id)
                      .order_by(LearnedKnowledge.use_count.desc(), LearnedKnowledge.id.desc())
                      .limit(200)).all()
    return [{"id": r.id, "question": r.question, "answer": r.answer,
             "source": r.source, "use_count": r.use_count,
             "created_at": r.created_at} for r in rows]


@router.delete("/api/owner/knowledge/{kid}")
def delete_knowledge(kid: int, business: str = "demo",
                     settings: Settings = Depends(_require_owner), db: Session = Depends(get_db)):
    from ..models import LearnedKnowledge
    b = _business(db, business)
    row = db.get(LearnedKnowledge, kid)
    if not row or row.business_id != b.id:
        raise HTTPException(404, "Not found")
    db.delete(row)
    db.commit()
    return {"ok": True}


# ── Setup wizard: 5 steps, ~5 minutes ──────────────────────────────
WIZARD_STEPS = {
    1: "Tell us about your business",
    2: "Pick your language",
    3: "Add a few catalog items",
    4: "Set your hours",
    5: "Connect WhatsApp (optional)",
}


@router.get("/api/setup/wizard")
def wizard_info():
    return {"steps": [{"n": n, "title": title} for n, title in WIZARD_STEPS.items()]}


@router.post("/api/setup/wizard", response_model=WizardStepOut)
def wizard_step(payload: WizardStepIn, db: Session = Depends(get_db),
                settings: Settings = Depends(get_settings)):
    step, data = payload.step, payload.data or {}
    if step == 1:
        name = (data.get("name") or "").strip()
        if not name:
            raise HTTPException(400, "data.name is required")
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:48] or f"shop-{secrets.token_hex(3)}"
        base, i = slug, 1
        while db.scalars(select(Business).where(Business.slug == slug)).first():
            i += 1
            slug = f"{base}-{i}"
        b = Business(slug=slug, name=name, tagline=data.get("tagline", ""),
                     phone=data.get("phone", ""), language=settings.DEFAULT_LANGUAGE,
                     currency=data.get("currency", settings.DEFAULT_CURRENCY),
                     hours_json={}, slot_minutes=settings.DEFAULT_SLOT_MINUTES,
                     require_approval_for=["discount", "refund"])
        db.add(b)
        db.commit()
        return WizardStepOut(business=slug, step_completed=1, next_step=2, done=False,
                             message=t("setup_step1_done", b.language))
    b = _business(db, payload.business)
    if step == 2:
        lang = (data.get("language") or "en").lower()
        b.language = lang if lang in available_locales() else "en"
        db.commit()
        return WizardStepOut(business=b.slug, step_completed=2, next_step=3, done=False,
                             message=t("setup_step2_done", b.language, lang=b.language))
    if step == 3:
        from ..models import CatalogItem
        items = data.get("items", [])
        n = 0
        for it in items[:20]:
            if it.get("name"):
                db.add(CatalogItem(business_id=b.id, name=it["name"], price=int(it.get("price", 0)),
                                   currency=b.currency, description=it.get("description", ""),
                                   category=it.get("category", "")))
                n += 1
        db.commit()
        return WizardStepOut(business=b.slug, step_completed=3, next_step=4, done=False,
                             message=t("setup_step3_done", b.language, n=n))
    if step == 4:
        b.hours_json = data.get("hours", {})
        b.slot_minutes = int(data.get("slot_minutes", b.slot_minutes or 30))
        db.commit()
        return WizardStepOut(business=b.slug, step_completed=4, next_step=5, done=False,
                             message=t("setup_step4_done", b.language))
    if step == 5:
        b.setup_completed = True
        db.commit()
        note = t("setup_step5_done", b.language)
        if not settings.whatsapp_configured:
            note += " (WhatsApp keys not set — the web chat works now; add keys in .env later.)"
        return WizardStepOut(business=b.slug, step_completed=5, next_step=None, done=True, message=note)
    raise HTTPException(400, "step must be 1–5")


# ── Health ────────────────────────────────────────────────────────
@router.get("/api/health", response_model=HealthOut)
def health(db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    try:
        db.execute(select(1))
        db_ok = "ok"
    except Exception as e:
        db_ok = f"error: {e}"
    return HealthOut(ok=db_ok == "ok", app=settings.APP_NAME, env=settings.APP_ENV,
                     demo_mode=settings.demo_mode,
                     whatsapp_configured=settings.whatsapp_configured,
                     owner_token_is_default=settings.owner_token_is_default, db=db_ok)

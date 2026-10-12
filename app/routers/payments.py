"""Orders + payment ingestion.

The model, per the founder:
  • An Order starts as pending_payment and flips to CONFIRMED only
    when a payment is verified by the matching engine.
  • Incoming payments arrive at POST /api/payments/incoming — from a
    gateway webhook, a staff entry, or a UPI ref the customer typed.
    Each one is matched to its order; the customer is confirmed
    instantly; the owner only hears about problems.
  • GET /api/owner/ledger is the owner's clean paid/pending view.

Nothing here moves money or notifies the owner of routine payments —
the owner already gets UPI notifications. The value is matching +
instant customer confirmation + ledger.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from .. import payments as pay
from ..config import Settings, get_settings
from ..db import get_db
from ..i18n import t
from ..models import Business, CatalogItem, Order

router = APIRouter(tags=["payments"])


def _business(db: Session, slug: str) -> Business:
    b = db.scalars(select(Business).where(Business.slug == slug)).first()
    if not b:
        raise HTTPException(404, f"Unknown business: {slug}")
    return b


def _require_owner(x_owner_token: str = Header(""), settings: Settings = Depends(get_settings)) -> Settings:
    if x_owner_token != settings.OWNER_TOKEN:
        raise HTTPException(401, "Owner token required (X-Owner-Token header)")
    return settings


# ── schemas ───────────────────────────────────────────────────────
class OrderItemIn(BaseModel):
    catalog_item_id: int | None = None
    name: str = ""
    qty: int = Field(default=1, ge=1)
    price: int = Field(default=0, ge=0, description="Minor units each")


class OrderIn(BaseModel):
    business: str = "demo"
    customer_name: str = ""
    customer_phone: str = ""
    items: list[OrderItemIn] = Field(min_length=1)


class OrderOut(BaseModel):
    order_id: str
    status: str
    amount: int
    currency: str
    customer_name: str
    customer_phone: str
    items: list[dict]


class PaymentIncoming(BaseModel):
    business: str = "demo"
    amount: int = Field(ge=0, description="Minor units, as received")
    currency: str = ""
    order_id: str = ""
    phone: str = ""
    provider_ref: str = Field(default="", description="UPI UTR / gateway reference")
    source: str = Field(default="webhook", description="webhook | manual | customer_chat")
    raw: dict = {}


class PaymentIngestOut(BaseModel):
    status: str
    order_id: str
    customer_message: str
    owner_problem: str | None = None
    customer_notified: bool = False


class PaymentStatusIn(BaseModel):
    business: str = "demo"
    order_id: str = ""
    phone: str = ""


class PaymentStatusOut(BaseModel):
    found: bool
    order_id: str = ""
    order_status: str = ""
    amount: int = 0
    currency: str = "INR"


# ── orders ────────────────────────────────────────────────────────
@router.post("/api/orders", response_model=OrderOut)
def create_order(payload: OrderIn, settings: Settings = Depends(_require_owner),
                 db: Session = Depends(get_db)):
    b = _business(db, payload.business)
    # Price items from the catalog: a catalog_item_id without an explicit
    # price resolves to the catalog's current price (and name). Without
    # this, orders would total ₹0 and every real payment would look like
    # a "wrong amount" problem.
    items: list[dict] = []
    for i in payload.items:
        d = i.model_dump()
        if d.get("catalog_item_id") and not d.get("price"):
            cat = db.get(CatalogItem, d["catalog_item_id"])
            if cat and cat.business_id == b.id:
                d["price"] = cat.price
                if not d.get("name"):
                    d["name"] = cat.name
        items.append(d)
    order = pay.create_order(db, b, customer_name=payload.customer_name,
                             customer_phone=payload.customer_phone,
                             items=items)
    db.commit()
    return OrderOut(order_id=order.order_id, status=order.status, amount=order.amount,
                    currency=order.currency, customer_name=order.customer_name,
                    customer_phone=order.customer_phone, items=order.items_json)


@router.get("/api/orders", response_model=list[OrderOut])
def list_orders(business: str = "demo", settings: Settings = Depends(_require_owner),
                db: Session = Depends(get_db)):
    b = _business(db, business)
    rows = db.scalars(select(Order).where(Order.business_id == b.id)
                      .order_by(Order.id.desc()).limit(200)).all()
    return [OrderOut(order_id=o.order_id, status=o.status, amount=o.amount, currency=o.currency,
                     customer_name=o.customer_name, customer_phone=o.customer_phone,
                     items=o.items_json or []) for o in rows]


# ── payment ingestion (the gateway webhook target) ────────────────
@router.post("/api/payments/incoming", response_model=PaymentIngestOut)
async def incoming(p: PaymentIncoming, db: Session = Depends(get_db),
                   settings: Settings = Depends(get_settings)):
    b = _business(db, p.business)
    outcome = pay.ingest_payment(db, b, amount=p.amount, currency=p.currency or b.currency,
                                 order_id=p.order_id, phone=p.phone, provider_ref=p.provider_ref,
                                 source=p.source, raw=p.raw, lang=b.language,
                                 strict_amount=(p.source != "customer_chat"))
    notified = False
    if outcome.status == "matched" and outcome.order:
        notified = await pay.notify_customer(db, settings, b, outcome.order, outcome.customer_message)
    return PaymentIngestOut(status=outcome.status, order_id=outcome.order.order_id if outcome.order else "",
                            customer_message=outcome.customer_message,
                            owner_problem=outcome.owner_problem, customer_notified=notified)


# ── customer-facing status check ──────────────────────────────────
@router.post("/api/payments/status", response_model=PaymentStatusOut)
def status(payload: PaymentStatusIn, db: Session = Depends(get_db)):
    b = _business(db, payload.business)
    order = pay.find_order(db, b.id, order_id=payload.order_id.upper() if payload.order_id else "",
                           phone=payload.phone)
    if not order:
        return PaymentStatusOut(found=False)
    return PaymentStatusOut(found=True, order_id=order.order_id, order_status=order.status,
                            amount=order.amount, currency=order.currency)


# ── owner ledger ──────────────────────────────────────────────────
@router.get("/api/owner/ledger")
def ledger(business: str = "demo", settings: Settings = Depends(_require_owner),
           db: Session = Depends(get_db)):
    b = _business(db, business)
    data = pay.ledger(db, b)
    c, p, x = data["counts"]["confirmed"], data["counts"]["pending_payment"], data["counts"]["problems"]
    data["plain_language"] = t("ledger_summary", b.language, c=c, p=p, x=x)
    return data

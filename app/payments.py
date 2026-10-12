"""Payment ↔ order matching engine.

The founder's rule, encoded:
  1. Every incoming payment (gateway webhook, UPI ref, or a reference
     the customer typed in chat) is matched to an Order + its items.
  2. The customer gets an instant confirmation — no waiting on the owner.
  3. An order flips to CONFIRMED only when a payment is verified.
  4. The owner sees a clean paid/pending ledger and is pinged ONLY on
     problems: wrong amount, payment not found, duplicate.

The AI never "tells the owner a payment arrived" — the owner already
gets UPI notifications. Its job is matching, instant replies, ledger.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import intents
from .config import Settings
from .i18n import t
from .models import Business, Conversation, Customer, Message, Order, PaymentRecord
from .notify import send_whatsapp_text


@dataclass
class MatchOutcome:
    status: str  # matched | wrong_amount | duplicate | unmatched | failed
    order: Order | None
    payment: PaymentRecord
    customer_message: str   # instant reply for the customer
    owner_problem: str | None  # set => ping the owner (escalation)


def find_order(db: Session, business_id: int, order_id: str = "", phone: str = "") -> Order | None:
    if order_id:
        return db.scalars(select(Order).where(
            Order.business_id == business_id, Order.order_id == order_id.upper())).first()
    if phone:
        return db.scalars(select(Order).where(
            Order.business_id == business_id, Order.customer_phone == phone)
            .order_by(Order.id.desc())).first()
    return None


def _next_order_id(db: Session, business_id: int) -> str:
    n = db.scalar(select(Order.id).where(Order.business_id == business_id)
                  .order_by(Order.id.desc()).limit(1)) or 0
    return f"ORD-{1001 + n}"


def create_order(db: Session, business: Business, *, customer_name: str = "",
                 customer_phone: str = "", items: list[dict] | None = None,
                 customer_id: int | None = None) -> Order:
    items = items or []
    amount = sum(int(i.get("price", 0)) * int(i.get("qty", 1)) for i in items)
    order = Order(business_id=business.id, order_id=_next_order_id(db, business.id),
                  customer_id=customer_id, customer_name=customer_name,
                  customer_phone=customer_phone, items_json=items,
                  amount=amount, currency=business.currency, status="pending_payment")
    db.add(order)
    db.flush()
    return order


def ingest_payment(db: Session, business: Business, *, amount: int, currency: str = "",
                   order_id: str = "", phone: str = "", provider_ref: str = "",
                   source: str = "webhook", raw: dict | None = None,
                   lang: str = "en", strict_amount: bool = True) -> MatchOutcome:
    """Match one incoming payment to an order. Always returns an outcome;
    never raises for business-level mismatches (those are outcomes).

    strict_amount=True (gateway webhooks): the received amount must equal
    the order total or it's a wrong_amount problem. False (a customer
    reporting 'paid, UTR …' in chat without stating an amount): the
    reference is the evidence; the amount check is skipped."""
    currency = currency or business.currency
    order_id = (order_id or "").upper()
    payment = PaymentRecord(business_id=business.id, order_id=order_id,
                            customer_phone=phone, amount=amount, currency=currency,
                            provider_ref=provider_ref, source=source, raw_json=raw or {})
    db.add(payment)
    db.flush()
    ow = t("owner_word", lang)

    def problem(status: str, detail: str, customer_msg: str) -> MatchOutcome:
        payment.status = status
        db.commit()
        return MatchOutcome(status, None, payment, customer_msg, detail)

    # Duplicate: same provider ref already matched before.
    if provider_ref:
        dup = db.scalars(select(PaymentRecord).where(
            PaymentRecord.business_id == business.id,
            PaymentRecord.provider_ref == provider_ref,
            PaymentRecord.status == "matched",
            PaymentRecord.id != payment.id)).first()
        if dup:
            detail = f"Duplicate payment: ref {provider_ref} already matched to order {dup.order_id}."
            return problem("duplicate", detail,
                           t("payment_claim_duplicate", lang, ref=provider_ref, order_id=dup.order_id,
                             owner_word=ow))

    order = find_order(db, business.id, order_id=order_id, phone=phone)
    if not order:
        detail = (f"Payment of {intents.format_money(amount, currency)} (ref {provider_ref or '—'}) "
                  f"could not be matched to any order.")
        return problem("unmatched", detail,
                       t("payment_claim_unmatched", lang, money=intents.format_money(amount, currency),
                         ref=provider_ref or "—", owner_word=ow))

    # Order already confirmed => this payment is extra.
    if order.status == "confirmed":
        detail = (f"Duplicate payment: order {order.order_id} is already confirmed; "
                  f"extra {intents.format_money(amount, currency)} arrived (ref {provider_ref or '—'}).")
        payment.order_id, payment.order_db_id = order.order_id, order.id
        return problem("duplicate", detail,
                       t("payment_claim_duplicate", lang, ref=provider_ref or "—",
                         order_id=order.order_id, owner_word=ow))

    # Wrong amount => needs a human, not an auto-confirm.
    # (Skipped for customer chat claims that didn't state an amount —
    # there the UPI reference is the evidence.)
    if strict_amount and amount != order.amount:
        payment.order_id, payment.order_db_id = order.order_id, order.id
        detail = (f"Wrong amount on order {order.order_id}: expected "
                  f"{intents.format_money(order.amount, order.currency)}, got "
                  f"{intents.format_money(amount, currency)} (ref {provider_ref or '—'}).")
        return problem("wrong_amount", detail,
                       t("payment_claim_wrong_amount", lang, order_id=order.order_id,
                         expected=intents.format_money(order.amount, order.currency),
                         got=intents.format_money(amount, currency), owner_word=ow))

    # ── Match: verify, confirm, done. ──
    payment.status = "matched"
    payment.order_id, payment.order_db_id = order.order_id, order.id
    payment.matched_at = datetime.now()
    order.status, order.confirmed_at, order.payment_id = "confirmed", datetime.now(), payment.id
    db.commit()
    msg = t("payment_claim_confirmed", lang, order_id=order.order_id,
            money=intents.format_money(order.amount, order.currency))
    return MatchOutcome("matched", order, payment, msg, None)


async def notify_customer(db: Session, settings: Settings, business: Business,
                          order: Order, message: str) -> bool:
    """Push the instant confirmation to the customer: append to their
    latest open thread, and send over WhatsApp when configured and the
    customer came from WhatsApp. Returns True if anything was delivered."""
    delivered = False
    conv = None
    if order.customer_phone:
        customer = db.scalars(select(Customer).where(
            Customer.business_id == business.id,
            Customer.phone == order.customer_phone)).first()
        if customer:
            conv = db.scalars(select(Conversation).where(
                Conversation.business_id == business.id,
                Conversation.customer_id == customer.id,
                Conversation.status != "resolved")
                .order_by(Conversation.updated_at.desc())).first()
            if conv:
                db.add(Message(conversation_id=conv.id, role="assistant",
                               text=message, intent="payment_confirmed"))
                db.commit()
                delivered = True
            if customer.channel == "whatsapp" and customer.external_id:
                delivered = await send_whatsapp_text(settings, customer.external_id, message) or delivered
    return delivered


def ledger(db: Session, business: Business) -> dict:
    """Owner ledger: clean paid/pending per order + problem payments.
    The owner is pinged only on problems — routine confirmations stay silent."""
    orders = db.scalars(select(Order).where(Order.business_id == business.id)
                        .order_by(Order.id.desc()).limit(200)).all()
    problems = db.scalars(select(PaymentRecord).where(
        PaymentRecord.business_id == business.id,
        PaymentRecord.status.in_(["wrong_amount", "unmatched", "duplicate"]))
        .order_by(PaymentRecord.id.desc()).limit(100)).all()

    def order_row(o: Order) -> dict:
        items = ", ".join(f"{i.get('name','')}×{i.get('qty',1)}" for i in (o.items_json or []))
        return {"order_id": o.order_id, "customer": o.customer_name or o.customer_phone,
                "items": items, "amount": o.amount, "currency": o.currency,
                "status": o.status,
                "money": intents.format_money(o.amount, o.currency),
                "confirmed_at": o.confirmed_at.isoformat() if o.confirmed_at else None}

    return {
        "orders": [order_row(o) for o in orders],
        "problems": [{"id": p.id, "status": p.status, "order_id": p.order_id or "—",
                       "money": intents.format_money(p.amount, p.currency),
                       "ref": p.provider_ref or "—", "source": p.source,
                       "at": p.created_at.isoformat() if p.created_at else None} for p in problems],
        "counts": {
            "confirmed": sum(1 for o in orders if o.status == "confirmed"),
            "pending_payment": sum(1 for o in orders if o.status == "pending_payment"),
            "problems": len(problems),
        },
    }

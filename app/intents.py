"""Tool-calling style intent detection + the tools themselves.

The engine detects an intent from the customer message, runs the
matching tool (FAQ lookup, catalog search, booking, payment status,
escalation), and then composes a reply. Deterministic and testable;
an LLM (when configured) only polishes wording — it never decides
what happened.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .models import Booking, Business, CatalogItem, Faq, PaymentRecord

# ── Intent detection ──────────────────────────────────────────────

INTENTS = ("greeting", "knowledge", "catalog", "customization", "price_quote",
           "delivery", "booking", "booking_cancel", "payment", "escalate",
           "sensitive", "fallback")

_HUMAN_WORDS = ["human", "agent", "owner", "person", "real person", "someone real",
                "call me", "callback", "insaan", "manushya"]
_SENSITIVE = {
    "discount": ["discount", "offer", "coupon", "cheaper", "reduce price", "bargain", "chhoot", "sasta"],
    "refund": ["refund", "return", "money back", "damaged", "broken", "defective", "kharaab"],
}
_BOOKING_WORDS = ["book", "booking", "appointment", "slot", "schedule", "visit", "reserve"]
_CANCEL_WORDS = ["cancel"]
_PAYMENT_WORDS = ["payment", "paid", "pay", "upi", "order status", "order id", "order",
                  "transaction", "refund status"]
_GREETING_WORDS = ["hi", "hello", "hey", "namaste", "namaskar", "good morning", "good evening"]
_CATALOG_WORDS = ["price", "cost", "how much", "show", "catalog", "catalogue", "buy", "purchase",
                  "collection", "design", "necklace", "ring", "earring", "bangle", "bracelet", "jewel"]
# Rural-customer actions: delivery and customization are first-class, not FAQ lookups.
_DELIVERY_WORDS = ["deliver", "delivery", "shipping", "ship", "courier", "pincode", "pin code",
                   "dispatch", "cod", "cash on delivery"]
_CUSTOM_WORDS = ["custom", "customize", "customise", "customised", "engrave", "engraving",
                 "personalize", "personalise", "gift wrap", "giftwrap", "make it for me"]
_QUOTE_WORDS = ["quote", "quotation", "estimate"]

# The brain's action taxonomy. EVERY customer request resolves to one of
# these — answers are then assembled strictly from the shop's structured
# data (catalog + customization options + delivery zones + orders).
# Anything the data can't answer becomes a graceful escalation.
ACTIONS = ("knowledge", "catalog_check", "customization", "price_quote",
           "delivery_check", "order", "escalate")


@dataclass
class Intent:
    name: str
    confidence: float
    slots: dict = field(default_factory=dict)


def _contains_any(text: str, words: list[str]) -> str | None:
    """Whole-word match, tolerant of a trailing plural 's'
    ("necklaces" matches "necklace"). Words in _NO_PLURAL are exempt:
    "his" must never greet as "hi"."""
    for w in words:
        if re.search(rf"\b{re.escape(w)}\b", text):
            return w
        if w not in _NO_PLURAL and re.search(rf"\b{re.escape(w)}s\b", text):
            return w
    return None


_NO_PLURAL = {"hi"}


def detect_intent(text: str, lang: str = "en") -> Intent:
    t = text.lower().strip()
    if _contains_any(t, _HUMAN_WORDS):
        return Intent("escalate", 0.95, {"reason": "human_requested"})
    for topic, words in _SENSITIVE.items():
        if _contains_any(t, words):
            return Intent("sensitive", 0.9, {"topic": topic})
    if _contains_any(t, _BOOKING_WORDS):
        if _contains_any(t, _CANCEL_WORDS):
            return Intent("booking_cancel", 0.9)
        return Intent("booking", 0.85)
    if _contains_any(t, _PAYMENT_WORDS):
        slots: dict = {"order_id": _extract_order_id(t)}
        claim = parse_payment_claim(t)
        if claim:
            slots.update(claim)
        return Intent("payment", 0.85, slots)
    if _contains_any(t, _DELIVERY_WORDS):
        m = re.search(r"\b(\d{6})\b", t)
        return Intent("delivery", 0.85, {"pincode": m.group(1) if m else ""})
    if _contains_any(t, _CUSTOM_WORDS):
        return Intent("customization", 0.8, {"query": text})
    if _contains_any(t, _QUOTE_WORDS):
        return Intent("price_quote", 0.85, {"query": text})
    if _contains_any(t, _CATALOG_WORDS):
        return Intent("catalog", 0.75, {"query": text})
    if _contains_any(t, _GREETING_WORDS) and len(t.split()) <= 4:
        return Intent("greeting", 0.9)
    return Intent("knowledge", 0.5, {"query": text})  # knowledge search doubles as the fallback path


def resolve_action(intent_name: str, slots: dict, text: str) -> str:
    """Map a detected intent onto the brain's action taxonomy. Every
    request resolves to exactly one action; the action decides which
    structured data may answer."""
    return {
        "greeting": "greeting",
        "knowledge": "knowledge",
        "catalog": "catalog_check",
        "customization": "customization",
        "price_quote": "price_quote",
        "delivery": "delivery_check",
        "booking": "order",
        "booking_cancel": "order",
        "payment": "order",
        "sensitive": "price_quote",  # gated inside the handler
        "escalate": "escalate",
        "fallback": "knowledge",
    }.get(intent_name, "knowledge")


# ── Tools ─────────────────────────────────────────────────────────

def _keyword_score(query: str, haystack: str) -> int:
    """Whole-word overlap score; stopwords don't count. Substring
    matching ("you" in "your") caused false FAQ hits."""
    stop = {"the", "and", "for", "with", "that", "this", "have", "from",
            "what", "when", "where", "your", "you", "yours", "are", "can",
            "will", "please", "do", "does", "is", "it", "it’s", "its",
            "batao", "kya", "hai", "ka", "ki", "ko", "mein", "me", "my"}
    hay = haystack.lower()
    return sum(1 for w in re.findall(r"\w{3,}", query.lower())
               if w not in stop and re.search(rf"\b{re.escape(w)}\b", hay))


def faq_search(db: Session, business_id: int, query: str) -> Faq | None:
    faqs = db.scalars(select(Faq).where(Faq.business_id == business_id)).all()
    best, best_score = None, 0
    for f in faqs:
        score = _keyword_score(query, f"{f.question} {f.keywords}")
        if score > best_score:
            best, best_score = f, score
    return best if best_score else None


def learned_search(db: Session, business_id: int, query: str):
    """Per-shop learned knowledge — searched BEFORE the static FAQ.
    This is the compounding moat: owner answers become AI answers."""
    from .models import LearnedKnowledge
    rows = db.scalars(select(LearnedKnowledge).where(
        LearnedKnowledge.business_id == business_id)).all()
    best, best_key = None, None
    for r in rows:
        score = _keyword_score(query, f"{r.question} {r.trigger_keywords}")
        if score > 0 and (best is None or (score, r.use_count) > best_key):
            best, best_key = r, (score, r.use_count)
    return best


def catalog_search(db: Session, business_id: int, query: str, limit: int = 5) -> list[CatalogItem]:
    words = [w for w in re.findall(r"\w{3,}", query.lower()) if w not in
             {"the", "and", "for", "with", "show", "price", "cost", "much", "have", "you"}]
    stmt = select(CatalogItem).where(CatalogItem.business_id == business_id)
    if words:
        ors = []
        for w in words:
            like = f"%{w}%"
            ors.append(or_(CatalogItem.name.ilike(like), CatalogItem.description.ilike(like),
                           CatalogItem.tags.ilike(like), CatalogItem.category.ilike(like)))
        stmt = stmt.where(or_(*ors))
    return list(db.scalars(stmt.limit(limit)).all())


def format_money(amount_minor: int, currency: str = "INR") -> str:
    major = amount_minor / 100
    if currency == "INR":
        return f"₹{major:,.0f}"
    return f"{currency} {major:,.2f}"


def parse_date(text: str, today: date | None = None) -> date | None:
    today = today or date.today()
    t = text.lower()
    if "day after tomorrow" in t:
        return today + timedelta(days=2)
    if "tomorrow" in t:
        return today + timedelta(days=1)
    if "today" in t:
        return today
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", t)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.search(r"(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?", t)
    if m:
        d, mo = int(m.group(1)), int(m.group(2))
        y = int(m.group(3)) if m.group(3) else today.year
        if y < 100:
            y += 2000
        try:
            return date(y, mo, d)
        except ValueError:
            return None
    return None


def parse_time(text: str) -> time | None:
    t = text.lower()
    m = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)", t)
    if m:
        h = int(m.group(1))
        minute = int(m.group(2) or 0)
        if m.group(3) == "pm" and h < 12:
            h += 12
        if m.group(3) == "am" and h == 12:
            h = 0
        return time(h, minute)
    m = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", t)
    if m:
        return time(int(m.group(1)), int(m.group(2)))
    return None


def day_hours(business: Business, day: date) -> tuple[time, time] | None:
    names = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    hours = (business.hours_json or {}).get(names[day.weekday()])
    if not hours:
        return None
    return time.fromisoformat(hours[0]), time.fromisoformat(hours[1])


def free_slots(db: Session, business: Business, day: date) -> list[tuple[datetime, datetime]]:
    span = day_hours(business, day)
    if not span:
        return []
    start_t, end_t = span
    step = timedelta(minutes=business.slot_minutes or 30)
    cur = datetime.combine(day, start_t)
    end = datetime.combine(day, end_t)
    booked = {b.starts_at.replace(tzinfo=None) for b in db.scalars(
        select(Booking).where(Booking.business_id == business.id,
                              Booking.status == "confirmed")).all()}
    slots = []
    while cur + step <= end:
        if cur.replace(tzinfo=None) not in booked and cur > datetime.now():
            slots.append((cur, cur + step))
        cur += step
    return slots


def _extract_order_id(t: str) -> str:
    """Pull an order reference like ORD-1002 out of free text, normalized
    to the ORD-NNNN form the engine generates."""
    m = re.search(r"\b((?:order|ord)[\s#_-]*[a-z0-9][a-z0-9-]*)", t)
    if not m:
        return ""
    raw = re.sub(r"[\s#_]", "", m.group(1)).upper()
    return re.sub(r"^ORDER-?", "ORD-", raw)


def parse_payment_claim(text: str) -> dict | None:
    """Detect a customer *reporting* a payment: 'paid', 'payment done',
    optionally with a UPI ref / UTR and an amount. Returns None when the
    message is only asking about status."""
    t = text.lower()
    paid_words = ["paid", "payment done", "payment made", "payment successful",
                  "sent the money", "transferred", "done the payment", "pay kiya"]
    if not any(w in t for w in paid_words):
        return None
    ref = ""
    m = re.search(r"\b(\d{12})\b", t)  # UPI UTR
    if m:
        ref = m.group(1)
    else:
        m = re.search(r"(?:utr|upi\s*(?:ref)?|ref(?:erence)?|txn|transaction)[\s#:]*([a-z0-9]{6,20})", t)
        if m:
            ref = m.group(1).upper()
    # Parse the amount from what's left (ref removed so its digits can't match).
    t2 = t.replace(ref.lower(), " ") if ref else t
    amount_minor = 0
    m = re.search(r"(?:₹|rs\.?|inr)\s*([\d,]+(?:\.\d{1,2})?)", t2)
    if not m:
        # Bare number ("paid 500") — in a payment claim this is the amount
        # in the business currency. Not preceded by ord- etc.
        m = re.search(r"(?<![\w\-₹$€.,])(\d[\d,]{1,6}(?:\.\d{1,2})?)(?![\w\-.,])", t2)
    if m:
        try:
            amount_minor = int(round(float(m.group(1).replace(",", "")) * 100))
        except ValueError:
            amount_minor = 0
    return {"is_claim": True, "ref": ref, "amount_minor": amount_minor}


def order_lookup(db: Session, business_id: int, order_id: str = "", phone: str = ""):
    """Legacy name kept for compatibility — delegates to the payments engine."""
    from . import payments as _pay
    return _pay.find_order(db, business_id, order_id=order_id, phone=phone)


def payment_lookup(db: Session, business_id: int, order_id: str = "", phone: str = ""):
    return order_lookup(db, business_id, order_id=order_id, phone=phone)

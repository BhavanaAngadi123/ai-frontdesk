"""SQLAlchemy 2.x models. Single-tenant friendly: every business row
is scoped by business_id, so one deployment can serve many shops
with fully separate data."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Business(Base):
    __tablename__ = "businesses"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    tagline: Mapped[str] = mapped_column(String(255), default="")
    phone: Mapped[str] = mapped_column(String(32), default="")
    language: Mapped[str] = mapped_column(String(8), default="en")
    currency: Mapped[str] = mapped_column(String(8), default="INR")
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Kolkata")
    bot_name: Mapped[str] = mapped_column(String(64), default="FrontDesk AI")
    # {"mon": ["10:00", "20:00"], ...} — empty list = closed
    hours_json: Mapped[dict] = mapped_column(JSON, default=dict)
    slot_minutes: Mapped[int] = mapped_column(default=30)
    # e.g. ["discount", "refund"] — AI never decides these alone
    require_approval_for: Mapped[list] = mapped_column(JSON, default=list)
    setup_completed: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    catalog: Mapped[list["CatalogItem"]] = relationship(back_populates="business", cascade="all, delete-orphan")
    faqs: Mapped[list["Faq"]] = relationship(back_populates="business", cascade="all, delete-orphan")


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), index=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    phone: Mapped[str] = mapped_column(String(32), default="", index=True)
    channel: Mapped[str] = mapped_column(String(16), default="web")  # web | whatsapp
    external_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), index=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), nullable=True)
    channel: Mapped[str] = mapped_column(String(16), default="web")
    external_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open | escalated | resolved
    language: Mapped[str] = mapped_column(String(8), default="en")
    demo_mode: Mapped[bool] = mapped_column(default=True)
    meta_json: Mapped[dict] = mapped_column(JSON, default=dict)  # consecutive_fallbacks, etc.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    messages: Mapped[list["Message"]] = relationship(back_populates="conversation", cascade="all, delete-orphan")
    customer: Mapped[Customer | None] = relationship()


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), index=True)
    role: Mapped[str] = mapped_column(String(16))  # customer | assistant | owner | system
    text: Mapped[str] = mapped_column(Text)
    intent: Mapped[str] = mapped_column(String(32), default="")
    meta_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class CatalogItem(Base):
    __tablename__ = "catalog_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    price: Mapped[int] = mapped_column(BigInteger, default=0)  # minor units (paise/cents)
    currency: Mapped[str] = mapped_column(String(8), default="INR")
    category: Mapped[str] = mapped_column(String(64), default="")
    image_url: Mapped[str] = mapped_column(String(512), default="")
    in_stock: Mapped[bool] = mapped_column(default=True)
    tags: Mapped[str] = mapped_column(String(255), default="")

    business: Mapped[Business] = relationship(back_populates="catalog")


class Faq(Base):
    __tablename__ = "faqs"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), index=True)
    question: Mapped[str] = mapped_column(String(255))
    answer: Mapped[str] = mapped_column(Text)
    keywords: Mapped[str] = mapped_column(String(255), default="")

    business: Mapped[Business] = relationship(back_populates="faqs")


class Booking(Base):
    __tablename__ = "bookings"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), index=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), nullable=True)
    service: Mapped[str] = mapped_column(String(160), default="")
    customer_name: Mapped[str] = mapped_column(String(120), default="")
    customer_phone: Mapped[str] = mapped_column(String(32), default="", index=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="confirmed")  # confirmed | cancelled | completed
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CustomizationOption(Base):
    """Structured customization data — answers are assembled from these,
    never invented. applies_to: a category name or "all"."""
    __tablename__ = "customization_options"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    price_delta: Mapped[int] = mapped_column(BigInteger, default=0)  # minor units added
    applies_to: Mapped[str] = mapped_column(String(64), default="all")


class DeliveryZone(Base):
    """Structured delivery data. pincode_prefixes: JSON list like
    ["500", "51"]; an empty list means catch-all (rest of India)."""
    __tablename__ = "delivery_zones"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    pincode_prefixes: Mapped[list] = mapped_column(JSON, default=list)
    fee: Mapped[int] = mapped_column(BigInteger, default=0)  # minor units
    days_min: Mapped[int] = mapped_column(default=3)
    days_max: Mapped[int] = mapped_column(default=5)
    cod_available: Mapped[bool] = mapped_column(default=False)


class LearnedKnowledge(Base):
    """Per-shop learning — the compounding moat. Every owner-answered
    escalation is logged back here, so the AI improves per business.
    The knowledge action searches these BEFORE the static FAQ."""
    __tablename__ = "learned_knowledge"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), index=True)
    question: Mapped[str] = mapped_column(Text, default="")  # verbatim-ish customer request
    answer: Mapped[str] = mapped_column(Text)  # the owner's own words
    trigger_keywords: Mapped[str] = mapped_column(String(255), default="")
    source: Mapped[str] = mapped_column(String(16), default="owner_reply")  # owner_reply | resolution
    source_escalation_id: Mapped[int | None] = mapped_column(ForeignKey("escalations.id"), nullable=True)
    use_count: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PaymentRecord(Base):
    """An incoming payment, as reported by a gateway webhook, a UPI ref,
    or the customer themselves. The matching engine (app/payments.py)
    links each payment to its Order.

    status: unmatched | matched | wrong_amount | duplicate | failed
    An order flips to CONFIRMED only when a payment is matched —
    never on the customer's word alone."""
    __tablename__ = "payment_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), index=True)
    order_id: Mapped[str] = mapped_column(String(64), default="", index=True)  # filled by matching
    order_db_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), nullable=True)
    customer_phone: Mapped[str] = mapped_column(String(32), default="", index=True)
    amount: Mapped[int] = mapped_column(BigInteger, default=0)  # minor units, as received
    currency: Mapped[str] = mapped_column(String(8), default="INR")
    status: Mapped[str] = mapped_column(String(16), default="unmatched")
    provider_ref: Mapped[str] = mapped_column(String(128), default="", index=True)  # UPI UTR / gateway ref
    source: Mapped[str] = mapped_column(String(16), default="webhook")  # webhook | manual | customer_chat
    raw_json: Mapped[dict] = mapped_column(JSON, default=dict)
    matched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Order(Base):
    """A customer order. Status flips to CONFIRMED only when a payment
    is verified by the matching engine — the owner never has to watch
    for payments; they only hear about problems."""
    __tablename__ = "orders"
    __table_args__ = (UniqueConstraint("business_id", "order_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), index=True)
    order_id: Mapped[str] = mapped_column(String(64), index=True)  # e.g. ORD-1003
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), nullable=True)
    customer_name: Mapped[str] = mapped_column(String(120), default="")
    customer_phone: Mapped[str] = mapped_column(String(32), default="", index=True)
    # [{"catalog_item_id": 1, "name": "...", "qty": 1, "price": 249900}]
    items_json: Mapped[list] = mapped_column(JSON, default=list)
    amount: Mapped[int] = mapped_column(BigInteger, default=0)  # expected total, minor units
    currency: Mapped[str] = mapped_column(String(8), default="INR")
    status: Mapped[str] = mapped_column(String(16), default="pending_payment")  # pending_payment | confirmed | cancelled
    payment_id: Mapped[int | None] = mapped_column(ForeignKey("payment_records.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    payment: Mapped[PaymentRecord | None] = relationship(foreign_keys=[payment_id])


class Escalation(Base):
    __tablename__ = "escalations"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), index=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), index=True)
    reason: Mapped[str] = mapped_column(String(32), default="human_requested")
    # The owner brief — the product's signature feature:
    brief_summary: Mapped[str] = mapped_column(Text, default="")   # what happened
    ai_tried: Mapped[list] = mapped_column(JSON, default=list)      # what the AI already tried
    needs_decision: Mapped[str] = mapped_column(Text, default="")  # what the human must decide
    status: Mapped[str] = mapped_column(String(16), default="open")  # open | claimed | resolved
    claimed_by: Mapped[str] = mapped_column(String(64), default="")
    resolution_note: Mapped[str] = mapped_column(Text, default="")
    context_json: Mapped[dict] = mapped_column(JSON, default=dict)  # extra facts (order id, amounts…)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    conversation: Mapped[Conversation] = relationship()

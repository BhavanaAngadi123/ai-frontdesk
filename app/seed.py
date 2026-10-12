"""Seed a demo business so the app is explorable in under a minute.
Idempotent — safe to run on every boot."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import payments as pay
from .models import Business, CatalogItem, Faq


def seed_demo(db: Session) -> Business:
    existing = db.scalars(select(Business).where(Business.slug == "demo")).first()
    if existing:
        return existing

    b = Business(
        slug="demo",
        name="Demo Boutique",
        tagline="Handpicked silver jewellery",
        phone="+91 98765 43210",
        language="en",
        currency="INR",
        timezone="Asia/Kolkata",
        bot_name="FrontDesk AI",
        hours_json={"mon": ["10:00", "20:00"], "tue": ["10:00", "20:00"],
                    "wed": ["10:00", "20:00"], "thu": ["10:00", "20:00"],
                    "fri": ["10:00", "20:00"], "sat": ["10:00", "20:00"]},
        slot_minutes=30,
        require_approval_for=["discount", "refund"],
        setup_completed=True,
    )
    db.add(b)
    db.flush()

    items = {}
    for name, desc, price, cat, tags in [
        ("Moonlight Silver Necklace", "925 sterling silver, handcrafted pendant", 249900, "Necklaces", "necklace silver pendant"),
        ("Temple Gold-Plated Jhumkas", "Traditional jhumka earrings, festive wear", 129900, "Earrings", "earring jhumka gold"),
        ("Oxidised Silver Bangle Set", "Set of 4 tribal-style bangles", 89900, "Bangles", "bangle oxidised tribal"),
        ("Pearl Drop Earrings", "Freshwater pearls on silver hooks", 159900, "Earrings", "earring pearl drop"),
        ("Silver Anklet Pair", "Ghungroo anklets, adjustable", 69900, "Anklets", "anklet payal ghungroo"),
        ("Kundan Choker Set", "Bridal choker with earrings", 399900, "Sets", "choker kundan bridal set"),
    ]:
        row = CatalogItem(business_id=b.id, name=name, description=desc, price=price,
                          currency="INR", category=cat, tags=tags)
        db.add(row)
        db.flush()
        items[name] = row

    for q, a, kw in [
        ("What are your timings?", "We're open Monday to Saturday, 10 AM to 8 PM. Sundays we're closed.",
         "timing hours open closed when"),
        ("Do you deliver?", "Yes — we ship across India in 3–5 days. Shipping is free over ₹999.",
         "delivery ship shipping courier"),
        ("What is your return policy?", "7-day easy returns on unworn items with the bill.",
         "return exchange refund policy"),
        ("Which payment methods do you accept?", "UPI, cards and cash on store visit.",
         "payment upi card cash pay"),
    ]:
        db.add(Faq(business_id=b.id, question=q, answer=a, keywords=kw))

    # Structured data the brain answers from (never invented):
    # customization options + delivery zones.
    from .models import CustomizationOption, DeliveryZone
    for name, desc, delta, applies in [
        ("Name engraving", "Hand-engraved name on the pendant", 29900, "Necklaces"),
        ("Gift wrap", "Festive gift box with ribbon", 9900, "all"),
    ]:
        db.add(CustomizationOption(business_id=b.id, name=name, description=desc,
                                   price_delta=delta, applies_to=applies))
    for zname, prefixes, fee, dmin, dmax, cod in [
        ("Telangana & Andhra Pradesh", ["500", "501", "502", "503", "504", "505", "506",
                                        "507", "508", "509", "51", "52"], 4900, 3, 5, True),
        ("Rest of India", [], 9900, 5, 7, False),
    ]:
        db.add(DeliveryZone(business_id=b.id, name=zname, pincode_prefixes=prefixes,
                            fee=fee, days_min=dmin, days_max=dmax, cod_available=cod))

    # ORD-1001: paid + confirmed (the matching engine did it, like prod).
    o1 = pay.create_order(db, b, customer_name="Asha", customer_phone="+91 90000 11111",
                          items=[{"catalog_item_id": items["Moonlight Silver Necklace"].id,
                                  "name": "Moonlight Silver Necklace", "qty": 1, "price": 249900}])
    pay.ingest_payment(db, b, amount=249900, currency="INR", order_id=o1.order_id,
                       phone="+91 90000 11111", provider_ref="UTR123456789012",
                       source="webhook", lang="en")
    # ORD-1002: waiting on payment — the demo customer can "pay" for this in chat.
    pay.create_order(db, b, customer_name="Ravi", customer_phone="+91 90000 22222",
                     items=[{"catalog_item_id": items["Temple Gold-Plated Jhumkas"].id,
                             "name": "Temple Gold-Plated Jhumkas", "qty": 1, "price": 129900}])
    db.commit()
    return b

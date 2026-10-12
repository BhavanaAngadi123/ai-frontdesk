"""Appointment booking: free-slot listing, creation, lookup, cancel."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import intents
from ..db import get_db
from ..models import Booking, Business
from ..schemas import BookingIn, BookingOut, SlotOut

router = APIRouter(prefix="/api/bookings", tags=["bookings"])


def _business(db: Session, slug: str) -> Business:
    b = db.scalars(select(Business).where(Business.slug == slug)).first()
    if not b:
        raise HTTPException(404, f"Unknown business: {slug}")
    return b


@router.get("/slots", response_model=list[SlotOut])
def slots(business: str = "demo", date_: str = Query(..., alias="date"),
          db: Session = Depends(get_db)):
    b = _business(db, business)
    try:
        day = date.fromisoformat(date_)
    except ValueError:
        raise HTTPException(400, "date must be YYYY-MM-DD")
    return [SlotOut(starts_at=s, ends_at=e) for s, e in intents.free_slots(db, b, day)]


@router.post("", response_model=BookingOut)
def create_booking(payload: BookingIn, db: Session = Depends(get_db)):
    b = _business(db, payload.business)
    starts = payload.starts_at.replace(tzinfo=None)
    day = starts.date()
    free = [s for s, _ in intents.free_slots(db, b, day)]
    if all(s != starts for s in free):
        raise HTTPException(409, "That slot is no longer free — pick another.")
    row = Booking(business_id=b.id, service=payload.service, customer_name=payload.customer_name,
                  customer_phone=payload.customer_phone, starts_at=starts,
                  ends_at=starts + timedelta(minutes=b.slot_minutes or 30), notes=payload.notes)
    db.add(row)
    db.commit()
    db.refresh(row)
    return BookingOut(id=row.id, service=row.service, customer_name=row.customer_name,
                      customer_phone=row.customer_phone, starts_at=row.starts_at,
                      ends_at=row.ends_at, status=row.status)


@router.get("", response_model=list[BookingOut])
def lookup(business: str = "demo", phone: str = "", db: Session = Depends(get_db)):
    b = _business(db, business)
    stmt = select(Booking).where(Booking.business_id == b.id).order_by(Booking.starts_at.desc())
    if phone:
        stmt = stmt.where(Booking.customer_phone == phone)
    rows = db.scalars(stmt.limit(50)).all()
    return [BookingOut(id=r.id, service=r.service, customer_name=r.customer_name,
                       customer_phone=r.customer_phone, starts_at=r.starts_at,
                       ends_at=r.ends_at, status=r.status) for r in rows]


@router.delete("/{booking_id}")
def cancel(booking_id: int, business: str = "demo", db: Session = Depends(get_db)):
    b = _business(db, business)
    row = db.get(Booking, booking_id)
    if not row or row.business_id != b.id:
        raise HTTPException(404, "Booking not found")
    row.status = "cancelled"
    db.commit()
    return {"ok": True}

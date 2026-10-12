"""Catalog CRUD. Reads are public (the chat engine and storefront use
them); writes require the owner token."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..db import get_db
from ..models import Business, CatalogItem
from ..schemas import CatalogItemIn, CatalogItemOut

router = APIRouter(prefix="/api/catalog", tags=["catalog"])


def _business(db: Session, slug: str) -> Business:
    b = db.scalars(select(Business).where(Business.slug == slug)).first()
    if not b:
        raise HTTPException(404, f"Unknown business: {slug}")
    return b


def _require_owner(x_owner_token: str, settings: Settings):
    if x_owner_token != settings.OWNER_TOKEN:
        raise HTTPException(401, "Owner token required")


@router.get("", response_model=list[CatalogItemOut])
def list_items(business: str = "demo", db: Session = Depends(get_db)):
    b = _business(db, business)
    return db.scalars(select(CatalogItem).where(CatalogItem.business_id == b.id)
                      .order_by(CatalogItem.id)).all()


@router.post("", response_model=CatalogItemOut)
def create_item(item: CatalogItemIn, business: str = "demo",
                x_owner_token: str = Header(""), db: Session = Depends(get_db),
                settings: Settings = Depends(get_settings)):
    _require_owner(x_owner_token, settings)
    b = _business(db, business)
    row = CatalogItem(business_id=b.id, **item.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.put("/{item_id}", response_model=CatalogItemOut)
def update_item(item_id: int, item: CatalogItemIn, business: str = "demo",
                x_owner_token: str = Header(""), db: Session = Depends(get_db),
                settings: Settings = Depends(get_settings)):
    _require_owner(x_owner_token, settings)
    b = _business(db, business)
    row = db.get(CatalogItem, item_id)
    if not row or row.business_id != b.id:
        raise HTTPException(404, "Item not found")
    for k, v in item.model_dump().items():
        setattr(row, k, v)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/{item_id}")
def delete_item(item_id: int, business: str = "demo",
                x_owner_token: str = Header(""), db: Session = Depends(get_db),
                settings: Settings = Depends(get_settings)):
    _require_owner(x_owner_token, settings)
    b = _business(db, business)
    row = db.get(CatalogItem, item_id)
    if not row or row.business_id != b.id:
        raise HTTPException(404, "Item not found")
    db.delete(row)
    db.commit()
    return {"ok": True}

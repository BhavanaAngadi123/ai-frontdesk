"""Server-rendered pages: the product landing page, the owner
dashboard, and the embeddable chat widget snippet."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..db import get_db
from ..models import Business

router = APIRouter(tags=["pages"])
templates = Jinja2Templates(directory="templates")


@router.get("/", response_class=HTMLResponse)
def landing(request: Request, settings: Settings = Depends(get_settings)):
    return templates.TemplateResponse(request, "landing.html",
                                      {"app_name": settings.APP_NAME, "demo_mode": settings.demo_mode})


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, settings: Settings = Depends(get_settings),
              db: Session = Depends(get_db)):
    businesses = db.scalars(select(Business).order_by(Business.id)).all()
    return templates.TemplateResponse(request, "dashboard.html",
                                      {"app_name": settings.APP_NAME,
                                       "businesses": [{"slug": b.slug, "name": b.name} for b in businesses],
                                       "demo_mode": settings.demo_mode,
                                       "owner_token_is_default": settings.owner_token_is_default})


@router.get("/embed/{slug}", response_class=HTMLResponse)
def embed(slug: str, request: Request, db: Session = Depends(get_db),
          settings: Settings = Depends(get_settings)):
    """A standalone chat page a shop owner can link to or iframe."""
    b = db.scalars(select(Business).where(Business.slug == slug)).first()
    if not b:
        return HTMLResponse("Unknown shop", status_code=404)
    return templates.TemplateResponse(request, "embed.html",
                                      {"business": b, "demo_mode": settings.demo_mode})

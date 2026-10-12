"""Per-shop learning — the compounding moat.

Every escalation the owner answers (a reply in the thread, or a
resolution note) is logged back into THAT shop's knowledge base as a
LearnedKnowledge row. The knowledge action searches learned rows
before the static FAQ, so the AI literally improves per business:
the tenth customer asking about gift wrapping gets the owner's own
words from the first time they asked.

Nothing is shared across businesses: learning is scoped by
business_id, so one shop's knowledge never leaks into another's.
"""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Conversation, Escalation, LearnedKnowledge, Message

_STOPWORDS = {"the", "and", "for", "with", "that", "this", "have", "from",
              "what", "when", "where", "your", "you", "are", "can", "will",
              "please", "batao", "kya", "hai", "ka", "ki", "ko", "mein"}


def _keywords(text: str) -> str:
    words = [w for w in re.findall(r"\w{3,}", text.lower()) if w not in _STOPWORDS]
    seen: list[str] = []
    for w in words:
        if w not in seen:
            seen.append(w)
    return " ".join(seen[:12])


def learn_from_escalation(db: Session, escalation: Escalation, answer: str,
                          source: str = "owner_reply") -> LearnedKnowledge | None:
    """Log an owner-answered escalation back into the shop's knowledge.
    Idempotent per escalation: the first answer wins, later ones are ignored
    (the owner can edit via the knowledge API later)."""
    answer = (answer or "").strip()
    if not answer:
        return None
    existing = db.scalars(select(LearnedKnowledge).where(
        LearnedKnowledge.source_escalation_id == escalation.id)).first()
    if existing:
        return existing
    verbatim = (escalation.context_json or {}).get("verbatim_request", "")
    if not verbatim:
        conv = db.get(Conversation, escalation.conversation_id)
        if conv:
            last = db.scalars(select(Message).where(
                Message.conversation_id == conv.id, Message.role == "customer")
                .order_by(Message.id.desc()).limit(1)).first()
            verbatim = last.text if last else ""
    entry = LearnedKnowledge(
        business_id=escalation.business_id,
        question=verbatim[:1000],
        answer=answer[:2000],
        trigger_keywords=_keywords(verbatim),
        source=source,
        source_escalation_id=escalation.id,
    )
    db.add(entry)
    db.commit()
    return entry

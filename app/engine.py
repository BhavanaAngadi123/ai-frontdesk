"""Conversation engine: owns the chat loop, escalation briefs, and the
trust rules (bot disclosure, human-approval gate, no conversation
reset on handoff).

An LLM, when configured, only polishes wording. Every decision —
which tool ran, whether to escalate — is deterministic and logged
on the message row.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import intents
from .config import Settings
from .i18n import t
from .llm import LLMMessage, LLMProvider
from .models import Booking, Business, Conversation, Customer, Escalation, Message

MAX_FALLBACKS_BEFORE_ESCALATION = 2


class ChatEngine:
    def __init__(self, db: Session, settings: Settings, llm: LLMProvider | None):
        self.db = db
        self.settings = settings
        self.llm = llm  # None => demo mode (template composer)

    # ── public entry ──────────────────────────────────────────────
    def handle(self, *, business_slug: str, channel: str, text: str,
               customer_name: str = "", customer_phone: str = "",
               external_id: str = "", lang: str = "",
               conversation_id: int | None = None) -> dict:
        business = self._business(business_slug)
        lang = lang or business.language or "en"
        conv, customer, disclosure = self._conversation(business, channel, external_id, customer_name,
                                            customer_phone, lang, conversation_id)
        self._save(conv.id, "customer", text, "")
        # NOTE: conv.meta_json is re-read fresh wherever it is used below.
        # Never hold a copy across _dispatch: the booking flow (and others)
        # update conv.meta_json mid-request (pending state), and a stale
        # copy written back at the end would clobber it.
        tried: list[str] = list((conv.meta_json or {}).get("ai_tried", []))

        # Trust rule: an escalated-but-unresolved chat never resets —
        # the customer keeps talking in the SAME thread; the AI holds
        # the line and appends context to the open escalation.
        open_esc = self._open_escalation(conv.id)
        if open_esc:
            open_esc.context_json = {**(open_esc.context_json or {}),
                                     "latest_customer_message": text}
            self.db.commit()
            reply = t("escalation_human_requested", lang, owner_word=t("owner_word", lang))
            self._save(conv.id, "assistant", reply, "escalated_hold", {"action": "escalate"})
            return self._result(conv, "escalated_hold", "escalate", reply, True, open_esc.id,
                               disclosure)

        detected = intents.detect_intent(text, lang)
        pending = (conv.meta_json or {}).get("pending")

        # Continue a multi-turn flow (e.g. booking slot-filling) first.
        if pending and detected.name not in ("escalate", "sensitive", "booking_cancel"):
            action = "order" if pending.get("intent") == "booking" else intents.resolve_action(
                detected.name, detected.slots, text)
            reply, intent_name, done = self._continue_pending(business, conv, customer, pending, text, lang, tried)
        else:
            action = intents.resolve_action(detected.name, detected.slots, text)
            reply, intent_name, done = self._dispatch(business, conv, customer, detected, action,
                                                      text, lang, tried)

        if done.get("escalated"):
            esc_id = done["escalation_id"]
            self._save(conv.id, "assistant", reply, intent_name, {"action": action})
            conv.status = "escalated"
            self.db.commit()
            return self._result(conv, intent_name, action, reply, True, esc_id, disclosure)

        # Fallback counting → escalate rather than loop forever.
        if intent_name == "fallback":
            fb = (conv.meta_json or {}).get("consecutive_fallbacks", 0) + 1
            conv.meta_json = {**(conv.meta_json or {}), "consecutive_fallbacks": fb}
            if fb >= MAX_FALLBACKS_BEFORE_ESCALATION:
                esc = self._escalate(business, conv, "low_confidence",
                                     t("escalation_brief_low_confidence", lang, n=fb),
                                     tried, t("needs_decision_default", lang), lang)
                reply = t("escalation_fallback", lang, owner_word=t("owner_word", lang))
                intent_name = "escalated_low_confidence"
                self._save(conv.id, "assistant", reply, intent_name, {"action": action})
                conv.status = "escalated"
                conv.meta_json = {**(conv.meta_json or {}), "consecutive_fallbacks": 0,
                                  "ai_tried": tried}
                self.db.commit()
                return self._result(conv, intent_name, action, reply, True, esc.id, disclosure)
        else:
            conv.meta_json = {**(conv.meta_json or {}), "consecutive_fallbacks": 0}

        conv.meta_json = {**(conv.meta_json or {}), "ai_tried": tried}
        self._save(conv.id, "assistant", reply, intent_name, {"action": action})
        self.db.commit()
        return self._result(conv, intent_name, action, reply, False, None, disclosure)

    # ── dispatch ──────────────────────────────────────────────────
    # The brain: every request resolves to exactly one ACTION, and the
    # action assembles its answer STRICTLY from the shop's structured
    # data (catalog, customization options, delivery zones, orders).
    # Anything the data can't answer becomes a graceful escalation with
    # the novel request captured verbatim for the owner.
    def _dispatch(self, business, conv, customer, detected, action, text, lang, tried):
        name = detected.name
        if action == "greeting":
            n = customer.name if customer and customer.name else ""
            greeted_before = (conv.meta_json or {}).get("greeted")
            key = "greeting_returning" if greeted_before else "greeting"
            conv.meta_json = {**(conv.meta_json or {}), "greeted": True}
            return t(key, lang, comma_name=f", {n}" if n else ""), "greeting", {}
        if action == "knowledge":
            return self._knowledge(business, conv, text, lang, tried)
        if action == "catalog_check":
            items = intents.catalog_search(self.db, business.id, detected.slots.get("query", text))
            tried.append(t("ai_tried_catalog", lang, q=detected.slots.get("query", text)))
            if not items:
                return self._knowledge(business, conv, text, lang, tried)
            lines = [f"• {i.name} — {intents.format_money(i.price, i.currency)}"
                     + ("" if i.in_stock else f" ({t('catalog_out_of_stock', lang)})") for i in items]
            reply = f"{t('catalog_intro', lang)}\n" + "\n".join(lines)
            return reply, "catalog", {}
        if action == "customization":
            return self._customization(business, conv, text, lang, tried)
        if action == "price_quote":
            return self._price_quote(business, conv, detected, text, lang, tried)
        if action == "delivery_check":
            return self._delivery_check(business, conv, detected, text, lang, tried)
        if action == "order":
            if name == "booking":
                return self._start_booking(business, conv, text, lang, tried)
            if name == "booking_cancel":
                return self._cancel_booking(business, conv, customer, text, lang, tried)
            return self._payment_status(business, conv, customer, detected.slots, text, lang, tried)
        if action == "escalate":
            esc = self._escalate(business, conv, "human_requested",
                                 t("escalation_brief_human", lang), tried,
                                 t("needs_decision_default", lang), lang)
            return (t("escalation_human_requested", lang, owner_word=t("owner_word", lang)),
                    "escalated_human", {"escalated": True, "escalation_id": esc.id})
        return self._knowledge(business, conv, text, lang, tried)

    # ── knowledge: learned first, FAQ second, graceful escalation ──
    def _knowledge(self, business, conv, text, lang, tried):
        learned = intents.learned_search(self.db, business.id, text)
        if learned:
            learned.use_count += 1
            tried.append("Answered from this shop's learned knowledge")
            return learned.answer, "knowledge_learned", {}
        faq = intents.faq_search(self.db, business.id, text)
        if faq:
            tried.append(t("ai_tried_faq", lang, q=faq.question))
            return f"{t('faq_source_note', lang)}\n{faq.answer}", "knowledge", {}
        # Graceful escalation — the novel request goes to the owner verbatim,
        # and their answer will teach the shop's AI next time.
        esc = self._escalate(business, conv, "novel_request",
                             t("escalation_brief_novel", lang, verbatim=text[:300]),
                             tried, t("needs_decision_default", lang), lang)
        return (t("escalation_novel", lang, owner_word=t("owner_word", lang)),
                "escalated_novel", {"escalated": True, "escalation_id": esc.id})

    # ── customization: strictly from CustomizationOption rows ──────
    def _customization(self, business, conv, text, lang, tried):
        from .models import CustomizationOption
        options = self.db.scalars(select(CustomizationOption).where(
            CustomizationOption.business_id == business.id)).all()
        tried.append("Checked the shop's customization options")
        if not options:
            esc = self._escalate(business, conv, "customization_request",
                                 t("escalation_brief_novel", lang, verbatim=text[:300]),
                                 tried, t("needs_decision_default", lang), lang)
            return (t("customization_none", lang, owner_word=t("owner_word", lang)),
                    "escalated_custom", {"escalated": True, "escalation_id": esc.id})
        items = intents.catalog_search(self.db, business.id, text, limit=1)
        cat = items[0].category if items else ""
        # No item identified → show everything; the customer picks.
        relevant = ([o for o in options if o.applies_to in ("all", cat)]
                    if cat else list(options)) or options
        lines = [t("customization_opt_line", lang, name=o.name,
                    money=intents.format_money(o.price_delta, business.currency),
                    desc=o.description) for o in relevant]
        return f"{t('customization_intro', lang)}\n" + "\n".join(lines), "customization", {}

    # ── price quote: item prices + option deltas, never invented ───
    def _price_quote(self, business, conv, detected, text, lang, tried):
        # Approval-gated topics never get a quote from the AI alone.
        if detected.name == "sensitive":
            topic = detected.slots.get("topic", "")
            if topic in (business.require_approval_for or []):
                esc = self._escalate(
                    business, conv, "approval_needed",
                    t("escalation_brief_approval", lang, topic=topic), tried,
                    t("needs_decision_approval", lang, topic=topic), lang,
                    context={"topic": topic, "customer_message": text})
                return (t("escalation_approval_needed", lang, owner_word=t("owner_word", lang)),
                        "escalated_approval", {"escalated": True, "escalation_id": esc.id})
            tried.append(f"Discussed {topic} (not approval-gated)")
            return (f"For {topic} requests I loop in {t('owner_word', lang)} so you get the right answer. "
                    f"{t('escalation_human_requested', lang, owner_word=t('owner_word', lang))}",
                    "price_quote", {})
        items = intents.catalog_search(self.db, business.id, detected.slots.get("query", text), limit=3)
        tried.append(t("ai_tried_catalog", lang, q=detected.slots.get("query", text)))
        if not items:
            return self._knowledge(business, conv, text, lang, tried)
        from .models import CustomizationOption
        options = self.db.scalars(select(CustomizationOption).where(
            CustomizationOption.business_id == business.id)).all()
        lines = [t("price_quote_line", lang, name=i.name,
                    money=intents.format_money(i.price, i.currency)) for i in items]
        reply = "\n".join(lines)
        relevant = [o for o in options if any(o.applies_to in ("all", i.category) for i in items)]
        if relevant:
            reply += "\n" + t("price_quote_custom_note", lang) + "\n" + "\n".join(
                t("customization_opt_line", lang, name=o.name,
                  money=intents.format_money(o.price_delta, business.currency),
                  desc=o.description) for o in relevant)
        return reply, "price_quote", {}

    # ── delivery check: strictly from DeliveryZone rows ────────────
    def _delivery_check(self, business, conv, detected, text, lang, tried):
        import re as _re
        from .models import DeliveryZone
        zones = self.db.scalars(select(DeliveryZone).where(
            DeliveryZone.business_id == business.id)).all()
        tried.append("Checked the shop's delivery zones")
        pincode = detected.slots.get("pincode", "")
        if not pincode:
            m = _re.search(r"\b(\d{6})\b", text)
            pincode = m.group(1) if m else ""
        if not zones:
            esc = self._escalate(business, conv, "delivery_unknown",
                                 t("escalation_brief_novel", lang, verbatim=text[:300]),
                                 tried, t("needs_decision_default", lang), lang)
            return (t("delivery_no_data", lang, owner_word=t("owner_word", lang)),
                    "escalated_delivery", {"escalated": True, "escalation_id": esc.id})
        if not pincode:
            return t("delivery_ask_pincode", lang), "delivery_check", {}
        zone = next((z for z in zones
                     if any(pincode.startswith(p) for p in (z.pincode_prefixes or []))), None)
        if zone is None:  # catch-all zone (empty prefixes) or nothing
            zone = next((z for z in zones if not (z.pincode_prefixes or [])), None)
        if zone is None:
            esc = self._escalate(business, conv, "delivery_unserviceable",
                                 t("escalation_brief_novel", lang, verbatim=text[:300]),
                                 tried, t("needs_decision_default", lang), lang)
            return (t("delivery_no_zone", lang, pincode=pincode,
                      owner_word=t("owner_word", lang)),
                    "escalated_delivery", {"escalated": True, "escalation_id": esc.id})
        cod = t("delivery_cod_yes", lang) if zone.cod_available else ""
        return (t("delivery_found", lang, pincode=pincode, zone=zone.name,
                  fee=intents.format_money(zone.fee, business.currency),
                  days=f"{zone.days_min}-{zone.days_max}", cod=cod),
                "delivery_check", {})

    # ── booking (multi-turn) ──────────────────────────────────────
    def _start_booking(self, business, conv, text, lang, tried):
        day = intents.parse_date(text)
        tm = intents.parse_time(text)
        data: dict = {}
        if day:
            data["date"] = day.isoformat()
        if tm:
            data["time"] = tm.strftime("%H:%M")
        conv.meta_json = {**(conv.meta_json or {}), "pending": {"intent": "booking", "data": data}}
        return self._continue_booking(business, conv, data, text, lang, tried)

    def _continue_pending(self, business, conv, customer, pending, text, lang, tried):
        if pending.get("intent") == "booking":
            data = pending.get("data", {})
            if not data.get("date"):
                d = intents.parse_date(text)
                if d:
                    data["date"] = d.isoformat()
            if not data.get("time"):
                tm = intents.parse_time(text)
                if tm:
                    data["time"] = tm.strftime("%H:%M")
            if not data.get("name") and customer and customer.name:
                data["name"] = customer.name
            elif not data.get("name") and len(text.strip().split()) <= 3 and "@" not in text and not any(
                    ch.isdigit() for ch in text):
                # looks like a name answer
                if "date" in data:  # only treat as name once we're past date/time
                    data["name"] = text.strip()
            if not data.get("phone"):
                m = __import__("re").search(r"(\+?\d[\d\s-]{7,}\d)", text)
                if m:
                    data["phone"] = m.group(1).strip()
            elif not data.get("phone") and customer and customer.phone:
                data["phone"] = customer.phone
            meta = dict(conv.meta_json or {})
            meta["pending"] = {"intent": "booking", "data": data}
            conv.meta_json = meta
            return self._continue_booking(business, conv, data, text, lang, tried)
        return t("fallback", lang, owner_word=t("owner_word", lang)), "fallback", {}

    def _continue_booking(self, business, conv, data, text, lang, tried):
        if not data.get("date"):
            return t("booking_ask_when", lang), "booking", {}
        from datetime import date as _date
        day = _date.fromisoformat(data["date"])
        tried.append(t("ai_tried_booking", lang, date=day.isoformat()))
        slots = intents.free_slots(self.db, business, day)
        if not slots:
            alts = []
            for i in range(1, 4):
                s = intents.free_slots(self.db, business, day + __import__("datetime").timedelta(days=i))
                if s:
                    alts.append(f"{s[0][0].strftime('%a %d %b %H:%M')}")
                    if len(alts) == 3:
                        break
            conv.meta_json = {k: v for k, v in (conv.meta_json or {}).items() if k != "pending"}
            return (t("booking_no_slots", lang, date=day.isoformat(), alts=", ".join(alts) or "—"),
                    "booking", {})
        if not data.get("time"):
            shown = ", ".join(s[0].strftime("%H:%M") for s in slots[:6])
            conv.meta_json = {**(conv.meta_json or {}), "pending": {"intent": "booking", "data": data}}
            return f"{day.strftime('%a %d %b')} — free: {shown}. Which time works?", "booking", {}
        # create the booking
        hh, mm = map(int, data["time"].split(":"))
        starts = datetime.combine(day, __import__("datetime").time(hh, mm))
        # re-check the slot is still free
        if all(s[0].replace(tzinfo=None) != starts.replace(tzinfo=None) for s in slots):
            return "That slot just got taken — pick another from the list above.", "booking", {}
        name = data.get("name") or ""
        phone = data.get("phone") or ""
        if not name:
            return t("booking_ask_name", lang), "booking", {}
        if not phone:
            return t("booking_ask_phone", lang), "booking", {}
        booking = Booking(business_id=business.id, service=data.get("service", ""),
                          customer_name=name, customer_phone=phone,
                          starts_at=starts,
                          ends_at=starts + __import__("datetime").timedelta(minutes=business.slot_minutes or 30))
        self.db.add(booking)
        conv.meta_json = {k: v for k, v in (conv.meta_json or {}).items() if k != "pending"}
        when = starts.strftime("%a %d %b, %H:%M")
        return (t("booking_confirmed", lang, service=data.get("service") or "your visit", when=when),
                "booking_confirmed", {})

    def _cancel_booking(self, business, conv, customer, text, lang, tried):
        phone = (customer.phone if customer else "") or ""
        m = __import__("re").search(r"(\+?\d[\d\s-]{7,}\d)", text)
        if m:
            phone = m.group(1).strip()
        if not phone:
            return "Which phone number is the booking under?", "booking_cancel", {}
        b = self.db.scalars(select(Booking).where(
            Booking.business_id == business.id, Booking.customer_phone == phone,
            Booking.status == "confirmed").order_by(Booking.starts_at)).first()
        if not b:
            return t("booking_not_found", lang), "booking_cancel", {}
        b.status = "cancelled"
        when = b.starts_at.strftime("%a %d %b, %H:%M")
        return t("booking_cancelled", lang, when=when), "booking_cancelled", {}

    # ── payments ──────────────────────────────────────────────────
    def _payment_status(self, business, conv, customer, slots, text, lang, tried):
        from . import payments as pay
        phone = (customer.phone if customer else "") or ""

        # (1) The customer *reports* a payment — match it instantly.
        if slots.get("is_claim"):
            ref = slots.get("ref", "")
            amount = slots.get("amount_minor") or 0
            order_id = (slots.get("order_id") or "").upper()
            if not ref and not order_id and not amount:
                return ("Got it — what's the UPI ref / UTR number for that payment? "
                        "That lets me confirm your order right away."), "payment", {}
            outcome = pay.ingest_payment(
                self.db, business, amount=amount, currency=business.currency,
                order_id=order_id, phone=phone, provider_ref=ref,
                source="customer_chat", raw={"text": text}, lang=lang,
                strict_amount=bool(amount))
            tried.append(t("ai_tried_payment_match", lang, ref=ref or "—",
                           order_id=outcome.order.order_id if outcome.order else "—"))
            if outcome.owner_problem:
                # Problems ping the owner; the customer already has an honest reply.
                esc = self._escalate(business, conv, "payment_problem",
                                     t("escalation_brief_payment", lang, detail=outcome.owner_problem),
                                     tried,
                                     t("needs_decision_payment", lang, detail=outcome.owner_problem),
                                     lang, context={"payment_id": outcome.payment.id})
                return (outcome.customer_message, "escalated_payment",
                        {"escalated": True, "escalation_id": esc.id})
            return outcome.customer_message, "payment_confirmed", {}

        # (2) Status check — read the order state, never invent it.
        order_id = (slots.get("order_id") or "").upper()
        if not order_id:
            m = __import__("re").search(r"(\+?\d[\d\s-]{7,}\d)", text)
            if m:
                phone = m.group(1).strip()
        order = pay.find_order(self.db, business.id, order_id=order_id, phone=phone)
        if order_id:
            tried.append(t("ai_tried_payment", lang, order_id=order_id))
        if not order:
            return t("payment_not_found", lang, owner_word=t("owner_word", lang)), "payment", {}
        money = intents.format_money(order.amount, order.currency)
        if order.status == "confirmed":
            return (t("payment_order_confirmed", lang, order_id=order.order_id, money=money),
                    "payment", {})
        return (t("payment_order_pending", lang, order_id=order.order_id, money=money),
                "payment", {})

    # ── escalation ────────────────────────────────────────────────
    def _escalate(self, business, conv, reason, summary, tried, needs_decision, lang, context=None) -> Escalation:
        # Graceful escalation captures the novel request VERBATIM — the
        # owner sees exactly what the customer said, and their answer
        # teaches this shop's AI (per-shop learning).
        ctx = dict(context or {})
        if "verbatim_request" not in ctx:
            last = self.db.scalars(select(Message).where(
                Message.conversation_id == conv.id, Message.role == "customer")
                .order_by(Message.id.desc()).limit(1)).first()
            ctx["verbatim_request"] = (last.text[:1000] if last else "")
        esc = Escalation(business_id=business.id, conversation_id=conv.id, reason=reason,
                         brief_summary=summary, ai_tried=list(tried),
                         needs_decision=needs_decision, context_json=ctx)
        self.db.add(esc)
        self.db.flush()
        return esc

    def _open_escalation(self, conversation_id: int) -> Escalation | None:
        return self.db.scalars(select(Escalation).where(
            Escalation.conversation_id == conversation_id,
            Escalation.status.in_(["open", "claimed"]))).first()

    # ── reply composition ─────────────────────────────────────────
    async def apolish(self, decided_text: str, business, conv, lang: str) -> str:
        """Best-effort LLM rewording. The model may ONLY use facts already
        in the draft — never invent prices, items, timings, delivery info,
        or policies. If the draft defers to the owner, the rewrite keeps
        that meaning."""
        if not self.llm:
            return decided_text
        history = self.db.scalars(select(Message).where(
            Message.conversation_id == conv.id).order_by(Message.id.desc()).limit(6)).all()
        msgs = [LLMMessage(role="user" if m.role == "customer" else "assistant", content=m.text)
                for m in reversed(history)]
        system = (f"You are {business.bot_name}, the AI receptionist for {business.name}. "
                  f"Rewrite the draft reply below in a warm, concise tone ({lang}). "
                  f"STRICT RULES: use ONLY facts present in the draft or the conversation history. "
                  f"Never invent or guess prices, products, timings, delivery details, or policies. "
                  f"If the draft says it doesn't know something or will ask the owner, keep that meaning. "
                  f"Draft: {decided_text}")
        try:
            return await self.llm.complete(system=system, messages=msgs)
        except Exception:
            return decided_text

    # ── helpers ───────────────────────────────────────────────────
    def _result(self, conv, intent, action, reply, escalated, escalation_id,
                disclosure: str | None = None) -> dict:
        return {"reply": reply, "intent": intent, "action": action, "conversation_id": conv.id,
                "demo_mode": self.settings.demo_mode, "escalated": escalated,
                "escalation_id": escalation_id, "disclosure": disclosure}

    def _business(self, slug: str) -> Business:
        b = self.db.scalars(select(Business).where(Business.slug == slug)).first()
        if not b:
            raise ValueError(f"Unknown business: {slug}")
        return b

    def _conversation(self, business, channel, external_id, name, phone, lang,
                      conversation_id: int | None = None):
        # Resume an explicit thread first (web widget passes conversation_id back).
        if conversation_id:
            conv = self.db.get(Conversation, conversation_id)
            if conv and conv.business_id == business.id:
                customer = self.db.get(Customer, conv.customer_id) if conv.customer_id else None
                return conv, customer, None
        customer = None
        if external_id:
            customer = self.db.scalars(select(Customer).where(
                Customer.business_id == business.id, Customer.external_id == external_id)).first()
        if not customer and phone:
            customer = self.db.scalars(select(Customer).where(
                Customer.business_id == business.id, Customer.phone == phone)).first()
        if not customer:
            customer = Customer(business_id=business.id, name=name, phone=phone,
                                channel=channel, external_id=external_id or f"web-{datetime.now().timestamp()}")
            self.db.add(customer)
            self.db.flush()
        else:
            if name and not customer.name:
                customer.name = name
            if phone and not customer.phone:
                customer.phone = phone
        conv = None
        if external_id:
            conv = self.db.scalars(select(Conversation).where(
                Conversation.business_id == business.id, Conversation.external_id == external_id,
                Conversation.status != "resolved")).first()
        if not conv:
            conv = Conversation(business_id=business.id, customer_id=customer.id, channel=channel,
                                external_id=external_id or f"web-{customer.id}-{datetime.now().timestamp()}",
                                language=lang, demo_mode=self.settings.demo_mode)
            self.db.add(conv)
            self.db.flush()
            # Trust-by-design: every new conversation opens with bot disclosure.
            # The text is returned to the caller too, so customer-facing
            # surfaces (embed widget, WhatsApp) show it on the first reply.
            disclosure = t("bot_disclosure", lang, bot_name=business.bot_name,
                           business=business.name)
            self._save(conv.id, "assistant", disclosure, "disclosure")
            return conv, customer, disclosure
        return conv, customer, None

    def _save(self, conversation_id: int, role: str, text: str, intent: str,
              meta: dict | None = None):
        self.db.add(Message(conversation_id=conversation_id, role=role, text=text,
                            intent=intent, meta_json=meta or {}))
        self.db.flush()

# FrontDesk — the AI front desk for small businesses

One AI receptionist that answers customers on web chat and WhatsApp: FAQs, catalog,
bookings, and **payment ↔ order matching** — with an owner escalation inbox where every
handoff arrives as a crisp brief. Single-tenant friendly, vernacular-ready, trust-by-design.

> **Demo mode:** with no `LLM_API_KEY` set, the engine runs on deterministic templates and
> every chat response carries `demo_mode: true` (plus a "DEMO AI" badge in the UI).
> Add any OpenAI-compatible key and the same engine gets LLM-polished wording.

## The payment model (read this first)

The owner **already gets UPI notifications** — so FrontDesk never "tells the owner a payment
arrived." Its job is:

1. **Matching** — every incoming payment (gateway webhook, staff entry, or a UPI ref / UTR
   the customer typed in chat) is matched to the correct order and its catalog items.
2. **Instant customer confirmation** — the customer gets
   *"Payment received ✓ Order #ORD-1003 confirmed"* immediately, without waiting on the owner.
3. **Order state rule** — an order starts as `pending_payment` and flips to `confirmed`
   **only** when a payment is verified by the matching engine.
4. **Owner ledger** — a clean paid/pending view per order at `GET /api/owner/ledger`
   (also the **Ledger** tab in the dashboard). The owner is pinged **only on problems**:
   wrong amount, no matching order, or a duplicate payment.

Chat claims (`"I paid ₹1299 for ORD-1002, UTR 987654321098"`) confirm against the order the
customer named; gateway webhooks (`source=webhook`) additionally enforce the amount match.
Every payment row records its `source`, so the ledger always shows *how* a payment was verified.

## Quickstart

```bash
cp .env.example .env          # optional: add LLM_API_KEY to leave demo mode
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --reload
```

- Landing page: http://127.0.0.1:8000/
- Owner dashboard: http://127.0.0.1:8000/dashboard (token in `.env` → `OWNER_TOKEN`)
- Embeddable shop chat: http://127.0.0.1:8000/embed/demo
- API docs: http://127.0.0.1:8000/docs

The demo seeds **Demo Boutique** (jewellery): 6 catalog items, 4 FAQs, business hours,
`ORD-1001` (paid ✓ confirmed) and `ORD-1002` (waiting on payment — try paying for it in the demo chat).

## Architecture

```
app/
  main.py          FastAPI app: creates tables, seeds demo, mounts routers + /static
  config.py        pydantic-settings — every knob is an env var (see .env.example)
  db.py            SQLAlchemy 2.x engine/session; SQLite dev, Postgres via DATABASE_URL
  models.py        Business, Customer, Conversation, Message, CatalogItem, Faq,
                   Booking, Order, PaymentRecord, Escalation
  engine.py        ChatEngine — the conversation loop, escalation briefs, trust rules
  intents.py       Tool-calling style intent detection + tools (FAQ/catalog/booking/payment)
  payments.py      Payment ↔ order matching engine + instant customer confirmation
  learn.py           Per-shop learning: owner answers → shop knowledge (the moat)
  notify.py          WhatsApp delivery (config-gated; logs when unconfigured)
  stt.py             Pluggable speech-to-text for WhatsApp voice notes
  telephony.py       Live-telephony hooks (premium roadmap module — stubs only)
  llm.py           Pluggable LLM provider (OpenAI-compatible) — None => demo mode
  i18n.py          JSON dictionaries, English fallback (i18n/en.json complete,
                   i18n/hi.json scaffolded — add i18n/<code>.json for more)
  seed.py          Demo business seed (idempotent)
  routers/
    chat.py        POST /api/chat — the AI receptionist endpoint
    whatsapp.py    GET/POST /api/whatsapp/webhook — verify handshake + receiver
    catalog.py     Catalog CRUD (reads public, writes owner-gated)
    bookings.py    Slot listing, booking create/lookup/cancel
    payments.py    POST /api/orders, GET /api/orders, POST /api/payments/incoming,
                   POST /api/payments/status, GET /api/owner/ledger
    owner.py       Escalation inbox (claim/resolve/reply-in-thread), conversations,
                   plain-language analytics, settings, 5-step setup wizard, /api/health
    pages.py       Landing page, owner dashboard, embeddable chat page
templates/         landing.html, dashboard.html, embed.html (Jinja2)
static/            css/site.css (design system), js/dashboard.js, js/widget.js
```

### The chat loop: intent → action (not FAQ caching)

`POST /api/chat` → `ChatEngine.handle()`:
1. Load/create business, customer, conversation (resumes `conversation_id`).
2. New threads open with a **bot disclosure** message (trust-by-design).
3. If an escalation is open on the thread, the AI holds the line and appends context —
   **the conversation never resets on handoff**.
4. **Intent → action**: every request resolves to exactly one action
   (`knowledge · catalog_check · customization · price_quote · delivery_check · order · escalate` —
   see `intents.resolve_action()`). The action assembles its answer **strictly from the shop's
   structured data** (catalog, customization options, delivery zones, orders) — never invented.
5. Anything the data can't answer becomes a **graceful escalation**: the novel request is
   captured **verbatim** for the owner, with what the AI tried and what the human must decide.
6. Sensitive intents (`discount`, `refund` — configurable per business) create an approval
   escalation instead of answering. Two low-confidence turns in a row also escalate.
7. An LLM (when configured) only rewords the decided reply under a strict
   use-only-these-facts contract; in demo mode the deterministic templates stand as-is.
   Every response carries its `action` for full visibility.

### Voice: async-first

**WhatsApp voice notes work today** — no telephony cost: the note is downloaded,
transcribed via pluggable STT (`STT_API_KEY`, any OpenAI-compatible endpoint —
Sarvam AI recommended for Telugu/Hindi), and the transcript runs through the same
engine. Without an STT key the customer gets an honest "please type it" reply.
**Live telephony is a premium roadmap module, not built** — clean hooks live in
`app/telephony.py`, and `/api/telephony/*` answers 501 until then.
See [ROADMAP.md](ROADMAP.md) for the ₹2–3/min cost target and the levers.

### Per-shop learning: the compounding moat

Every escalation the owner answers (thread reply or substantive resolution note) is
logged back into **that shop's** knowledge base. The knowledge action searches learned
answers **before** the static FAQ — so the AI improves per business automatically.
Inspect at `GET /api/owner/knowledge`. Learning is scoped by `business_id`; one shop's
knowledge never leaks into another's. Full write-up in [ROADMAP.md](ROADMAP.md).

### WhatsApp & voice

Code-complete and **config-gated**: with no `WHATSAPP_*` keys, the verify endpoint returns
`404 {detail: "WhatsApp not configured"}` and the receiver acknowledges without acting.
Add the three keys and the same engine answers WhatsApp messages — **including voice notes**,
which are transcribed (see `STT_API_KEY`) and answered as text. Zero telephony cost.
Live phone calls are a future premium module: `/api/telephony/*` answers 501 until then
(see [ROADMAP.md](ROADMAP.md)).

## What needs your keys to go live

| Key(s) | Unlocks |
|---|---|
| `LLM_API_KEY` (+`LLM_BASE_URL`, `LLM_MODEL`) | Full LLM wording via any OpenAI-compatible API; without it you get labeled demo mode |
| `WHATSAPP_VERIFY_TOKEN`, `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID` | WhatsApp Cloud API webhook + replies (from developers.facebook.com) |
| `STT_API_KEY` (+`STT_BASE_URL`, `STT_MODEL`) | WhatsApp voice-note transcription (Whisper, Sarvam AI for Telugu/Hindi); without it voice notes get an honest "please type it" |
| `PAYMENT_WEBHOOK_SECRET` | Authenticates your payment gateway's webhook calls to `/api/payments/incoming` |
| `OWNER_TOKEN` | Dashboard auth — change from the dev default before anything public |
| `DATABASE_URL` | `postgresql+psycopg://…` for Postgres (SQLite file by default); then `alembic upgrade head` |

### Wiring a real payment gateway

Point your gateway's webhook (Razorpay / Stripe / your UPI provider) at:

```
POST https://YOUR-HOST/api/payments/incoming
{
  "business": "demo",
  "amount": 129900,
  "currency": "INR",
  "order_id": "ORD-1002",
  "phone": "+91 90000 22222",
  "provider_ref": "pay_N8aBcDeFgH",
  "source": "webhook"
}
```

The engine matches it, flips the order to `confirmed`, confirms the customer instantly
(in-thread + WhatsApp when configured), and stays silent toward the owner unless there's a
problem. Map your gateway's payload to these fields in a tiny adapter if its shape differs.

## i18n

`app/i18n.py` loads `i18n/<locale>.json`; any missing key falls back to English.
`en.json` is complete; `hi.json` scaffolds Hindi (customer-facing strings translated).
Add a locale by copying the key set — e.g. `hi-latn.json` for Hinglish. Set per business
via the setup wizard or dashboard settings.

## Database & migrations

Dev: `Base.metadata.create_all()` on boot (SQLite file `./frontdesk.db`).
Prod/Postgres: set `DATABASE_URL`, then:

```bash
alembic revision --autogenerate -m "init"
alembic upgrade head
```

## Docker

```bash
docker build -t frontdesk .
docker run -p 8000:8000 -v frontdesk-data:/app/data \
  -e OWNER_TOKEN=your-long-random-token frontdesk
```

## Tests

```bash
.venv/bin/python -m pytest tests/ -q
```

Smoke tests cover: chat intents (greeting/disclosure, FAQ, catalog, booking multi-turn,
payment claim + matching, status checks), escalation briefs, owner claim/reply/resolve,
order confirmation on webhook ingest, wrong-amount/duplicate/unmatched problems, ledger,
catalog CRUD, slots, setup wizard, and WhatsApp gating.

## Security notes

- Owner endpoints require `X-Owner-Token`; never ship the dev default.
- No secrets are committed — only `.env.example` (see `.gitignore`).
- The app never moves money; it only matches and reports payment state.
- Chat payment claims are customer-reported; gateway webhooks are authoritative —
  the ledger records the `source` of every payment.

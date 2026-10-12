# FrontDesk Roadmap — voice, brain & the compounding moat

How this product wins: not by being the smartest chatbot, but by being the
most *trustworthy* front desk for unpredictable, real-world customers —
and by getting smarter for each shop every week, automatically.

## 1. Voice: async-first (founder directive)

**Live phone calls are NOT the starting point.** Telephony costs money per
minute, needs call infrastructure, and fails noisily. The async path
already reaches every customer and costs nothing extra:

### Phase 1 — NOW (shipped in this codebase)
- **WhatsApp voice notes**: customer sends a voice note → we download it →
  transcribe via pluggable STT (`STT_API_KEY`, any OpenAI-compatible
  `/audio/transcriptions` endpoint) → the transcript runs through the
  exact same engine as text → reply goes back as text.
- No STT key? The customer gets an honest *"I can't listen yet — please
  type it"* and the owner sees it was a voice note. Nothing silently dropped.
- Zero telephony cost. Zero new infrastructure. Works on the phones
  customers already use.

### Phase 2 — LATER (premium module, hooks only)
- **Live telephony** as a paid add-on: inbound AI receptionist calls and
  outbound call-backs, via Exotel/Plivo adapters implementing
  `app/telephony.py::TelephonyProvider`.
- Until then, `/api/telephony/*` answers **501** with a pointer here.
  The engine never changes: a call is just another channel feeding
  transcripts into `ChatEngine.handle()`.

### The ₹2–3/min live-voice cost target
Live voice only ships as a premium module if a minute of talk stays
around **₹2–3**. The levers, in order of impact:
1. **Indian telephony (Exotel / Plivo)** — domestic termination is an
   order of magnitude cheaper than US-centric voice APIs.
2. **Sarvam AI for Telugu/Hindi STT+TTS** — Indian-language speech models
   priced for this market, not a Silicon Valley markup.
3. **Small models for the brain** — the intent→action map (below) is
   deliberately small-model friendly; a 7B-class model is plenty when the
   answers come from structured data, not reasoning.
4. **Concise responses** — every extra spoken sentence is money. Replies
   are capped short; details go to WhatsApp text follow-ups (free).

## 2. The brain: intent → action, not FAQ caching (founder directive)

Designed for **unpredictable rural customers**, not for demo-day FAQs.
Cheap small models can run it because the intelligence is in the
*structure*, not in eloquence:

```
customer message
      │  detect_intent()  (cheap: keywords/regex today, small model tomorrow)
      ▼
┌─────────────────────────────────────────────────────────┐
│ ACTION  (exactly one — intents.resolve_action())        │
│  knowledge · catalog_check · customization · price_quote │
│  delivery_check · order · escalate                       │
└─────────────────────────────────────────────────────────┘
      │  each action reads ONLY the shop's structured data
      ▼
  catalog + customization options + delivery zones + orders
      │  no row matches?
      ▼
  graceful escalation — novel request captured VERBATIM for the owner
```

Rules the engine enforces today:
- **Answers are assembled strictly from structured data.** The demo
  composer uses templates over tool results; the LLM polish prompt is
  contractually forbidden from inventing prices, items, timings, delivery
  info, or policies ("use ONLY facts present in the draft").
- **What the data can't answer, the owner answers** — the escalation
  carries `verbatim_request` (the customer's exact words), what the AI
  tried, and what the human must decide. The thread never resets.
- **Sensitive actions never auto-resolve**: discounts/refunds (per-shop
  configurable) always route to the owner with a brief.

## 3. Per-shop learning: the compounding moat

Every escalation the owner answers is logged back into **that shop's**
knowledge base (`LearnedKnowledge`):

- Owner replies in the thread → answer stored with the verbatim question.
- Owner resolves with a substantive note → same.
- The knowledge action searches **learned rows before the static FAQ**,
  ranked by keyword overlap then `use_count`.

Why it's a moat: the tenth customer asking about gift wrapping gets the
owner's own words from the first time they asked — automatically, per
shop, with zero configuration. A competitor can copy the software; they
can't copy six months of a specific shop's learned answers. Learning is
strictly scoped by `business_id`: one shop's knowledge never leaks into
another's. Owners can inspect and delete entries at
`GET/DELETE /api/owner/knowledge`.

## 4. What exists now vs. later

| Now (this repo) | Later (roadmap) |
|---|---|
| WhatsApp voice notes (STT-gated) | Live telephony premium module (Exotel/Plivo) |
| Intent→action brain, 7 actions | Small-model intent classifier replacing regex |
| Structured data: catalog, customizations, delivery zones, orders | More structured sources (staff roster, live inventory counts) |
| Per-shop learning from owner answers | Learning analytics ("what your shop taught its AI this month") |
| Demo-mode templates / pluggable LLM | Sarvam AI as default STT+TTS for Telugu/Hindi |
| Single-tenant SQLite → Postgres | Multi-shop hosted tier with flat pricing |

## 5. Non-goals (explicit)

- No live telephony until the ₹2–3/min math works.
- No FAQ-caching architecture: novel questions escalate, then teach.
- The AI never "notifies the owner a payment arrived" — matching,
  instant customer confirmation, and the ledger are the payment story.
- No invented facts, ever: if the structured data doesn't have it,
  the owner does.

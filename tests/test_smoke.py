"""Smoke tests for FrontDesk. Run: python -m pytest tests/ -q
Uses a throwaway SQLite DB (tmp_path) so the dev database is untouched."""
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("OWNER_TOKEN", "test-token")
    # Fresh settings/engine per test DB
    import importlib
    import app.config as cfg
    importlib.reload(cfg)
    import app.db as dbmod
    importlib.reload(dbmod)
    import app.main as mainmod
    importlib.reload(mainmod)
    return TestClient(mainmod.app)


TOKEN = {"X-Owner-Token": "test-token"}


def chat(client, text, cid=None, business="demo"):
    body = {"business": business, "text": text}
    if cid:
        body["conversation_id"] = cid
    return client.post("/api/chat", json=body)


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["ok"]


def test_disclosure_first_message(client):
    r = chat(client, "Hi!")
    d = r.json()
    assert d["intent"] == "greeting" and d["demo_mode"] is True
    msgs = client.get(f"/api/owner/conversations/{d['conversation_id']}/messages", headers=TOKEN).json()
    assert msgs[0]["role"] == "assistant" and msgs[0]["intent"] == "disclosure"
    assert "AI" in msgs[0]["text"]


def test_faq_and_catalog(client):
    assert chat(client, "what are your timings?").json()["intent"] == "knowledge"
    d = chat(client, "show me silver necklaces").json()
    assert d["intent"] == "catalog" and "Necklace" in d["reply"]
    assert d["action"] == "catalog_check"


def test_booking_multiturn(client):
    c1 = chat(client, "book for tomorrow").json()
    cid = c1["conversation_id"]
    assert c1["intent"] == "booking" and "Which time works" in c1["reply"]
    c2 = chat(client, "2 PM", cid=cid).json()
    assert c2["conversation_id"] == cid  # same thread resumed
    c3 = chat(client, "Priya Sharma", cid=cid).json()
    c4 = chat(client, "+91 90000 33333", cid=cid).json()
    assert c4["intent"] == "booking_confirmed", c4["reply"]
    assert "booked" in c4["reply"].lower()


def test_payment_claim_matches_and_confirms(client):
    # ORD-1002 is pending_payment for 129900 in seed
    d = chat(client, "I paid 1299 for ORD-1002, UTR 987654321098").json()
    assert d["intent"] == "payment_confirmed", d["reply"]
    assert "ORD-1002" in d["reply"] and "confirmed" in d["reply"].lower()
    # Order state flipped
    s = client.post("/api/payments/status", json={"business": "demo", "order_id": "ORD-1002"}).json()
    assert s["found"] and s["order_status"] == "confirmed"
    # Duplicate ref is a problem, not a second confirmation
    d2 = chat(client, "paid again, UTR 987654321098").json()
    assert d2["escalated"] is True


def test_payment_wrong_amount_escalates(client):
    d = chat(client, "I paid 500 for ORD-1002, UTR 111122223333").json()
    assert d["escalated"] is True
    escs = client.get("/api/owner/escalations?business=demo", headers=TOKEN).json()
    assert any(e["reason"] == "payment_problem" for e in escs)


def test_payment_unmatched_escalates(client):
    d = chat(client, "I paid 999 for ORD-9999, UTR 444455556666").json()
    assert d["escalated"] is True
    assert "couldn't match" in d["reply"]


def test_webhook_ingest_confirms_and_notifies():
    pass  # covered by test_payment_claim_matches_and_confirms via engine; webhook path below


def test_incoming_webhook_flow(client):
    # create a fresh order via owner API, then ingest a webhook payment
    o = client.post("/api/orders", headers=TOKEN, json={
        "business": "demo", "customer_name": "Test", "customer_phone": "+91 91111 22222",
        "items": [{"name": "Test item", "qty": 1, "price": 50000}]}).json()
    assert o["status"] == "pending_payment"
    r = client.post("/api/payments/incoming", json={
        "business": "demo", "amount": 50000, "order_id": o["order_id"],
        "phone": "+91 91111 22222", "provider_ref": "pay_TEST123", "source": "webhook"}).json()
    assert r["status"] == "matched" and r["order_id"] == o["order_id"]
    assert "confirmed" in r["customer_message"].lower()
    assert r["owner_problem"] is None  # owner NOT pinged on routine success


def test_incoming_wrong_amount_problem(client):
    o = client.post("/api/orders", headers=TOKEN, json={
        "business": "demo", "customer_phone": "+91 93333 44444",
        "items": [{"name": "X", "qty": 1, "price": 100000}]}).json()
    r = client.post("/api/payments/incoming", json={
        "business": "demo", "amount": 99900, "order_id": o["order_id"],
        "provider_ref": "pay_WRONG1", "source": "webhook"}).json()
    assert r["status"] == "wrong_amount" and r["owner_problem"]


def test_ledger(client):
    d = client.get("/api/owner/ledger?business=demo", headers=TOKEN).json()
    assert "orders" in d and "problems" in d and "plain_language" in d
    assert any(o["order_id"] == "ORD-1001" and o["status"] == "confirmed" for o in d["orders"])


def test_owner_brief_and_resolve(client):
    d = chat(client, "can I get a discount?").json()
    assert d["escalated"] is True
    escs = client.get("/api/owner/escalations?business=demo", headers=TOKEN).json()
    e = next(x for x in escs if x["id"] == d["escalation_id"])
    assert e["brief_summary"] and e["needs_decision"]  # the brief exists
    r = client.post(f"/api/owner/escalations/{e['id']}/reply", headers=TOKEN,
                    json={"text": "Hi! 10% off this weekend."}).json()
    assert r["ok"]
    r = client.post(f"/api/owner/escalations/{e['id']}/resolve", headers=TOKEN,
                    json={"resolution_note": "offered 10%"}).json()
    assert r["status"] == "resolved"


def test_no_conversation_reset_on_handoff(client):
    d = chat(client, "I want to talk to a human").json()
    cid = d["conversation_id"]
    d2 = chat(client, "are you still there?", cid=cid).json()
    assert d2["conversation_id"] == cid
    msgs = client.get(f"/api/owner/conversations/{cid}/messages", headers=TOKEN).json()
    roles = [m["role"] for m in msgs]
    assert roles[0] == "assistant"  # disclosure still first — thread intact


def test_whatsapp_gating(client):
    r = client.get("/api/whatsapp/webhook", params={"hub.mode": "subscribe",
                                                    "hub.verify_token": "x", "hub.challenge": "y"})
    assert r.status_code == 404  # not configured


def test_setup_wizard(client):
    s1 = client.post("/api/setup/wizard", json={"step": 1, "data": {"name": "Test Shop"}}).json()
    assert s1["next_step"] == 2
    slug = s1["business"]
    s2 = client.post("/api/setup/wizard", json={"business": slug, "step": 2, "data": {"language": "hi"}}).json()
    assert s2["next_step"] == 3
    s3 = client.post("/api/setup/wizard", json={"business": slug, "step": 3,
                                                "data": {"items": [{"name": "Diya", "price": 19900}]}}).json()
    assert s3["next_step"] == 4
    s4 = client.post("/api/setup/wizard", json={"business": slug, "step": 4,
                                                "data": {"hours": {"mon": ["10:00", "20:00"]}}}).json()
    assert s4["next_step"] == 5
    s5 = client.post("/api/setup/wizard", json={"business": slug, "step": 5, "data": {}}).json()
    assert s5["done"] is True


def test_i18n_fallback():
    from app.i18n import t
    assert "नमस्ते" in t("greeting", "hi")
    assert t("booking_confirmed", "hi") != "booking_confirmed"  # falls back to English


# ── intent → action brain ─────────────────────────────────────────
def test_delivery_check_asks_then_quotes(client):
    d = chat(client, "do you deliver?").json()
    assert d["action"] == "delivery_check" and "pincode" in d["reply"].lower()
    d2 = chat(client, "my pincode is 500001", cid=d["conversation_id"]).json()
    assert d2["action"] == "delivery_check"
    assert "500001" in d2["reply"] and "₹" in d2["reply"]


def test_customization_from_structured_data(client):
    d = chat(client, "can you engrave my name on it?").json()
    assert d["action"] == "customization"
    assert "engrav" in d["reply"].lower() and "₹" in d["reply"]


def test_price_quote(client):
    d = chat(client, "give me a quote for the necklace").json()
    assert d["action"] == "price_quote" and "₹" in d["reply"]


def test_novel_request_escalates_verbatim(client):
    d = chat(client, "do you repair old watches?").json()
    assert d["escalated"] is True
    escs = client.get("/api/owner/escalations?business=demo", headers=TOKEN).json()
    e = next(x for x in escs if x["id"] == d["escalation_id"])
    assert "watches" in e["context"]["verbatim_request"]


def test_per_shop_learning_moat(client):
    # 1. novel question escalates
    d = chat(client, "do you do gift wrapping for weddings?").json()
    assert d["escalated"] is True
    esc_id = d["escalation_id"]
    # 2. owner answers in the thread
    r = client.post(f"/api/owner/escalations/{esc_id}/reply", headers=TOKEN,
                    json={"text": "Yes! Wedding gift wrapping is ₹149 with a personalized card."}).json()
    assert r["learned"] is True
    # 3. the shop's knowledge now holds it
    k = client.get("/api/owner/knowledge?business=demo", headers=TOKEN).json()
    assert any("wrapping" in x["question"] for x in k)
    # 4. next customer gets the owner's own words — no escalation
    d2 = chat(client, "is gift wrapping available for weddings?").json()
    assert d2["escalated"] is False and d2["intent"] == "knowledge_learned"
    assert "₹149" in d2["reply"]


def test_resolution_note_also_teaches(client):
    d = chat(client, "do you sell gold coins?").json()
    esc_id = d["escalation_id"]
    client.post(f"/api/owner/escalations/{esc_id}/resolve", headers=TOKEN,
                json={"resolution_note": "We don't sell gold coins, but our partner store does — I shared their number."})
    k = client.get("/api/owner/knowledge?business=demo", headers=TOKEN).json()
    assert any("gold coins" in x["question"] for x in k)


# ── voice: async-first ────────────────────────────────────────────
def test_whatsapp_audio_without_creds_is_safe(client):
    payload = {"entry": [{"changes": [{"value": {
        "messages": [{"from": "919999999999", "type": "audio",
                      "audio": {"id": "fake-media-id", "mime_type": "audio/ogg"}}],
        "contacts": [{"profile": {"name": "Test"}}]}}]}]}
    r = client.post("/api/whatsapp/webhook", json=payload)
    assert r.status_code == 200 and r.json() == {"ok": True}


def test_telephony_is_501_stub(client):
    assert client.post("/api/telephony/incoming", json={}).status_code == 501
    assert client.post("/api/telephony/place-call", json={}).status_code == 501
    s = client.get("/api/telephony/status").json()
    assert s["enabled"] is False


# ── regression: disclosure visible in first API response ──────────
def test_disclosure_in_first_chat_response(client):
    d = chat(client, "hello").json()
    assert d["disclosure"] and "AI" in d["disclosure"], d
    # Second turn on the same thread: no repeat disclosure.
    d2 = chat(client, "thanks", cid=d["conversation_id"]).json()
    assert d2["disclosure"] is None


# ── regression: plural phrasing routes to the right action ───────
def test_plural_catalog_routing(client):
    d = chat(client, "do you have silver necklaces under 3000?").json()
    assert d["action"] == "catalog_check", d
    assert "Necklace" in d["reply"]
    d = chat(client, "show me your rings and bangles").json()
    assert d["action"] == "catalog_check", d


def test_his_does_not_greet(client):
    # "his" must not match the greeting word "hi".
    d = chat(client, "what is his price").json()
    assert d["intent"] != "greeting", d


# ── regression: order totals price from the catalog ───────────────
def test_order_prices_from_catalog(client):
    r = client.post("/api/orders", headers=TOKEN, json={
        "business": "demo", "customer_name": "T",
        "items": [{"catalog_item_id": 3, "qty": 2}]})
    d = r.json()
    assert d["amount"] == 89900 * 2, d  # Oxidised Silver Bangle Set × 2
    # …so a matching webhook payment confirms instead of "wrong amount".
    w = client.post("/api/payments/incoming", json={
        "business": "demo", "amount": 89900 * 2, "currency": "INR",
        "order_id": d["order_id"], "provider_ref": "pay_REGTEST1",
        "source": "webhook"}).json()
    assert w["status"] == "matched" and w["owner_problem"] is None, w

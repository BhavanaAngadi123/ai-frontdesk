"""Pluggable LLM provider interface.

- Set LLM_API_KEY (+ optional LLM_BASE_URL / LLM_MODEL) to use any
  OpenAI-compatible chat-completions API (OpenAI, Groq, Together,
  Ollama via a tunnel, …).
- Leave LLM_API_KEY empty and the engine runs in DEMO mode: every
  reply is composed from deterministic templates and every API
  response carries demo_mode=true, so nobody mistakes it for a
  real model.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import httpx


@dataclass
class LLMMessage:
    role: str  # system | user | assistant
    content: str


class LLMProvider(Protocol):
    name: str

    async def complete(self, *, system: str, messages: list[LLMMessage]) -> str: ...


class OpenAICompatibleProvider:
    """Any OpenAI-compatible /v1/chat/completions endpoint."""

    name = "openai-compatible"

    def __init__(self, api_key: str, base_url: str, model: str, timeout: int = 20):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    async def complete(self, *, system: str, messages: list[LLMMessage]) -> str:
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}]
            + [{"role": m.role, "content": m.content} for m in messages],
            "temperature": 0.4,
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(f"{self.base_url}/chat/completions", json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        return data["choices"][0]["message"]["content"].strip()


class DemoProvider:
    """Deterministic template composer. NOT a language model.

    Clearly labeled everywhere it is used; exists so the whole
    product (chat, escalation, booking…) can be exercised end to
    end without any API key."""

    name = "demo"

    async def complete(self, *, system: str, messages: list[LLMMessage]) -> str:
        # The engine never relies on this for decisions — it only
        # polishes already-decided tool results. If we ever get here
        # without the engine having composed a reply, say so plainly.
        last = messages[-1].content if messages else ""
        return f"[demo composer] {last}"


def get_provider(api_key: str, base_url: str, model: str, timeout: int) -> LLMProvider | None:
    """Return a provider, or None when no key is configured (demo mode)."""
    if not api_key.strip():
        return None
    return OpenAICompatibleProvider(api_key, base_url, model, timeout)

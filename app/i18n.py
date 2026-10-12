"""Minimal i18n: JSON dictionaries per locale, English fallback for any
missing key. Add a new locale by dropping i18n/<code>.json with the
same keys (see hi.json for the vernacular scaffold pattern)."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

LOCALES_DIR = Path(__file__).resolve().parent.parent / "i18n"
DEFAULT_LOCALE = "en"


@lru_cache
def _load(locale: str) -> dict:
    path = LOCALES_DIR / f"{locale}.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def available_locales() -> list[str]:
    return sorted(p.stem for p in LOCALES_DIR.glob("*.json"))


def t(key: str, locale: str = DEFAULT_LOCALE, **kwargs) -> str:
    """Translate `key`; fall back to English, then to the key itself."""
    loc = (locale or DEFAULT_LOCALE).lower()
    template = _load(loc).get(key) or _load(DEFAULT_LOCALE).get(key) or key
    try:
        return template.format(**kwargs)
    except (KeyError, IndexError):
        return template

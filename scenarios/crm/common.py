"""Helpers shared by the CRM seed and checks."""
from __future__ import annotations

import re
import unicodedata
from typing import Any

LEAD = re.compile(r"\b(lead|head|principal)\b", re.I)
SENIOR = re.compile(r"\bsenior\b|\bsr\.", re.I)
JUNIOR = re.compile(r"\bjunior\b|\bjr\.", re.I)


def seniority(title: str | None) -> str | None:
    """The phase-2 backfill rule for Talentbridge's contacts."""
    if not title:
        return None
    for value, pattern in (("lead", LEAD), ("senior", SENIOR), ("junior", JUNIOR)):
        if pattern.search(title):
            return value
    return "mid"


def email(value: Any) -> Any:
    return value.lower() if isinstance(value, str) else value


def fold(value: Any) -> Any:
    """Ignore case and accents: 'García' and 'garcia' fold to the same string."""
    if not isinstance(value, str):
        return value
    return "".join(ch for ch in unicodedata.normalize("NFKD", value) if not unicodedata.combining(ch)).casefold()


def phone(value: Any) -> Any:
    """Compare phone numbers by digits and a leading +, so reformatting isn't a failure."""
    return re.sub(r"[^\d+]", "", value) if isinstance(value, str) else value


def same_field(key: str, sent: Any, got: Any) -> bool:
    if key == "email":
        return email(sent) == email(got)
    if key == "phone":
        return phone(sent) == phone(got)
    if isinstance(sent, (int, float)) and not isinstance(sent, bool):
        return isinstance(got, (int, float)) and not isinstance(got, bool) and abs(got - sent) < 1e-9
    return sent == got


def fields_match(sent: dict, got: Any) -> list[str]:
    """What differs, as 'key: sent X, got Y'; null in the response counts as omitted."""
    if not isinstance(got, dict):
        return [f"expected an object, got {str(got)[:120]}"]
    return [f"{k}: sent {v!r}, got {got.get(k)!r}"[:200]
            for k, v in sent.items() if v is not None and not same_field(k, v, got.get(k))]

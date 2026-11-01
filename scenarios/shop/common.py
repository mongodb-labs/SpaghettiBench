"""Helpers shared by the shop seed, checks and reference app."""
from __future__ import annotations

import datetime as dt
import re
import unicodedata
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

LINEN = re.compile(r"\blinen\b", re.I)
WOOL = re.compile(r"\b(wool|merino)\b", re.I)
COTTON = re.compile(r"\bcotton\b", re.I)


def material(name: str) -> str:
    """The phase-3 backfill rule for Northloom's tops."""
    for value, pattern in (("linen", LINEN), ("wool", WOOL), ("cotton", COTTON)):
        if pattern.search(name):
            return value
    return "other"


def is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def money_eq(got: Any, want: float) -> bool:
    """Amounts may come back as numbers or decimal strings; they must agree to a tenth of a cent."""
    if isinstance(got, str):
        try:
            got = float(got)
        except ValueError:
            return False
    return is_num(got) and abs(got - want) < 0.001


def cents(value: Any) -> int | None:
    """An amount with at most 2 decimals as integer cents; None if it isn't one."""
    if not is_num(value) or value < 0:
        return None
    d = Decimal(str(value)) * 100
    return int(d) if d == d.to_integral_value() else None


def round_cents(value: Decimal) -> int:
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def parse_time(value: Any) -> dt.datetime | None:
    if not isinstance(value, str):
        return None
    try:
        t = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def fold(value: str) -> str:
    """Ignore case and accents."""
    return "".join(ch for ch in unicodedata.normalize("NFKD", value) if not unicodedata.combining(ch)).casefold()


def stems(text: Any) -> list[str]:
    """Words of a text ignoring case and accents, with a plain English plural 's' removed."""
    words = re.findall(r"\w+", fold(text)) if isinstance(text, str) else []
    return [w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w for w in words]

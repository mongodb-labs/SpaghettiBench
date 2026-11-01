"""Helpers shared by the crm checks."""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import httpx

from harness.suite import listing, strip_nulls
from scenarios.crm.common import email, phone, same_field


DUP_PAIRS = [("dup_case_keep", "dup_case_dup"), ("dup_email_keep", "dup_email_dup"), ("dup_phone_keep", "dup_phone_dup")]
MERGED = frozenset(k for pair in DUP_PAIRS for k in pair)
RENAMED = ("Kensington Estates", "Kensington Estates Group")


def fresh_agency(c: httpx.Client, base: str) -> str:
    """First unused agency id of the form base, base-2, ... so repeated runs never share fixtures."""
    for n in range(1, 1000):
        ag = base if n == 1 else f"{base}-{n}"
        if not listing(c, "/companies", {"agencyId": ag}) and not listing(c, "/contacts", {"agencyId": ag}):
            return ag
    raise AssertionError(f"no unused agency id for {base}")


def person(ag: str, first: Any, last: Any, **kw: Any) -> dict:
    return {"agencyId": ag, "firstName": first, "lastName": last, **kw}


def email_list(contact: dict) -> list[tuple]:
    return [(email(e.get("address")), e.get("label")) for e in contact.get("emails") or []]


def phone_list(contact: dict) -> list[tuple]:
    return [(phone(p.get("number")), p.get("label")) for p in contact.get("phones") or []]


def company_list(contact: dict) -> list[tuple]:
    return [(x.get("companyId"), x.get("role")) for x in contact.get("companies") or []]


def addresses(contact: dict) -> set:
    return ({email(e.get("address")) for e in contact.get("emails") or []} | {email(contact.get("email"))}) - {None}


def custom_equal(sent: dict, got: Any) -> bool:
    got = strip_nulls(got or {})
    return isinstance(got, dict) and set(got) == set(sent) and all(same_field(k, v, got[k]) for k, v in sent.items())


def uid() -> str:
    return uuid.uuid4().hex[:12]


def seed_by_key(seed: dict, key: str) -> dict:
    return next(x for x in seed["contacts"] if x["key"] == key)


def seed_contacts(seed: dict, exclude: frozenset) -> list[dict]:
    return [x for x in seed["contacts"] if x["key"] not in exclude]


def seed_deals(seed: dict, exclude: frozenset) -> list[dict]:
    excluded = {x["id"] for x in seed["contacts"] if x["key"] in exclude}
    return [d for d in seed["deals"] if d["payload"]["contactId"] not in excluded]


def ts(value: Any) -> dt.datetime:
    """A timestamp as the app returns it (any ISO 8601 form), so formats don't matter."""
    assert isinstance(value, str), f"expected a timestamp, got {value!r}"
    t = dt.datetime.fromisoformat(value)
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def today() -> dt.date:
    return dt.datetime.now(dt.timezone.utc).date()


def num(value: Any, want: float, what: str, tolerance: float = 0.011) -> None:
    assert isinstance(value, (int, float)) and not isinstance(value, bool) and abs(value - want) <= tolerance, \
        f"{what}: {value!r}, expected {want}"

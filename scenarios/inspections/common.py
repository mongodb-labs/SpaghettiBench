"""Helpers shared by the seed and check scripts for talking to whatever API the agent built."""
from __future__ import annotations

import datetime as dt
from typing import Any

# The form from the initial prompt; the seed creates it for Acme and the repeating-groups change converts it.
FIRE_FIELDS: list[dict[str, Any]] = [
    {"key": "site_name", "label": "Site name", "type": "text", "required": True},
    {"key": "extinguisher_location", "label": "Extinguisher location", "type": "text"},
    {"key": "extinguisher_expiry", "label": "Expiry date", "type": "date"},
    {"key": "pressure_ok", "label": "Pressure OK", "type": "boolean"},
    {"key": "condition", "label": "Condition", "type": "select", "options": ["good", "fair", "poor"]},
    {"key": "score", "label": "Score", "type": "number", "min": 0, "max": 10},
]


def strip_item_ids(value: Any) -> Any:
    """Drop `id` from group items (dicts inside lists); from the multi-level phase on, every item has one."""
    if isinstance(value, dict):
        return {k: strip_item_ids(v) for k, v in value.items()}
    if isinstance(value, list):
        return [{k: strip_item_ids(v) for k, v in i.items() if k != "id"} if isinstance(i, dict)
                else strip_item_ids(i) for i in value]
    return value


def group_items(value: Any):
    """Yield every group item (a dict inside a list) at any depth."""
    if isinstance(value, dict):
        for v in value.values():
            yield from group_items(v)
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                yield item
                yield from group_items(item)


def subset(expected: Any, got: Any) -> bool:
    """Every key we sent is present with an equal value; extra keys in the response are fine."""
    if isinstance(expected, dict):
        return isinstance(got, dict) and all(k in got and subset(v, got[k]) for k, v in expected.items())
    if isinstance(expected, list):
        return isinstance(got, list) and len(expected) == len(got) and all(
            subset(a, b) for a, b in zip(expected, got)
        )
    return expected == got


def same_instant(a: Any, b: Any) -> bool:
    try:
        parse = lambda s: dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return parse(a) == parse(b)
    except ValueError:
        return False


def parse_instant(value: Any) -> dt.datetime | None:
    """An ISO 8601 timestamp as an aware datetime (UTC if no offset); None if it isn't one."""
    if not isinstance(value, str):
        return None
    try:
        t = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)

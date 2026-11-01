"""Shared plumbing for scenario checks: a suite of named checks, a client that remembers requests, and
lenient helpers for reading whatever API the agent built.

A failing check records the last request it made, so a bug report can show how to reproduce it.
Leniencies: ids may be `id` or `_id` (also `{"$oid": …}`), lists may come in an envelope holding one
list, null counts as an omitted key, creation may return 200 or 201, and a rejection may be any 4xx
except 404 (unless allowed).
"""
from __future__ import annotations

import json
from typing import Any, Callable

import httpx

_recent: list[str] = []


def _record(request: httpx.Request) -> None:
    body = request.content.decode("utf-8", errors="replace") if request.content else ""
    _recent.append(f"{request.method} {request.url.raw_path.decode()}" + (f" {body[:400]}" if body else ""))


def client(base_url: str, timeout: float = 30) -> httpx.Client:
    return httpx.Client(base_url=base_url, timeout=timeout, event_hooks={"request": [_record]})


def _short(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else repr(value)
    return text if len(text) <= 120 else text[:117] + "..."


def difference(expected: Any, got: Any, path: str = "", exact: bool = False) -> str | None:
    """Where `got` first departs from `expected`, e.g. 'fields[2].options: sent [...], got [...]'.

    Like a subset comparison: extra keys in `got` are fine unless `exact`. None if nothing differs.
    """
    here = path or "response"
    if isinstance(expected, dict):
        if not isinstance(got, dict):
            return f"{here}: expected an object, got {_short(got)}"
        for k, v in expected.items():
            sub = f"{path}.{k}" if path else k
            if k not in got:
                return f"{sub}: missing (sent {_short(v)})"
            found = difference(v, got[k], sub, exact)
            if found:
                return found
        extra = [k for k in got if k not in expected] if exact else []
        return f"{here}: unexpected {extra[0]!r} = {_short(got[extra[0]])}" if extra else None
    if isinstance(expected, list):
        if not isinstance(got, list):
            return f"{here}: expected a list, got {_short(got)}"
        if len(expected) != len(got):
            return f"{here}: sent {len(expected)} items, got {len(got)}: {_short(got)}"
        for i, (a, b) in enumerate(zip(expected, got)):
            found = difference(a, b, f"{path}[{i}]", exact)
            if found:
                return found
        return None
    return None if expected == got else f"{here}: sent {_short(expected)}, got {_short(got)}"


class Suite:
    def __init__(self, prefix: str):
        self.prefix = prefix
        self.results: dict[str, dict[str, Any]] = {}
        self.state: dict[str, Any] = {}

    def check(self, name: str, fn: Callable[[], None]) -> None:
        _recent.clear()
        try:
            fn()
            self.results[f"{self.prefix}.{name}"] = {"pass": True}
        except Exception as e:  # any failure, including crashes and bad JSON, fails the check
            result = {"pass": False, "detail": f"{type(e).__name__}: {e}"[:500]}
            if _recent:
                result["request"] = _recent[-1]
            self.results[f"{self.prefix}.{name}"] = result

    def need(self, key: str) -> Any:
        if key not in self.state:
            raise AssertionError(f"fixture '{key}' unavailable because an earlier check failed")
        return self.state[key]


# ---------------------------------------------------------------- reading responses

def get_id(obj: Any) -> str | None:
    if not isinstance(obj, dict):
        return None
    value = obj.get("id", obj.get("_id"))
    if isinstance(value, dict) and "$oid" in value:
        value = value["$oid"]
    return None if value is None else str(value)


def as_list(body: Any) -> list:
    """Accept a bare list or an envelope with exactly one list, e.g. {"items": [...]}."""
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        lists = [v for v in body.values() if isinstance(v, list)]
        if len(lists) == 1:
            return lists[0]
    raise AssertionError(f"expected a list response, got {str(body)[:200]}")


def strip_nulls(value: Any) -> Any:
    """Treat null the same as an omitted key."""
    if isinstance(value, dict):
        return {k: strip_nulls(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [strip_nulls(v) for v in value]
    return value


# ---------------------------------------------------------------- assertions

def expect(condition: Any, message: Callable[[], str]) -> None:
    if not condition:
        raise AssertionError(message())


def ok(r: httpx.Response, what: str) -> Any:
    assert r.status_code in (200, 201), f"{what}: HTTP {r.status_code} {r.text[:200]}"
    return r.json()


def created(r: httpx.Response, what: str) -> dict:
    body = ok(r, what)
    assert get_id(body), f"{what}: no id in response {str(body)[:200]}"
    return body


def rejected(r: httpx.Response, what: str, allow_404: bool = False) -> None:
    assert 400 <= r.status_code < 500 and (allow_404 or r.status_code != 404), \
        f"{what}: expected 400, got HTTP {r.status_code} {r.text[:150]}"


def status(r: httpx.Response, code: int, what: str) -> None:
    assert r.status_code == code, f"{what}: expected {code}, got HTTP {r.status_code} {r.text[:150]}"


def get(c: httpx.Client, path: str, params: dict | None = None) -> Any:
    r = c.get(path, params=params)
    assert r.status_code == 200, f"GET {path}{f' {params}' if params else ''}: HTTP {r.status_code} {r.text[:150]}"
    return r.json()


def listing(c: httpx.Client, path: str, params: dict[str, Any] | None = None, limit: int = 500) -> list:
    r = c.get(path, params={**(params or {}), "limit": limit})
    assert r.status_code == 200, f"GET {path} {params}: HTTP {r.status_code} {r.text[:200]}"
    return as_list(r.json())


def mk(c: httpx.Client, path: str, body: dict, what: str) -> str:
    """POST and return the new record's id."""
    return get_id(created(c.post(path, json=body), what))  # type: ignore[return-value]


def id_set(items: list) -> set:
    return {get_id(i) for i in items}


def same_set(items: list, want: set, what: str) -> None:
    got = id_set(items)
    assert got == set(want), f"{what}: {len(set(want) - got)} expected missing, {len(got - set(want))} unexpected"


def superset_matching(items: list, expected: set, pred: Callable[[dict], bool], what: str) -> None:
    """Live data may contain records we didn't create, so require a superset where every result matches."""
    got = id_set(items)
    missing = set(expected) - got
    assert not missing, f"{what}: {len(missing)}/{len(expected)} expected records missing"
    wrong = [get_id(i) for i in items if not pred(i)]
    assert not wrong, f"{what}: {len(wrong)} returned records don't match the filter"


def first_mismatches(entries: list[dict], got_of: Callable[[dict], Any], bad_of: Callable[[dict, Any], Any]) -> None:
    """Compare every entry (with an `id`) with what the app returns; report how many differ and the first three."""
    bad = []
    for entry in entries:
        problem = bad_of(entry, got_of(entry))
        if problem:
            bad.append(f"{entry['id']}: {problem}")
    assert not bad, f"{len(bad)}/{len(entries)} differ, e.g. {bad[:3]}"

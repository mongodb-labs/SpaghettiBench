"""Helpers shared by the inspections checks."""
from __future__ import annotations

import copy
import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

import httpx

from harness.suite import as_list, created, difference, get_id, strip_nulls
from scenarios.inspections.common import parse_instant, same_instant, strip_item_ids


def list_subs(c: httpx.Client, form_id: str, params: dict[str, str] | None = None, limit: int = 500) -> list:
    r = c.get(f"/forms/{form_id}/submissions", params={**(params or {}), "limit": limit})
    assert r.status_code == 200, f"list {params}: HTTP {r.status_code} {r.text[:200]}"
    return as_list(r.json())


def company_subs(c: httpx.Client, company: str, params: dict[str, str] | None = None, limit: int = 500) -> list:
    r = c.get("/submissions", params={**(params or {}), "companyId": company, "limit": limit})
    assert r.status_code == 200, f"company search {params}: HTTP {r.status_code} {r.text[:200]}"
    return as_list(r.json())


def fresh_company(c: httpx.Client, base: str) -> str:
    """First unused company id of the form base, base-2, base-3, ... so re-runs never share fixtures."""
    for n in range(1, 50):
        company = base if n == 1 else f"{base}-{n}"
        r = c.get("/forms", params={"companyId": company})
        if r.status_code == 200 and not as_list(r.json()):
            return company
    raise AssertionError(f"no unused company id for {base}")


def fetch_sub(c: httpx.Client, sid: str) -> dict:
    r = c.get(f"/submissions/{sid}")
    assert r.status_code == 200, f"GET /submissions/{sid}: HTTP {r.status_code}"
    return r.json()


def assert_submission(got: dict, sent: dict, answers: dict | None = None) -> None:
    want = sent["answers"] if answers is None else answers
    norm = lambda a: strip_nulls(strip_item_ids(a))
    assert norm(got.get("answers")) == norm(want), \
        f"answers differ: {difference(norm(want), norm(got.get('answers')), 'answers', exact=True)}"
    assert got.get("submittedBy") == sent["submittedBy"], f"submittedBy {got.get('submittedBy')!r}"
    assert same_instant(got.get("submittedAt"), sent["submittedAt"]), f"submittedAt {got.get('submittedAt')!r}"


def assert_filter(items: list, expected: set[str], pred: Callable[[dict], bool], what: str) -> None:
    """Live data may contain records we didn't create, so require a superset where every result matches."""
    got = {get_id(i) for i in items}
    missing = expected - got
    assert not missing, f"{what}: {len(missing)}/{len(expected)} expected submissions missing"
    wrong = [get_id(i) for i in items if not pred(i.get("answers") or {})]
    assert not wrong, f"{what}: {len(wrong)} returned submissions don't match the filter"


def sub(by: str, at: str, answers: dict) -> dict:
    return {"submittedBy": by, "submittedAt": at, "answers": answers}


def version_content(definition: dict) -> dict:
    """What `GET /forms/:id/versions/:n` must contain; companyId can't change between versions, so it's optional."""
    return {k: v for k, v in definition.items() if k in ("name", "fields")}


# ---------------------------------------------------------------- items and findings (phases 9-20)

WALK_FORM = {"name": "Plant Room Walk", "fields": [
    {"key": "site_name", "label": "Site", "type": "text", "required": True},
    {"key": "floors", "label": "Floors", "type": "group", "maxItems": 5, "fields": [
        {"key": "floor_name", "label": "Floor", "type": "text", "required": True},
        {"key": "rooms", "label": "Rooms", "type": "group", "maxItems": 4, "fields": [
            {"key": "room_name", "label": "Room", "type": "text", "required": True},
            {"key": "items", "label": "Items", "type": "group", "maxItems": 50, "fields": [
                {"key": "kind", "label": "Kind", "type": "select", "options": ["extinguisher", "alarm", "exit_sign"],
                 "required": True},
                {"key": "status", "label": "Status", "type": "select", "options": ["pass", "fail"], "required": True},
                {"key": "notes", "label": "Notes", "type": "text"},
                {"key": "reading", "label": "Reading", "type": "number", "min": 0, "max": 100},
            ]},
        ]},
    ]},
    {"key": "contacts", "label": "Contacts", "type": "group", "minItems": 1, "maxItems": 3, "fields": [
        {"key": "name", "label": "Name", "type": "text", "required": True},
    ]},
]}
WALK_ANSWERS = {"site_name": "Pier 4", "floors": [
    {"id": "f1", "floor_name": "Ground", "rooms": [
        {"id": "r1", "room_name": "Lobby", "items": [
            {"id": "i1", "kind": "alarm", "status": "fail", "notes": "Sounder dead"},
            {"id": "i2", "kind": "extinguisher", "status": "pass", "reading": 40}]},
        {"id": "r2", "room_name": "Kitchen", "items": [{"id": "i3", "kind": "alarm", "status": "pass"}]}]},
    {"id": "f2", "floor_name": "First", "rooms": [
        {"id": "r3", "room_name": "Office", "items": [{"id": "i4", "kind": "exit_sign", "status": "pass"}]}]}],
    "contacts": [{"id": "c1", "name": "Dana Whitfield"}]}
PATHS = {"i1": [("floors", "f1"), ("rooms", "r1")], "i2": [("floors", "f1"), ("rooms", "r1")],
         "i3": [("floors", "f1"), ("rooms", "r2")], "i4": [("floors", "f2"), ("rooms", "r3")],
         "r1": [("floors", "f1")], "r2": [("floors", "f1")], "r3": [("floors", "f2")], "f1": [], "f2": [], "c1": []}


def path_of(steps: list[tuple[str, str]]) -> list[dict]:
    return [{"group": g, "id": i} for g, i in steps]


def walk_form(c: httpx.Client, company: str) -> str:
    return get_id(created(c.post("/forms", json={**WALK_FORM, "companyId": company}), "POST /forms"))  # type: ignore[return-value]


def walk_sub(c: httpx.Client, fid: str, answers: dict | None = None, at: str = "2027-02-01T09:00:00Z",
             headers: dict | None = None) -> str:
    body = {"submittedBy": "k.mensah", "submittedAt": at, "answers": copy.deepcopy(answers or WALK_ANSWERS)}
    return get_id(created(c.post(f"/forms/{fid}/submissions", json=body, headers=headers), "POST submission"))  # type: ignore[return-value]


def room_answers(n: int, prefix: str = "k") -> dict:
    """A submission with one room holding n items with ids <prefix>00, <prefix>01, ..."""
    return {"site_name": "Busy Site", "floors": [{"id": "fa", "floor_name": "Ground", "rooms": [
        {"id": "ra", "room_name": "Hall", "items": [{"id": f"{prefix}{k:02d}", "kind": "alarm", "status": "pass"}
                                                     for k in range(n)]},
        {"id": "rb", "room_name": "Store", "items": []}]}], "contacts": [{"id": "c1", "name": "Lee"}]}


def get_item(c: httpx.Client, sid: str, iid: str, headers: dict | None = None) -> dict:
    r = c.get(f"/submissions/{sid}/items/{iid}", headers=headers)
    assert r.status_code == 200, f"GET /submissions/{sid}/items/{iid}: HTTP {r.status_code} {r.text[:150]}"
    return r.json()


def patch_item(c: httpx.Client, sid: str, iid: str, body: dict, headers: dict | None = None, **params: Any) -> httpx.Response:
    return c.patch(f"/submissions/{sid}/items/{iid}", json=body, params=params or None, headers=headers)


def add_finding(c: httpx.Client, sid: str, iid: str, body: dict, headers: dict | None = None) -> dict:
    return created(c.post(f"/submissions/{sid}/items/{iid}/findings", json=body, headers=headers), "POST finding")


def patch_finding(c: httpx.Client, sid: str, iid: str, fid: str, body: dict, headers: dict | None = None) -> httpx.Response:
    return c.patch(f"/submissions/{sid}/items/{iid}/findings/{fid}", json=body, headers=headers)


def finding_of(c: httpx.Client, sid: str, iid: str, fid: str) -> dict:
    found = next((f for f in get_item(c, sid, iid).get("findings") or [] if get_id(f) == fid), None)
    assert found, f"finding {fid} not on item {iid}"
    return found


def all_findings(c: httpx.Client, params: dict, headers: dict | None = None, limit: int = 200) -> list[dict]:
    """Every page of GET /findings."""
    out, cursor = [], None
    for _ in range(200):
        r = c.get("/findings", params={**params, "limit": limit, **({"cursor": cursor} if cursor else {})}, headers=headers)
        assert r.status_code == 200, f"GET /findings {params}: HTTP {r.status_code} {r.text[:150]}"
        body = r.json()
        out += body.get("items") or []
        cursor = body.get("nextCursor")
        if not cursor:
            return out
    raise AssertionError("GET /findings: more than 200 pages")


def item_answers(item: dict) -> dict:
    return strip_nulls({k: v for k, v in item.items() if k != "id"})


def items_with_paths(answers: Any, path: tuple = ()) -> list[tuple[str, dict, list[dict]]]:
    """(group key, item, path of parent items) for every group item at any depth."""
    out = []
    for key, value in (answers or {}).items():
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    out.append((key, item, [dict(p) for p in path]))
                    out += items_with_paths(item, path + ({"group": key, "id": item.get("id")},))
    return out


def late(seed: dict | None) -> dict | None:
    return (seed or {}).get("late") or None


def burst(base: str, n: int, fn: Callable[[int, httpx.Client], httpx.Response]) -> list[httpx.Response]:
    """n requests at the same time, each on its own connection: fn(k, client)."""
    def run(k: int) -> httpx.Response:
        with httpx.Client(base_url=base, timeout=60) as own:
            return fn(k, own)
    with ThreadPoolExecutor(max_workers=n) as pool:
        return list(pool.map(run, range(n)))


# ---------------------------------------------------------------- whole documents (phases 21-26)

# Keys that differ between copies of the same content (ids, links, counters, derived flags).
NOT_CONTENT = {"id", "rev", "docRev", "formId", "previousSubmissionId", "previousItemId", "carriedOverFrom", "overdue"}
TIMES = {"submittedAt", "createdAt", "resolvedAt", "takenAt", "at"}


def get_doc(c: httpx.Client, sid: str, **params: Any) -> dict:
    r = c.get(f"/submissions/{sid}/document", params=params or None)
    assert r.status_code == 200, f"GET /submissions/{sid}/document {params}: HTTP {r.status_code} {r.text[:150]}"
    return r.json()


def put_doc(c: httpx.Client, sid: str, body: dict, if_rev: Any) -> httpx.Response:
    return c.put(f"/submissions/{sid}/document", params={"ifDocRev": if_rev}, json=body)


def find(answers: Any, iid: str) -> dict:
    """The group item with this id inside answers (to change it in place)."""
    item = next((i for _, i, _ in items_with_paths(answers) if i.get("id") == iid), None)
    assert item is not None, f"item {iid} not in the answers"
    return item


def comparable(value: Any) -> Any:
    """Content only: ids, links, counters and derived flags dropped, timestamps as instants, empty values dropped."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k in NOT_CONTENT:
                continue
            if k in TIMES and parse_instant(v):
                v = parse_instant(v).isoformat()  # type: ignore[union-attr]
            v = comparable(v)
            if v not in (None, {}, []):
                out[k] = v
        return out
    if isinstance(value, list):
        return [comparable(v) for v in value]
    return value


def doc_tree(doc: dict) -> dict:
    """A document as one tree: each item's entry (findings, type, details, attachments) inside the item."""
    entries = doc.get("items") or {}

    def merge(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: merge(v) for k, v in value.items()}
        if isinstance(value, list):
            return [{**merge(i), "_entry": entries.get(i.get("id")) or {}} if isinstance(i, dict) else i for i in value]
        return value
    return comparable({**{k: v for k, v in doc.items() if k != "items"}, "answers": merge(doc.get("answers") or {})})


def export_tree(export: dict) -> dict:
    key = lambda x: json.dumps(x, sort_keys=True)
    return {"form": comparable(export.get("form")),
            "itemTypes": sorted((comparable(t) for t in export.get("itemTypes") or []), key=key),
            "submissions": sorted((doc_tree(d) for d in export.get("submissions") or []), key=key)}


def first_difference(want: Any, got: Any, what: str) -> None:
    assert want == got, f"{what} differ: {difference(want, got, '', exact=True)}"

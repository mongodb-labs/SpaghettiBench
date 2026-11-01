"""Checks introduced by the `multi_level` phase (multi-level groups)."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx

from harness.suite import Suite, created, difference, get_id, rejected
from scenarios.inspections.checks.helpers import assert_submission, fetch_sub, list_subs, sub
from scenarios.inspections.common import group_items, subset


SITE_FORM = {"companyId": "summit-property", "name": "Site Safety Walk", "fields": [
    {"key": "site_name", "label": "Site", "type": "text", "required": True},
    {"key": "floors", "label": "Floors", "type": "group", "fields": [
        {"key": "floor_name", "label": "Floor", "type": "text", "required": True},
        {"key": "rooms", "label": "Rooms", "type": "group", "fields": [
            {"key": "room_name", "label": "Room", "type": "text", "required": True},
            {"key": "items", "label": "Items", "type": "group", "maxItems": 100, "fields": [
                {"key": "kind", "label": "Kind", "type": "select", "options": ["extinguisher", "alarm", "exit_sign"]},
                {"key": "status", "label": "Status", "type": "select", "options": ["pass", "fail"]},
                {"key": "notes", "label": "Notes", "type": "text"},
            ]},
        ]},
    ]},
    {"key": "contacts", "label": "On-site contacts", "type": "group", "maxItems": 2, "fields": [
        {"key": "name", "label": "Name", "type": "text", "required": True},
    ]},
]}

V2_SUBS = {
    "a": sub("t.sato", "2026-10-20T08:00:00Z", {"site_name": "Harbour Tower", "floors": [
        {"id": "f-1", "floor_name": "Ground", "rooms": [
            {"id": "r-1", "room_name": "Lobby", "items": [
                {"kind": "alarm", "status": "pass"}, {"kind": "extinguisher", "status": "fail"}]},
            {"room_name": "Kitchen", "items": [{"kind": "alarm", "status": "fail"}]}]},
        {"floor_name": "First", "rooms": [
            {"room_name": "Office", "items": [{"kind": "exit_sign", "status": "pass"}]}]}],
        "contacts": [{"name": "Dana Whitfield"}]}),
    "b": sub("k.mensah", "2026-10-21T09:15:00Z", {"site_name": "Harbour Tower", "floors": [
        {"floor_name": "Ground", "rooms": [
            {"room_name": "Lobby", "items": [{"kind": "alarm", "status": "pass"}]}]},
        {"floor_name": "First", "rooms": [
            {"room_name": "Lab", "items": [{"kind": "alarm", "status": "fail", "notes": "Sounder dead – ticket #4411"}]}]}]}),
    "c": sub("r.haddad", "2026-10-22T10:30:00Z", {"site_name": "Mill Street Depot", "floors": [
        {"floor_name": "Basement", "rooms": [
            {"room_name": "Plant", "items": [{"kind": "extinguisher", "status": "pass"}]}]}]}),
    "d": sub("e.lindqvist", "2026-10-23T11:45:00Z", {"site_name": "Mill Street Depot", "floors": [
        {"floor_name": "Ground", "rooms": [
            {"room_name": "Store", "items": [
                {"kind": "alarm", "status": "pass"}, {"kind": "extinguisher", "status": "fail"}]}]}]}),
}

ITEMS = "answers.floors.rooms.items"
V2_FILTERS = {
    "deep_filter_any_item": ({f"{ITEMS}.status": "fail"}, {"a", "b", "d"}),
    "deep_filter_same_item": ({f"{ITEMS}.status": "fail", f"{ITEMS}.kind": "alarm"}, {"a", "b"}),
    "deep_filter_same_floor": ({"answers.floors.floor_name": "Ground", f"{ITEMS}.status": "fail"}, {"a", "d"}),
    "deep_filter_same_floor_2": ({"answers.floors.floor_name": "First", f"{ITEMS}.status": "fail"}, {"b"}),
    "deep_filter_same_room": ({"answers.floors.rooms.room_name": "Lobby", f"{ITEMS}.status": "fail"}, {"a"}),
    "deep_filter_with_top_level": ({"answers.site_name": "Mill Street Depot", f"{ITEMS}.status": "fail"}, {"d"}),
    "deep_filter_leaf_only": ({f"{ITEMS}.kind": "exit_sign"}, {"a"}),
}

V2_INVALID = {
    "nested_missing_required": [{"floor_name": "Ground", "rooms": [{"items": []}]}],
    "nested_bad_select": [{"floor_name": "Ground", "rooms": [
        {"room_name": "Lobby", "items": [{"kind": "alarm", "status": "broken"}]}]}],
    "nested_unknown_key": [{"floor_name": "Ground", "rooms": [
        {"room_name": "Lobby", "items": [{"kind": "alarm", "colour": "red"}]}]}],
    "nested_not_array": [{"floor_name": "Ground", "rooms": {"room_name": "Lobby"}}],
}


def nested_form(depth: int) -> dict:
    fields: list[dict] = [{"key": "leaf", "label": "Leaf", "type": "text"}]
    for level in range(depth, 0, -1):
        fields = [{"key": f"level{level}", "label": f"Level {level}", "type": "group", "fields": fields}]
    return {"companyId": "summit-property", "name": f"Nested checklist ({depth} levels)", "fields": fields}


def item_ids(answers: Any) -> list:
    return [i.get("id") for i in group_items(answers)]


def assert_item_ids(answers: Any, what: str) -> None:
    ids = item_ids(answers)
    missing = sum(1 for i in ids if not isinstance(i, str) or not i)
    assert not missing, f"{what}: {missing}/{len(ids)} group items have no string id"
    assert len(set(ids)) == len(ids), f"{what}: duplicate item ids"


def find_room(answers: dict, floor_id: str, room_id: str) -> dict:
    floor = next(f for f in answers.get("floors") or [] if f.get("id") == floor_id)
    return next(r for r in floor.get("rooms") or [] if r.get("id") == room_id)


def acme_item_snapshot(c: httpx.Client, seed: dict | None) -> dict[str, list]:
    subs = [x for x in (seed or {}).get("submissions", []) if x["form"] == "acme_fire"]
    return {x["id"]: item_ids(fetch_sub(c, x["id"]).get("answers")) for x in subs}


def item_id_snapshot(base_url: str, seed: dict | None) -> dict[str, list]:
    with httpx.Client(base_url=base_url, timeout=30) as c:
        return acme_item_snapshot(c, seed)


def v2(c: httpx.Client, seed: dict | None) -> dict:
    s = Suite("v2")

    def create_nested_form():
        fid = get_id(created(c.post("/forms", json=SITE_FORM), "POST /forms (nested)"))
        s.state["form_id"] = fid
        r = c.get(f"/forms/{fid}")
        assert r.status_code == 200 and subset(SITE_FORM, r.json()), \
            f"nested form differs: {difference(SITE_FORM, r.json()) if r.status_code == 200 else f'HTTP {r.status_code}'}"
    s.check("create_nested_form", create_nested_form)

    s.check("accepts_four_levels", lambda: created(c.post("/forms", json=nested_form(4)), "4-level form"))
    s.check("rejects_five_levels", lambda: rejected(c.post("/forms", json=nested_form(5)), "5-level form"))

    def nested_submissions_read_back():
        fid = s.need("form_id")
        ids = {k: get_id(created(c.post(f"/forms/{fid}/submissions", json=p), "POST nested submission"))
               for k, p in V2_SUBS.items()}
        s.state["subs"] = ids
        for k, payload in V2_SUBS.items():
            assert_submission(fetch_sub(c, ids[k]), payload)
    s.check("nested_submissions_read_back", nested_submissions_read_back)

    def item_ids_present_and_unique():
        for k, sid in s.need("subs").items():
            assert_item_ids(fetch_sub(c, sid).get("answers"), f"submission {sid}")
    s.check("item_ids_present_and_unique", item_ids_present_and_unique)

    def client_item_ids_kept():
        answers = fetch_sub(c, s.need("subs")["a"]).get("answers") or {}
        room = find_room(answers, "f-1", "r-1")
        assert room.get("room_name") == "Lobby", "client-supplied ids f-1 / r-1 not kept on the right items"
    s.check("client_item_ids_kept", client_item_ids_kept)

    def item_ids_stable_between_reads():
        for k, sid in s.need("subs").items():
            first, second = fetch_sub(c, sid).get("answers"), fetch_sub(c, sid).get("answers")
            assert item_ids(first) == item_ids(second), f"submission {sid}: item ids change between reads"
    s.check("item_ids_stable_between_reads", item_ids_stable_between_reads)

    for case, floors in V2_INVALID.items():
        def reject(floors=floors, case=case):
            payload = sub("t.sato", "2026-10-24T09:00:00Z", {"site_name": "Harbour Tower", "floors": floors})
            rejected(c.post(f"/forms/{s.need('form_id')}/submissions", json=payload), case)
        s.check(f"rejects_{case}", reject)

    def nested_invalid_not_stored():
        ids = s.need("subs")
        got = {get_id(i) for i in list_subs(c, s.need("form_id"))}
        assert got == set(ids.values()), f"expected {len(ids)} submissions on the form, found {len(got)}"
    s.check("nested_invalid_not_stored", nested_invalid_not_stored)

    for name, (params, keys) in V2_FILTERS.items():
        def filt(params=params, keys=keys):
            ids = s.need("subs")
            got = {get_id(i) for i in list_subs(c, s.need("form_id"), params)}
            want = {ids[k] for k in keys}
            assert got == want, f"{params}: got {len(got)} results, expected {len(want)} (exact set mismatch)"
        s.check(name, filt)

    def append(sid: str, path: list, group: str, item: dict) -> httpx.Response:
        return c.post(f"/submissions/{sid}/items", json={"path": path, "group": group, "item": item})

    room_path = [{"group": "floors", "id": "f-1"}, {"group": "rooms", "id": "r-1"}]

    def append_nested_item():
        sid = s.need("subs")["a"]
        body = created(append(sid, room_path, "items", {"kind": "alarm", "status": "fail", "notes": "added later"}),
                       "append nested item")
        items = find_room(fetch_sub(c, sid).get("answers") or {}, "f-1", "r-1").get("items") or []
        assert len(items) == 3, f"room has {len(items)} items after append, expected 3"
        assert any(i.get("id") == get_id(body) and i.get("notes") == "added later" for i in items), \
            "appended item (with the returned id) not found in the room"
    s.check("append_nested_item", append_nested_item)

    def append_top_level_item():
        sid = s.need("subs")["a"]
        created(append(sid, [], "contacts", {"name": "Lee Park"}), "append top-level item")
        contacts = (fetch_sub(c, sid).get("answers") or {}).get("contacts") or []
        assert [x.get("name") for x in contacts] == ["Dana Whitfield", "Lee Park"], f"contacts: {contacts}"
        s.state["contacts_full"] = True
    s.check("append_top_level_item", append_top_level_item)

    def append_respects_max_items():
        s.need("contacts_full")
        rejected(append(s.need("subs")["a"], [], "contacts", {"name": "Third Person"}), "append over maxItems")
    s.check("append_respects_max_items", append_respects_max_items)

    s.check("append_rejects_invalid_item", lambda: rejected(
        append(s.need("subs")["a"], room_path, "items", {"kind": "alarm", "status": "broken"}), "invalid item"))

    def append_unknown_path_404():
        r = append(s.need("subs")["a"], [{"group": "floors", "id": "no-such-floor"}, {"group": "rooms", "id": "r-1"}],
                   "items", {"kind": "alarm", "status": "pass"})
        assert r.status_code == 404, f"expected 404 for an unknown path, got HTTP {r.status_code}"
    s.check("append_unknown_path_404", append_unknown_path_404)

    def append_concurrent_no_lost_items():
        fid = s.need("form_id")
        payload = sub("j.ramirez", "2026-10-25T07:00:00Z", {"site_name": "Quay Warehouse", "floors": [
            {"id": "f-x", "floor_name": "Roof", "rooms": [
                {"id": "r-a", "room_name": "Plant A", "items": []},
                {"id": "r-b", "room_name": "Plant B", "items": []}]}]})
        sid = get_id(created(c.post(f"/forms/{fid}/submissions", json=payload), "POST concurrency fixture"))
        jobs = [("r-a" if n < 30 else "r-b", f"c-{n:02d}") for n in range(50)]
        base = str(c.base_url)

        def post(job):
            room, note = job
            path = [{"group": "floors", "id": "f-x"}, {"group": "rooms", "id": room}]
            with httpx.Client(base_url=base, timeout=60) as own:
                return own.post(f"/submissions/{sid}/items", json={
                    "path": path, "group": "items", "item": {"kind": "alarm", "status": "pass", "notes": note}})

        with ThreadPoolExecutor(max_workers=25) as pool:
            statuses = [r.status_code for r in pool.map(post, jobs)]
        failed = [st for st in statuses if st not in (200, 201)]
        assert not failed, f"{len(failed)}/50 concurrent appends failed (e.g. HTTP {failed[0]})"
        answers = fetch_sub(c, sid).get("answers") or {}
        for room, count in (("r-a", 30), ("r-b", 20)):
            notes = sorted(i.get("notes") for i in find_room(answers, "f-x", room).get("items") or [])
            want = sorted(n for r, n in jobs if r == room)
            assert notes == want, f"room {room}: {len(notes)} items stored, expected {count} (lost or duplicated)"
        assert_item_ids(answers, "after concurrent appends")
    s.check("append_concurrent_no_lost_items", append_concurrent_no_lost_items)

    # Acme's converted submissions are existing data: their items must have been given ids.
    def acme_items_backfilled_ids():
        snap = acme_item_snapshot(c, seed)
        assert snap, "no seeded Acme submissions"
        missing = sum(1 for ids in snap.values() for i in ids if not isinstance(i, str) or not i)
        total = sum(len(ids) for ids in snap.values())
        assert total, "no extinguisher items found in Acme's submissions"
        assert not missing, f"{missing}/{total} existing Acme items have no id"
        s.state["acme_ids"] = snap
    s.check("acme_items_backfilled_ids", acme_items_backfilled_ids)

    def acme_item_ids_stable_between_reads():
        assert acme_item_snapshot(c, seed) == s.need("acme_ids"), "Acme item ids change between reads"
    s.check("acme_item_ids_stable_between_reads", acme_item_ids_stable_between_reads)

    return s.results

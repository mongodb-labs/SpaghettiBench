"""Checks introduced by the `move_items` phase (moving items with their nested items and findings)."""
from __future__ import annotations

import httpx

from harness.suite import Suite, get_id, ok, rejected, status
from scenarios.inspections.checks.helpers import (add_finding, all_findings, burst, fetch_sub, fresh_company, get_item,
                                                  path_of, room_answers, walk_form, walk_sub)


def ids_in(answers: dict, *keys: str) -> list:
    """Ids of the items of a nested group, e.g. ids_in(answers, 'floors:f1', 'rooms')."""
    node: dict = answers
    for key in keys[:-1]:
        group, iid = key.split(":")
        node = next(i for i in node.get(group) or [] if i.get("id") == iid)
    return [i.get("id") for i in node.get(keys[-1]) or []]


def v12(c: httpx.Client, seed: dict | None) -> dict:
    s = Suite("v12")
    st = s.state
    base = str(c.base_url)

    def move(sid: str, iid: str, body: dict) -> httpx.Response:
        return c.post(f"/submissions/{sid}/items/{iid}/move", json=body)

    def fixtures():
        st["company"] = fresh_company(c, "lighthouse-estates")
        st["form"] = walk_form(c, st["company"])
        st["sub"] = walk_sub(c, st["form"])
        st["finding"] = get_id(add_finding(c, st["sub"], "i1", {"title": "Sounder dead", "severity": "high",
                                                                "dueDate": "2027-05-01"}))
    s.check("create_fixtures", fixtures)

    def room_to_floor():
        sid = s.need("sub")
        got = ok(move(sid, "r1", {"path": path_of([("floors", "f2")]), "group": "rooms"}), "move room")
        assert got.get("path") == path_of([("floors", "f2")]), f"path in the response {got.get('path')!r}"
        answers = fetch_sub(c, sid)["answers"]
        assert ids_in(answers, "floors:f1", "rooms") == ["r2"], f"first floor rooms {ids_in(answers, 'floors:f1', 'rooms')}"
        assert ids_in(answers, "floors:f2", "rooms") == ["r3", "r1"], f"second floor rooms {ids_in(answers, 'floors:f2', 'rooms')}"
        assert ids_in(answers, "floors:f2", "rooms:r1", "items") == ["i1", "i2"], "items didn't move with the room"
        assert get_item(c, sid, "i1").get("path") == path_of([("floors", "f2"), ("rooms", "r1")]), "item path not updated"
    s.check("move_room_to_other_floor", room_to_floor)

    def findings_follow():
        s.need("finding")
        assert [get_id(f) for f in get_item(c, s.need("sub"), "i1").get("findings") or []] == [s.need("finding")], \
            "finding lost in the move"
        entry = next(f for f in all_findings(c, {"companyId": s.need("company")}) if get_id(f) == s.need("finding"))
        assert entry.get("path") == path_of([("floors", "f2"), ("rooms", "r1")]), f"finding search path {entry.get('path')}"
    s.check("findings_move_with_item", findings_follow)

    def position():
        sid = s.need("sub")
        ok(move(sid, "i4", {"path": path_of([("floors", "f1"), ("rooms", "r2")]), "group": "items", "position": 0}),
           "move item to position 0")
        assert ids_in(fetch_sub(c, sid)["answers"], "floors:f1", "rooms:r2", "items") == ["i4", "i3"], "wrong position"
    s.check("move_to_position", position)

    def rejects():
        sid = s.need("sub")
        rejected(move(sid, "i3", {"path": [], "group": "floors"}), "item into a different group")
        rejected(move(sid, "r1", {"path": path_of([("floors", "f2"), ("rooms", "r1")]), "group": "rooms"}),
                 "room into itself")
        status(move(sid, "i3", {"path": path_of([("floors", "f9"), ("rooms", "r9")]), "group": "items"}), 404,
               "unknown target")
        answers = fetch_sub(c, sid)["answers"]
        assert ids_in(answers, "floors:f1", "rooms:r2", "items") == ["i4", "i3"], "a rejected move changed the submission"
    s.check("move_rejects_invalid_targets", rejects)

    def max_items():
        sid = walk_sub(c, s.need("form"), {"site_name": "Full", "contacts": [{"id": "c1", "name": "A"}], "floors": [
            {"id": "fa", "floor_name": "A", "rooms": [{"id": f"x{k}", "room_name": f"R{k}", "items": []} for k in range(4)]},
            {"id": "fb", "floor_name": "B", "rooms": [{"id": "y0", "room_name": "Extra", "items": []}]}]})
        status(move(sid, "y0", {"path": path_of([("floors", "fa")]), "group": "rooms"}), 409,
               "move into a group that is full (maxItems 4)")
    s.check("move_respects_max_items", max_items)

    def concurrent():
        sid = walk_sub(c, s.need("form"), room_answers(10, "m"))
        target = {"path": path_of([("floors", "fa"), ("rooms", "rb")]), "group": "items"}
        source = path_of([("floors", "fa"), ("rooms", "ra")])

        def call(k: int, own: httpx.Client) -> httpx.Response:
            if k < 5:
                return own.post(f"/submissions/{sid}/items/m{k:02d}/move", json=target)
            if k < 10:
                return own.patch(f"/submissions/{sid}/items/m{k:02d}", json={"notes": f"n{k}"})
            return own.post(f"/submissions/{sid}/items", json={"path": source, "group": "items",
                                                               "item": {"kind": "alarm", "status": "pass", "notes": f"new{k}"}})
        rs = burst(base, 15, call)
        assert all(r.status_code in (200, 201) for r in rs), f"statuses {sorted({r.status_code for r in rs})}"
        rooms = fetch_sub(c, sid)["answers"]["floors"][0]["rooms"]
        ra, rb = rooms[0].get("items") or [], rooms[1].get("items") or []
        assert sorted(i.get("id") for i in rb) == [f"m{k:02d}" for k in range(5)], f"moved items {[i.get('id') for i in rb]}"
        kept = {i.get("id"): i.get("notes") for i in ra}
        assert all(kept.get(f"m{k:02d}") == f"n{k}" for k in range(5, 10)), "item changes lost during moves"
        assert sorted(n for n in kept.values() if n and n.startswith("new")) == sorted(f"new{k}" for k in range(10, 15)), \
            "appended items lost during moves"
        assert len(ra) == 10 and len(rb) == 5, f"{len(ra)} + {len(rb)} items, expected 10 + 5 (lost or duplicated)"
    s.check("concurrent_moves_and_edits", concurrent)

    return s.results

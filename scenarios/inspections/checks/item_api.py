"""Checks introduced by the `item_api` phase (single items: read, change, remove)."""
from __future__ import annotations

import copy

import httpx

from harness.suite import Suite, get_id, ok, rejected, status, strip_nulls
from scenarios.inspections.checks.helpers import (PATHS, WALK_ANSWERS, WALK_FORM, burst, fetch_sub, fresh_company,
                                                  get_item, item_answers, items_with_paths, late, list_subs, path_of,
                                                  patch_item, room_answers, walk_form, walk_sub)
from scenarios.inspections.common import strip_item_ids


def v7(c: httpx.Client, seed: dict | None) -> dict:
    s = Suite("v7")
    st = s.state
    base = str(c.base_url)

    def fixtures():
        company = fresh_company(c, "pier-facilities")
        st["form"] = walk_form(c, company)
        st["sub"] = walk_sub(c, st["form"])
    s.check("create_fixtures", fixtures)

    def read(iid: str, answers: dict, group: str) -> None:
        got = get_item(c, s.need("sub"), iid)
        assert get_id(got) == iid, f"id {get_id(got)!r}"
        assert got.get("submissionId") == s.need("sub"), f"submissionId {got.get('submissionId')!r}"
        assert got.get("group") == group, f"group {got.get('group')!r}, expected {group!r}"
        assert got.get("path") == path_of(PATHS[iid]), f"path {got.get('path')!r}, expected {path_of(PATHS[iid])}"
        assert strip_nulls(strip_item_ids(got.get("answers"))) == strip_item_ids(answers), \
            f"answers {str(got.get('answers'))[:200]}"
    room = WALK_ANSWERS["floors"][0]["rooms"][0]
    s.check("get_nested_item", lambda: read("i1", item_answers(room["items"][0]), "items"))
    s.check("get_item_with_nested_group", lambda: read("r1", item_answers(room), "rooms"))
    s.check("get_top_level_item", lambda: read("c1", {"name": "Dana Whitfield"}, "contacts"))

    def nested_ids():
        got = get_item(c, s.need("sub"), "r1").get("answers") or {}
        assert [i.get("id") for i in got.get("items") or []] == ["i1", "i2"], f"items {got.get('items')}"
    s.check("item_answers_keep_nested_ids", nested_ids)

    def not_found():
        status(c.get(f"/submissions/{s.need('sub')}/items/no-such-item"), 404, "unknown item")
        status(c.get("/submissions/ffffffffffffffffffffffff/items/i1"), 404, "unknown submission")
    s.check("get_item_404", not_found)

    def stored_item(iid: str) -> dict:
        return next(i for _, i, _ in items_with_paths(fetch_sub(c, s.need("sub")).get("answers")) if i.get("id") == iid)

    def patch_sets():
        got = ok(patch_item(c, s.need("sub"), "i1", {"status": "pass", "notes": "Replaced sounder"}), "PATCH item")
        assert (got.get("answers") or {}).get("status") == "pass", f"response answers {got.get('answers')}"
        stored = stored_item("i1")
        assert (stored.get("status"), stored.get("notes"), stored.get("kind")) == ("pass", "Replaced sounder", "alarm"), \
            f"stored item {stored}"
    s.check("patch_sets_fields", patch_sets)

    def patch_null():
        ok(patch_item(c, s.need("sub"), "i2", {"reading": None}), "PATCH null")
        assert "reading" not in strip_nulls(stored_item("i2")), f"reading not removed: {stored_item('i2')}"
    s.check("patch_null_removes_field", patch_null)

    invalid = {
        "bad_option": ("i3", {"status": "broken"}),
        "unknown_key": ("i3", {"colour": "red"}),
        "number_out_of_range": ("i3", {"reading": 500}),
        "required_null": ("r2", {"room_name": None}),
        "nested_group": ("f2", {"rooms": []}),
    }
    for name, (iid, body) in invalid.items():
        def reject(iid: str = iid, body: dict = body, name: str = name):
            before = stored_item(iid)
            rejected(patch_item(c, s.need("sub"), iid, body), name)
            assert stored_item(iid) == before, "rejected PATCH changed the item"
        s.check(f"patch_rejects_{name}", reject)

    def own_version():
        fid = s.need("form")
        v2 = copy.deepcopy(WALK_FORM)
        kind = v2["fields"][1]["fields"][1]["fields"][1]["fields"][0]
        kind["options"] = kind["options"] + ["sprinkler"]
        company = ok(c.get(f"/forms/{fid}"), "GET form").get("companyId")
        ok(c.put(f"/forms/{fid}", json={**v2, "companyId": company}), "PUT form version 2")
        rejected(patch_item(c, s.need("sub"), "i3", {"kind": "sprinkler"}), "option of version 2 on a version-1 submission")
        newer = walk_sub(c, fid)
        ok(patch_item(c, newer, "i3", {"kind": "sprinkler"}), "option of version 2 on a version-2 submission")
    s.check("patch_validates_with_own_version", own_version)

    def visible_in_filters():
        s.need("sub")
        got = {get_id(x) for x in list_subs(c, s.need("form"), {"answers.floors.rooms.items.status": "fail"})}
        assert s.need("sub") not in got, "filter still finds the failed status that was changed to pass"
        got = {get_id(x) for x in list_subs(c, s.need("form"), {"answers.floors.rooms.items.notes": "Replaced sounder"})}
        assert s.need("sub") in got, "filter doesn't find the changed notes"
    s.check("patch_visible_in_filters", visible_in_filters)

    def delete():
        r = c.delete(f"/submissions/{s.need('sub')}/items/i3")
        assert r.status_code in (200, 204), f"DELETE: HTTP {r.status_code}"
        status(c.get(f"/submissions/{s.need('sub')}/items/i3"), 404, "deleted item")
        rooms = fetch_sub(c, s.need("sub"))["answers"]["floors"][0]["rooms"]
        assert next(r for r in rooms if r.get("id") == "r2").get("items") == [], "item still in the submission"
    s.check("delete_item", delete)

    def delete_subtree():
        r = c.delete(f"/submissions/{s.need('sub')}/items/r3")
        assert r.status_code in (200, 204), f"DELETE room: HTTP {r.status_code}"
        status(c.get(f"/submissions/{s.need('sub')}/items/i4"), 404, "item of the deleted room")
    s.check("delete_removes_nested_items", delete_subtree)

    def min_items():
        status(c.delete(f"/submissions/{s.need('sub')}/items/c1"), 409, "deleting the only contact (minItems 1)")
        get_item(c, s.need("sub"), "c1")
    s.check("delete_respects_min_items", min_items)

    def concurrent_patches():
        sid = walk_sub(c, s.need("form"), room_answers(30))
        rs = burst(base, 30, lambda k, own: own.patch(f"/submissions/{sid}/items/k{k:02d}", json={"notes": f"n{k}"}))
        bad = [r.status_code for r in rs if r.status_code != 200]
        assert not bad, f"{len(bad)}/30 PATCHes failed (e.g. HTTP {bad[0]})"
        items = fetch_sub(c, sid)["answers"]["floors"][0]["rooms"][0]["items"]
        lost = [i.get("id") for i in items if i.get("notes") != f"n{int(i['id'][1:])}"]
        assert len(items) == 30 and not lost, f"{len(lost)}/30 concurrent changes to different items were lost"
    s.check("concurrent_patches_of_different_items", concurrent_patches)

    def concurrent_mixed():
        sid = walk_sub(c, s.need("form"), room_answers(15))
        path = [{"group": "floors", "id": "fa"}, {"group": "rooms", "id": "ra"}]

        def call(k: int, own: httpx.Client) -> httpx.Response:
            if k < 15:
                return own.patch(f"/submissions/{sid}/items/k{k:02d}", json={"notes": f"n{k}"})
            return own.post(f"/submissions/{sid}/items", json={"path": path, "group": "items",
                                                               "item": {"kind": "exit_sign", "status": "pass",
                                                                        "notes": f"new{k}"}})
        rs = burst(base, 30, call)
        bad = [r.status_code for r in rs if r.status_code not in (200, 201)]
        assert not bad, f"{len(bad)}/30 requests failed (e.g. HTTP {bad[0]})"
        notes = sorted(i.get("notes") for i in fetch_sub(c, sid)["answers"]["floors"][0]["rooms"][0]["items"])
        want = sorted([f"n{k}" for k in range(15)] + [f"new{k}" for k in range(15, 30)])
        assert notes == want, f"{len(set(want) - set(notes))} of 30 concurrent changes and appends lost"
    s.check("concurrent_patches_and_appends", concurrent_mixed)

    if late(seed):
        def seeded_items():
            for x in late(seed)["submissions"][1:6]:
                for group, item, path in items_with_paths(fetch_sub(c, x["id"]).get("answers")):
                    got = get_item(c, x["id"], item["id"])
                    assert (got.get("group"), got.get("path")) == (group, path), \
                        f"submission {x['id']} item {item['id']}: group/path {got.get('group')!r} {got.get('path')!r}"
                    assert strip_nulls(got.get("answers")) == item_answers(item), \
                        f"submission {x['id']} item {item['id']}: answers differ"
        s.check("seeded_items_readable", seeded_items)

        def seeded_patch():
            sid = late(seed)["submissions"][0]["id"]
            first = items_with_paths(fetch_sub(c, sid).get("answers"))
            item = next(i for g, i, _ in first if g == "items")
            ok(patch_item(c, sid, item["id"], {"serial": "SN-RECHECKED"}), "PATCH an existing item")
            assert get_item(c, sid, item["id"])["answers"].get("serial") == "SN-RECHECKED", "change not stored"
        s.check("seeded_patch_existing_item", seeded_patch)

    return s.results

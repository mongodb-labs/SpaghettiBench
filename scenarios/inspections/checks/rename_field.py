"""Checks introduced by the `rename_field` phase (improvement: Tidewater's notes become remarks)."""
from __future__ import annotations

import httpx

from harness.suite import Suite, created, get_id, ok
from scenarios.inspections.checks.helpers import (fetch_sub, fresh_company, get_item, items_with_paths, late,
                                                  list_subs, patch_item, walk_form, walk_sub)

ITEMS = "answers.floors.rooms.items"


def item_fields(form: dict) -> dict[str, dict]:
    floors = next(f for f in form.get("fields") or [] if f.get("key") == "floors")
    rooms = next(f for f in floors.get("fields") or [] if f.get("key") == "rooms")
    items = next(f for f in rooms.get("fields") or [] if f.get("key") == "items")
    return {f.get("key"): f for f in items.get("fields") or []}


def i3(c: httpx.Client, seed: dict | None) -> dict:
    s = Suite("i3")
    st = s.state

    def form():
        assert late(seed) and late(seed)["forms"], "Tidewater's form is missing"
        fid = late(seed)["forms"][0]["id"]
        for path in (f"/forms/{fid}", f"/forms/{fid}/versions/1"):
            fields = item_fields(ok(c.get(path), f"GET {path}"))
            assert "notes" not in fields and fields.get("remarks", {}).get("label") == "Remarks", \
                f"{path}: item fields {sorted(fields)}"
        st["form"] = fid
    s.check("form_renamed_in_every_version", form)

    def answers():
        bad, total = [], 0
        for x in late(seed)["submissions"]:  # type: ignore[index]
            sent = [i for g, i, _ in items_with_paths(x["payload"]["answers"]) if g == "items"]
            stored = [i for g, i, _ in items_with_paths(fetch_sub(c, x["id"]).get("answers")) if g == "items"]
            for a, b in zip(sent, stored):
                total += "notes" in a
                if "notes" in b or b.get("remarks") != a.get("notes"):
                    bad.append(f"{x['id']}/{b.get('id')}")
        assert total, "no Tidewater items with notes"
        assert not bad, f"{len(bad)} existing items not renamed, e.g. {bad[:3]}"
    s.check("existing_answers_renamed", answers)

    def old_key_writes():
        fid = s.need("form")
        sid = get_id(created(c.post(f"/forms/{fid}/submissions", json={
            "submittedBy": "k.mensah", "submittedAt": "2027-05-26T08:00:00Z", "answers": {
                "site_name": "Marina Point", "floors": [{"id": "rf", "floor_name": "Ground", "rooms": [
                    {"id": "rr", "room_name": "Lobby", "items": [
                        {"id": "ri", "kind": "alarm", "status": "pass", "notes": "Old tablet"}]}]}]}}),
            "submission with the old key"))
        assert get_item(c, sid, "ri")["answers"].get("remarks") == "Old tablet", "old key not stored as remarks"
        body = created(c.post(f"/submissions/{sid}/items", json={
            "path": [{"group": "floors", "id": "rf"}, {"group": "rooms", "id": "rr"}], "group": "items",
            "item": {"kind": "exit_sign", "status": "pass", "notes": "Appended"}}), "append with the old key")
        assert get_item(c, sid, get_id(body))["answers"].get("remarks") == "Appended", "appended notes not stored as remarks"
        ok(patch_item(c, sid, "ri", {"notes": "Patched"}), "PATCH with the old key")
        got = get_item(c, sid, "ri")["answers"]
        assert got.get("remarks") == "Patched" and "notes" not in got, f"answers {got}"
        st["sub"] = sid
    s.check("old_key_accepted_in_writes", old_key_writes)

    def old_key_filter():
        fid = s.need("form")
        new = {get_id(x) for x in list_subs(c, fid, {f"{ITEMS}.remarks": "Blocked by furniture"})}
        old = {get_id(x) for x in list_subs(c, fid, {f"{ITEMS}.notes": "Blocked by furniture"})}
        assert new and new == old, f"filter with notes found {len(old)}, with remarks {len(new)}"
    s.check("old_key_in_filters", old_key_filter)

    def old_key_report():
        fid = s.need("form")
        rows = {}
        for key in ("remarks", "notes"):
            r = ok(c.get(f"/forms/{fid}/report", params={"groupBy": f"{ITEMS}.{key}"}), f"report by {key}")
            # "Responses always use remarks", so a notes report may name its group property either way.
            rows[key] = sorted((str(g.get(f"{ITEMS}.{key}", g.get(f"{ITEMS}.remarks"))), g.get("count"))
                               for g in r.get("groups") or [])
        assert rows["remarks"] and rows["remarks"] == rows["notes"], "report by notes differs from report by remarks"
    s.check("old_key_in_reports", old_key_report)

    def other_forms():
        fid = walk_form(c, fresh_company(c, "inland-depots"))
        sid = walk_sub(c, fid)
        assert get_item(c, sid, "i1")["answers"].get("notes") == "Sounder dead", "another form's notes changed"
        assert sid in {get_id(x) for x in list_subs(c, fid, {f"{ITEMS}.notes": "Sounder dead"})}, \
            "another form's notes filter broke"
    s.check("other_forms_keep_notes", other_forms)

    return s.results

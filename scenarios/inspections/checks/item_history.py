"""Checks introduced by the `item_history` phase (who changed what, and when)."""
from __future__ import annotations

import httpx

from harness.suite import Suite, created, get_id, ok, status, strip_nulls
from scenarios.inspections.checks.helpers import (add_finding, burst, fresh_company, get_item, patch_finding,
                                                  patch_item, path_of, walk_form, walk_sub)
from scenarios.inspections.common import parse_instant


def v14(c: httpx.Client, seed: dict | None) -> dict:
    s = Suite("v14")
    st = s.state
    base = str(c.base_url)

    def history(sid: str, iid: str) -> list[dict]:
        r = c.get(f"/submissions/{sid}/items/{iid}/history")
        assert r.status_code == 200, f"GET history of {iid}: HTTP {r.status_code} {r.text[:150]}"
        body = r.json()
        return body if isinstance(body, list) else body.get("items") or body.get("history") or []

    def fixtures():
        company = fresh_company(c, "riverside-trust")
        st["user"] = get_id(created(c.post(f"/companies/{company}/users", json={
            "name": "Audrey Auditor", "email": f"audrey@{company}.example", "role": "manager"}), "user"))
        st["form"] = walk_form(c, company)
        st["sub"] = walk_sub(c, st["form"])
        st["company"] = company
        body = created(c.post(f"/submissions/{st['sub']}/items", json={
            "path": path_of([("floors", "f1"), ("rooms", "r1")]), "group": "items",
            "item": {"id": "h1", "kind": "alarm", "status": "fail"}}), "append")
        st["item"] = get_id(body) or "h1"
    s.check("create_fixtures", fixtures)

    def created_entry():
        entries = history(s.need("sub"), s.need("item"))
        assert [e.get("action") for e in entries] == ["created"], f"actions {[e.get('action') for e in entries]}"
        assert entries[0].get("by") is None and parse_instant(entries[0].get("at")), f"entry {entries[0]}"
    s.check("append_recorded_as_created", created_entry)

    def new_submission_items():
        sid = walk_sub(c, s.need("form"))
        assert [e.get("action") for e in history(sid, "i4")] == ["created"], "items of a new submission not recorded"
    s.check("new_submission_items_recorded", new_submission_items)

    def lifecycle():
        sid, iid, user = s.need("sub"), s.need("item"), s.need("user")
        ok(patch_item(c, sid, iid, {"status": "pass", "notes": "Fixed"}), "PATCH")
        fid = get_id(add_finding(c, sid, iid, {"title": "Retest", "severity": "low", "dueDate": "2027-06-01"}))
        ok(patch_finding(c, sid, iid, fid, {"status": "in_progress"}), "PATCH finding")
        comment = ok(c.post(f"/submissions/{sid}/items/{iid}/findings/{fid}/comments",
                            json={"author": "tech", "text": "Booked"}), "comment")
        ok(c.post(f"/submissions/{sid}/items/{iid}/move", json={"path": path_of([("floors", "f1"), ("rooms", "r2")]),
                                                                "group": "items"}), "move")
        ok(patch_item(c, sid, iid, {"notes": "Audited"}, headers={"X-User-Id": user}), "PATCH as a user")
        r = c.delete(f"/submissions/{sid}/items/{iid}")
        assert r.status_code in (200, 204), f"DELETE: HTTP {r.status_code}"
        st.update(fid=fid, comment=get_id(comment))
        st["entries"] = history(sid, iid)
    s.check("changes_recorded", lifecycle)

    def actions():
        got = [e.get("action") for e in s.need("entries")]
        want = ["created", "updated", "finding_added", "finding_updated", "comment_added", "moved", "updated", "deleted"]
        assert got == want, f"actions {got}, expected {want}"
    s.check("history_in_order", actions)

    def details():
        e = s.need("entries")
        upd = e[1]
        assert strip_nulls(upd.get("before") or {}) == {"status": "fail"} and \
            upd.get("after") == {"status": "pass", "notes": "Fixed"}, f"update entry {upd}"
        assert e[2].get("findingId") == s.need("fid"), f"finding_added {e[2]}"
        assert (e[3].get("findingId"), (e[3].get("before") or {}).get("status"), (e[3].get("after") or {}).get("status")) \
            == (s.need("fid"), "open", "in_progress"), f"finding_updated {e[3]}"
        assert (e[4].get("findingId"), e[4].get("commentId")) == (s.need("fid"), s.need("comment")), f"comment_added {e[4]}"
        assert (e[5].get("fromPath"), e[5].get("toPath")) == (path_of([("floors", "f1"), ("rooms", "r1")]),
                                                              path_of([("floors", "f1"), ("rooms", "r2")])), f"moved {e[5]}"
        assert e[6].get("by") == s.need("user") and all(x.get("by") is None for x in e[:6]), \
            f"by: {[x.get('by') for x in e]}"
    s.check("history_details", details)

    def deleted_readable():
        s.need("entries")
        status(c.get(f"/submissions/{s.need('sub')}/items/{s.need('item')}"), 404, "deleted item")
    s.check("deleted_item_history_readable", deleted_readable)

    def bulk_recorded():
        sid = s.need("sub")
        fid = get_id(add_finding(c, sid, "i2", {"title": "Bulk me", "severity": "low", "dueDate": "2027-06-01"}))
        ok(c.post("/findings/bulk", json={"companyId": s.need("company"), "filter": {"submissionId": sid},
                                          "set": {"assignee": "bulk-user"}}), "bulk")
        last = history(sid, "i2")[-1]
        assert (last.get("action"), last.get("findingId"), (last.get("after") or {}).get("assignee")) == \
            ("finding_updated", fid, "bulk-user"), f"last entry {last}"
    s.check("bulk_changes_recorded", bulk_recorded)

    s.check("unknown_item_404", lambda: status(c.get(f"/submissions/{s.need('sub')}/items/nope/history"), 404,
                                               "history of an unknown item"))

    def concurrent():
        sid = s.need("sub")
        before = len(history(sid, "i3"))
        rs = burst(base, 20, lambda k, own: own.patch(f"/submissions/{sid}/items/i3", json={"notes": f"n{k}"}))
        assert all(r.status_code == 200 for r in rs), f"statuses {sorted({r.status_code for r in rs})}"
        entries = history(sid, "i3")[before:]
        assert len(entries) == 20 and all(e.get("action") == "updated" for e in entries), \
            f"{len(entries)} entries for 20 concurrent changes"
        assert (entries[-1].get("after") or {}).get("notes") == get_item(c, sid, "i3")["answers"].get("notes"), \
            "the last entry doesn't match the item"
    s.check("concurrent_changes_recorded_once", concurrent)

    return s.results

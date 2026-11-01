"""Checks introduced by the `snapshots` phase (kept document versions and restore)."""
from __future__ import annotations

import httpx

from harness.suite import Suite, created, get_id, ok, status
from scenarios.inspections.checks.helpers import (add_finding, doc_tree, first_difference, fresh_company, get_doc,
                                                  get_item, late, patch_finding, patch_item, path_of, walk_form,
                                                  walk_sub)
from scenarios.inspections.common import parse_instant


def v19(c: httpx.Client, seed: dict | None) -> dict:
    s = Suite("v19")
    st = s.state

    def revisions(sid: str) -> list:
        got = ok(c.get(f"/submissions/{sid}/revisions"), "GET revisions")
        assert isinstance(got, list), f"revisions {str(got)[:150]}"
        return got

    def fixtures():
        company = fresh_company(c, "kestrel-works")
        sid = walk_sub(c, walk_form(c, company))
        user = get_id(created(c.post(f"/companies/{company}/users", json={
            "name": "Ines Duarte", "email": "ines@kestrel.example", "role": "manager"}), "user"))
        f = get_id(add_finding(c, sid, "i1", {"title": "Sounder dead", "severity": "high"}))
        ok(c.post(f"/submissions/{sid}/items/i1/findings/{f}/comments", json={"author": "w-1", "text": "Seen"}), "comment")
        st.update(sid=sid, user=user, f=f, before=get_doc(c, sid))
        ok(patch_item(c, sid, "i1", {"notes": "Changed"}, headers={"X-User-Id": user}), "PATCH")
        ok(patch_finding(c, sid, "i1", f, {"status": "resolved"}), "resolve")
        add_finding(c, sid, "i2", {"title": "Later finding", "severity": "low"})
        assert c.delete(f"/submissions/{sid}/items/i3").status_code in (200, 204), "DELETE"
        created(c.post(f"/submissions/{sid}/items", json={"path": path_of([("floors", "f1"), ("rooms", "r1")]),
                                                          "group": "items", "item": {"kind": "alarm", "status": "pass",
                                                                                     "notes": "Later item"}}), "append")
        st["latest"] = get_doc(c, sid)
    s.check("create_fixtures", fixtures)

    def listed():
        d = s.need("before")["docRev"]
        got = revisions(s.need("sid"))
        revs = [e.get("docRev") for e in got]
        assert revs == sorted(revs) and set(range(d, d + 6)) <= set(revs), f"docRevs {revs}, expected {d}..{d + 5}"
        by = {e.get("docRev"): e.get("by") for e in got}
        assert by[d + 1] == s.need("user") and by[d + 2] is None, f"by {by[d + 1]!r} and {by[d + 2]!r}"
        assert all(parse_instant(e.get("at")) for e in got), "revision without a valid at"
    s.check("revisions_listed", listed)

    def old_version():
        before = s.need("before")
        got = get_doc(c, s.need("sid"), docRev=before["docRev"])
        assert got.get("docRev") == before["docRev"], f"docRev {got.get('docRev')!r}"
        first_difference(doc_tree(before), doc_tree(got), "kept version")
        assert set(got.get("items") or {}) == set(before.get("items") or {}), "kept version has other item ids"
    s.check("old_version_readable", old_version)

    s.check("unknown_version_404", lambda: status(c.get(f"/submissions/{s.need('sid')}/document",
                                                        params={"docRev": 9999}), 404, "docRev 9999"))

    def restore():
        sid, before = s.need("sid"), s.need("before")
        st["rev_i1"] = get_item(c, sid, "i1").get("rev")
        got = ok(c.post(f"/submissions/{sid}/restore", json={"docRev": before["docRev"]}), "restore")
        assert got.get("docRev") == s.need("latest")["docRev"] + 1, \
            f"docRev {got.get('docRev')!r} after the restore, expected {s.need('latest')['docRev'] + 1}"
        now = get_doc(c, sid)
        first_difference(doc_tree(before), doc_tree(now), "restored submission")
        assert set(now.get("items") or {}) == set(before.get("items") or {}), "restored items have other ids"
        f = next(x for x in now["items"]["i1"]["findings"] if get_id(x) == s.need("f"))
        assert f.get("status") == "open" and f.get("resolvedAt") is None, f"restored finding {f.get('status')!r}"
        st["restored"] = True
    s.check("restore_version", restore)

    def revs_and_history():
        s.need("restored")
        sid, d = s.need("sid"), s.need("before")["docRev"]
        rev = get_item(c, sid, "i1").get("rev")
        assert isinstance(rev, int) and rev > s.need("rev_i1"), f"rev of i1 {rev!r}, was {s.need('rev_i1')}"
        status(patch_item(c, sid, "i1", {"notes": "Stale tablet"}, ifRev=s.need("rev_i1")), 409, "stale ifRev after restore")
        for iid in ("i1", "i3"):
            hist = ok(c.get(f"/submissions/{sid}/items/{iid}/history"), "history")
            last = hist[-1] if hist else {}
            assert (last.get("action"), last.get("docRev")) == ("restored", d), f"last history entry of {iid}: {last}"
        assert "restored" not in [e.get("action") for e in ok(c.get(f"/submissions/{sid}/items/i4/history"), "history")], \
            "unchanged item i4 got a restored entry"
    s.check("restore_bumps_revs_and_history", revs_and_history)

    def kept_after():
        s.need("restored")
        sid, latest = s.need("sid"), s.need("latest")
        first_difference(doc_tree(latest), doc_tree(get_doc(c, sid, docRev=latest["docRev"])), "version before the restore")
        assert latest["docRev"] + 1 in [e.get("docRev") for e in revisions(sid)], "the restore is not listed"
    s.check("versions_kept_after_restore", kept_after)

    s.check("restore_unknown_404", lambda: status(c.post(f"/submissions/{s.need('sid')}/restore", json={"docRev": 9999}),
                                                  404, "restore docRev 9999"))

    if late(seed):
        def seeded():
            sid = late(seed)["submissions"][8]["id"]
            doc = get_doc(c, sid)
            first_difference(doc_tree(doc), doc_tree(get_doc(c, sid, docRev=doc["docRev"])), "current version")
            assert doc["docRev"] in [e.get("docRev") for e in revisions(sid)], "current version not listed"
        s.check("seeded_current_version_kept", seeded)

    return s.results

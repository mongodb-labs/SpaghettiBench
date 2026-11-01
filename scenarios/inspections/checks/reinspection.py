"""Checks introduced by the `reinspection` phase (a new inspection copied from an old one)."""
from __future__ import annotations

import httpx

from harness.suite import Suite, get_id, ok, rejected, status, strip_nulls
from scenarios.inspections.checks.helpers import (add_finding, finding_of, fresh_company, get_doc, get_item,
                                                  items_with_paths, late, patch_finding, patch_item, walk_form,
                                                  walk_sub)
from scenarios.inspections.common import strip_item_ids

WHEN = {"submittedBy": "a.ruiz", "submittedAt": "2027-09-01T09:00:00Z"}


def v20(c: httpx.Client, seed: dict | None) -> dict:
    s = Suite("v20")
    st = s.state

    def reinspect(sid: str) -> httpx.Response:
        return c.post(f"/submissions/{sid}/reinspect", json=WHEN)

    def fixtures():
        company = fresh_company(c, "brook-lane")
        ok(c.post(f"/companies/{company}/item-types", json={"code": "EXT-9", "name": "9 l foam", "intervalDays": 365}),
           "item type")
        sid = walk_sub(c, walk_form(c, company))
        ok(c.put(f"/submissions/{sid}/items/i2/type", json={"code": "EXT-9"}), "set type")
        f1 = get_id(add_finding(c, sid, "i1", {"title": "Sounder dead", "severity": "high", "assignee": "w-1",
                                               "dueDate": "2027-09-20"}))
        ok(c.post(f"/submissions/{sid}/items/i1/findings/{f1}/comments", json={"author": "w-1", "text": "Ordered"}),
           "comment")
        f2 = get_id(add_finding(c, sid, "i2", {"title": "Pin missing", "severity": "low"}))
        ok(patch_finding(c, sid, "i2", f2, {"status": "resolved"}), "resolve")
        f3 = get_id(add_finding(c, sid, "r1", {"title": "Door wedged", "severity": "medium", "dueDate": "2027-10-01"}))
        ok(patch_finding(c, sid, "r1", f3, {"status": "in_progress"}), "start")
        st.update(sid=sid, f1=f1, f3=f3, before=get_doc(c, sid))
    s.check("create_fixtures", fixtures)

    def copy_made():
        sid = s.need("sid")
        r = reinspect(sid)
        status(r, 201, "reinspect")
        new = r.json()
        st["new"] = new
        assert get_id(new) not in (None, sid), f"id {get_id(new)!r}"
        assert (new.get("formId"), new.get("formVersion"), new.get("submittedBy"), new.get("previousSubmissionId")) == \
            (s.need("before")["formId"], s.need("before")["formVersion"], "a.ruiz", sid), \
            f"formId/formVersion/submittedBy/previousSubmissionId {[new.get(k) for k in ('formId', 'formVersion', 'submittedBy', 'previousSubmissionId')]}"
        assert strip_nulls(strip_item_ids(new.get("answers"))) == strip_nulls(strip_item_ids(s.need("before")["answers"])), \
            "answers are not a copy"
        old_ids = set(s.need("before")["items"])
        assert not set(new.get("items") or {}) & old_ids, "new items keep the old ids"
        assert new.get("docRev") == 1 and {e.get("rev") for e in (new.get("items") or {}).values()} == {1}, \
            "docRev and revs don't start at 1"
        pairs = {}
        for (_, a, _), (_, b, _) in zip(items_with_paths(s.need("before")["answers"]), items_with_paths(new["answers"])):
            pairs[a["id"]] = b["id"]
        st["pairs"] = pairs
    s.check("reinspect_copies_the_inspection", copy_made)

    def linked():
        new, pairs = s.need("new"), s.need("pairs")
        bad = [old for old, n in pairs.items() if (new["items"].get(n) or {}).get("previousItemId") != old]
        assert not bad, f"{len(bad)} items without the right previousItemId in the document, e.g. {bad[:3]}"
        item = get_item(c, get_id(new), pairs["i2"])
        assert item.get("previousItemId") == "i2", f"previousItemId {item.get('previousItemId')!r} in GET item"
        assert (item.get("type") or {}).get("code") == "EXT-9", "type not copied"
        assert get_item(c, s.need("sid"), "i2").get("previousItemId") is None, "original item has a previousItemId"
        assert get_doc(c, s.need("sid")).get("previousSubmissionId") is None, "original has a previousSubmissionId"
    s.check("items_linked_to_originals", linked)

    def carried():
        new, pairs = s.need("new"), s.need("pairs")
        nid = get_id(new)
        got = get_item(c, nid, pairs["i1"]).get("findings") or []
        assert len(got) == 1, f"{len(got)} findings on the copy of i1, expected 1"
        f = got[0]
        want = {"title": "Sounder dead", "severity": "high", "assignee": "w-1", "dueDate": "2027-09-20", "status": "open",
                "comments": [], "carriedOverFrom": s.need("f1")}
        assert {k: f.get(k) for k in want} == want and get_id(f) != s.need("f1"), f"carried-over finding {f}"
        r1 = get_item(c, nid, pairs["r1"]).get("findings") or []
        assert [(x.get("title"), x.get("status")) for x in r1] == [("Door wedged", "open")], f"copy of r1 findings {r1}"
        assert not get_item(c, nid, pairs["i2"]).get("findings"), "a resolved finding was carried over"
        assert finding_of(c, s.need("sid"), "i1", s.need("f1")).get("carriedOverFrom") is None, \
            "original finding has carriedOverFrom"
    s.check("open_findings_carried_over", carried)

    def history():
        new, pairs = s.need("new"), s.need("pairs")
        hist = [e.get("action") for e in ok(c.get(f"/submissions/{get_id(new)}/items/{pairs['i1']}/history"), "history")]
        assert hist == ["created", "finding_added"], f"history of a copied item {hist}"
    s.check("history_of_copies", history)

    def original_unchanged():
        s.need("new")
        sid, before = s.need("sid"), s.need("before")
        now = get_doc(c, sid)
        assert now.get("docRev") == before["docRev"], f"original docRev {before['docRev']} -> {now.get('docRev')}"
        assert finding_of(c, sid, "r1", s.need("f3")).get("status") == "in_progress", "original finding changed"
        new, pairs = s.need("new"), s.need("pairs")
        ok(patch_item(c, get_id(new), pairs["i1"], {"notes": "Copy only"}), "PATCH the copy")
        assert get_item(c, sid, "i1")["answers"].get("notes") == "Sounder dead", "changing the copy changed the original"
    s.check("original_unchanged", original_unchanged)

    def invalid():
        rejected(c.post(f"/submissions/{s.need('sid')}/reinspect", json={"submittedAt": WHEN["submittedAt"]}),
                 "reinspect without submittedBy")
        status(reinspect("no-such-submission"), 404, "reinspect an unknown submission")
    s.check("rejects_invalid_reinspection", invalid)

    if late(seed):
        def seeded():
            sid = late(seed)["submissions"][9]["id"]
            before = get_doc(c, sid)
            r = reinspect(sid)
            status(r, 201, "reinspect a seeded submission")
            new = r.json()
            assert strip_nulls(strip_item_ids(new.get("answers"))) == strip_nulls(strip_item_ids(before["answers"])), \
                "answers are not a copy"
            want = sorted(f.get("title") for e in before["items"].values() for f in e.get("findings") or []
                          if f.get("status") != "resolved")
            got = sorted(f.get("title") for e in (new.get("items") or {}).values() for f in e.get("findings") or [])
            assert got == want, f"carried-over findings {got}, expected {want}"
        s.check("seeded_reinspection", seeded)

    return s.results

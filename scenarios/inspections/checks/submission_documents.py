"""Checks introduced by the `submission_documents` phase (download and upload a whole submission)."""
from __future__ import annotations

import copy

import httpx

from harness.suite import Suite, get_id, ok, rejected, status, strip_nulls
from scenarios.inspections.checks.helpers import (add_finding, burst, fetch_sub, find, finding_of, fresh_company,
                                                  get_doc, get_item, items_with_paths, late, patch_item, path_of,
                                                  put_doc, room_answers, walk_form, walk_sub)

ALL_IDS = {"f1", "r1", "i1", "i2", "r2", "i3", "f2", "r3", "i4", "c1"}


def v17(c: httpx.Client, seed: dict | None) -> dict:
    s = Suite("v17")
    st = s.state
    base = str(c.base_url)

    def fixtures():
        st["form"] = walk_form(c, fresh_company(c, "lighthouse-estates"))
        st["sub"] = walk_sub(c, st["form"])
        st["f"] = get_id(add_finding(c, st["sub"], "i1", {"title": "Sounder dead", "severity": "high"}))
    s.check("create_fixtures", fixtures)

    def shape():
        sid = s.need("sub")
        doc = get_doc(c, sid)
        assert (get_id(doc), doc.get("formId"), doc.get("formVersion")) == (sid, s.need("form"), 1), \
            f"id/formId/formVersion {get_id(doc)!r} {doc.get('formId')!r} {doc.get('formVersion')!r}"
        assert strip_nulls(doc.get("answers")) == strip_nulls(fetch_sub(c, sid).get("answers")), \
            "answers differ from GET /submissions/:id"
        items = doc.get("items") or {}
        assert set(items) == ALL_IDS, f"items entries {sorted(items)}"
        assert [get_id(f) for f in items["i1"].get("findings") or []] == [s.need("f")], "findings of i1"
        i1 = items["i1"]["findings"][0]
        assert (i1.get("title"), i1.get("comments")) == ("Sounder dead", []), f"finding in the document {i1}"
        assert (items["i2"].get("rev"), items["i2"].get("typeCode"), items["i2"].get("findings")) == (1, None, []), \
            f"entry of i2 {items['i2']}"
    s.check("document_has_everything", shape)

    def counts():
        sid = walk_sub(c, s.need("form"))
        rev = lambda: get_doc(c, sid).get("docRev")
        seen = [rev()]
        ok(patch_item(c, sid, "i2", {"notes": "x"}), "PATCH")
        seen.append(rev())
        fid = get_id(add_finding(c, sid, "i2", {"title": "t", "severity": "low"}))
        seen.append(rev())
        ok(c.post(f"/submissions/{sid}/items/i2/findings/{fid}/comments", json={"author": "a", "text": "b"}), "comment")
        seen.append(rev())
        rejected(patch_item(c, sid, "i2", {"status": "broken"}), "invalid PATCH")
        get_item(c, sid, "i2")
        seen.append(rev())
        ok(c.post(f"/submissions/{sid}/items/i2/move", json={"path": path_of([("floors", "f2"), ("rooms", "r3")]),
                                                             "group": "items"}), "move")
        seen.append(rev())
        assert c.delete(f"/submissions/{sid}/items/i3").status_code in (200, 204), "DELETE"
        seen.append(rev())
        assert seen == [1, 2, 3, 4, 4, 5, 6], f"docRev after create, PATCH, finding, comment, a rejected PATCH, " \
                                              f"move, delete: {seen}, expected [1, 2, 3, 4, 4, 5, 6]"
    s.check("doc_rev_counts_changes", counts)

    def upload():
        sid = s.need("sub")
        doc = get_doc(c, sid)
        st["rev0"] = doc.get("docRev")
        answers = copy.deepcopy(doc["answers"])
        find(answers, "i2")["notes"] = "Re-read"
        kitchen, lobby, office = find(answers, "r2"), find(answers, "r1"), find(answers, "r3")
        kitchen["items"] = [i for i in kitchen["items"] if i.get("id") != "i3"] + \
            [{"kind": "exit_sign", "status": "pass", "notes": "New sign"}]
        lobby["items"].append(next(i for i in office["items"] if i.get("id") == "i4"))
        office["items"] = [i for i in office["items"] if i.get("id") != "i4"]
        items = copy.deepcopy(doc["items"])
        items.pop("i3")
        f = items["i1"]["findings"][0]
        f["status"] = "in_progress"
        f["comments"].append({"author": "k.mensah", "text": "Part ordered"})
        items["i2"]["findings"] = [{"title": "Loose bracket", "severity": "low"}]
        got = ok(put_doc(c, sid, {**doc, "answers": answers, "items": items}, st["rev0"]), "PUT document")
        assert got.get("docRev") == st["rev0"] + 1, f"docRev {got.get('docRev')!r} after the upload, expected {st['rev0'] + 1}"
        st["uploaded"] = True
    s.check("upload_document", upload)

    def applied():
        s.need("uploaded")
        sid = s.need("sub")
        assert get_item(c, sid, "i2")["answers"].get("notes") == "Re-read", "changed answer not stored"
        assert c.get(f"/submissions/{sid}/items/i3").status_code == 404, "left-out item still exists"
        new = [i for _, i, _ in items_with_paths(fetch_sub(c, sid)["answers"]) if i.get("notes") == "New sign"]
        assert len(new) == 1 and new[0].get("id"), f"new item {new}"
        st["new"] = new[0]["id"]
        assert get_item(c, sid, "i4").get("path") == path_of([("floors", "f1"), ("rooms", "r1")]), "item not moved"
        f = finding_of(c, sid, "i1", s.need("f"))
        assert f.get("status") == "in_progress" and [x.get("text") for x in f.get("comments") or []] == ["Part ordered"], \
            f"finding {f.get('status')!r}, comments {[x.get('text') for x in f.get('comments') or []]}"
        got = [(x.get("title"), x.get("status")) for x in get_item(c, sid, "i2").get("findings") or []]
        assert got == [("Loose bracket", "open")], f"findings of i2 {got}"
    s.check("upload_applies_every_change", applied)

    def like_single():
        sid, new = s.need("sub"), s.need("new")
        revs = {i: get_item(c, sid, i).get("rev") for i in ("i1", "i2", "i4", "c1")}
        assert revs == {"i1": 1, "i2": 2, "i4": 2, "c1": 1}, f"revs {revs}, expected i2 and i4 (changed, moved) at 2"
        actions = lambda iid: [e.get("action") for e in ok(c.get(f"/submissions/{sid}/items/{iid}/history"), "history")]
        hist = ok(c.get(f"/submissions/{sid}/items/i2/history"), "history")
        assert any(e.get("action") == "updated" and (e.get("after") or {}).get("notes") == "Re-read" for e in hist), \
            f"i2 history {[e.get('action') for e in hist]}"
        for iid, action in (("i3", "deleted"), ("i4", "moved"), ("i1", "finding_updated"), ("i1", "comment_added"),
                            ("i2", "finding_added"), (new, "created")):
            assert action in actions(iid), f"history of {iid} has no {action}: {actions(iid)}"
    s.check("upload_counts_like_single_changes", like_single)

    def stale():
        s.need("uploaded")
        sid = s.need("sub")
        doc = get_doc(c, sid)
        r = put_doc(c, sid, {"answers": {**doc["answers"], "site_name": "Stale"}}, s.need("rev0"))
        status(r, 409, "upload with a stale ifDocRev")
        assert r.json().get("docRev") == doc["docRev"], f"409 body docRev {r.json().get('docRev')!r}"
        assert fetch_sub(c, sid)["answers"]["site_name"] == "Pier 4", "stale upload applied"
        rejected(c.put(f"/submissions/{sid}/document", json={"answers": doc["answers"]}), "upload without ifDocRev")
    s.check("stale_upload_409", stale)

    def all_or_nothing():
        sid = s.need("sub")
        doc = get_doc(c, sid)

        def bad(change, what: str) -> None:
            body = copy.deepcopy({"answers": doc["answers"], "items": doc["items"]})
            find(body["answers"], "i2")["notes"] = "Must not stay"
            change(body)
            rejected(put_doc(c, sid, body, doc["docRev"]), what)
        bad(lambda b: find(b["answers"], "i1").update(status="broken"), "an invalid answer")
        bad(lambda b: b["items"]["i1"]["findings"].append({"id": "no-such-finding", "status": "resolved"}),
            "an unknown finding id")
        bad(lambda b: find(b["answers"], "r1")["items"].append({"id": "i2", "kind": "alarm", "status": "pass"}),
            "the same item id twice")
        bad(lambda b: b["items"]["i1"]["findings"].append({"severity": "low"}), "a new finding without title")
        assert get_item(c, sid, "i2")["answers"].get("notes") == "Re-read", "a rejected upload changed an item"
        assert get_doc(c, sid).get("docRev") == doc["docRev"], "a rejected upload changed docRev"
    s.check("invalid_upload_changes_nothing", all_or_nothing)

    def one_winner():
        sid = s.need("sub")
        doc = get_doc(c, sid)
        rs = burst(base, 10, lambda k, own: put_doc(own, sid, {"answers": {**doc["answers"], "site_name": f"Tablet {k}"}},
                                                     doc["docRev"]))
        codes = sorted(r.status_code for r in rs)
        assert codes == [200] + [409] * 9, f"statuses {codes}, expected one 200 and nine 409"
        assert get_doc(c, sid).get("docRev") == doc["docRev"] + 1, "docRev not increased exactly once"
    s.check("concurrent_uploads_one_wins", one_winner)

    def counted():
        sid = walk_sub(c, s.need("form"), answers=room_answers(20, "d"))
        before = get_doc(c, sid).get("docRev")
        rs = burst(base, 20, lambda k, own: own.patch(f"/submissions/{sid}/items/d{k:02d}", json={"notes": f"n{k}"}))
        assert all(r.status_code == 200 for r in rs), f"statuses {sorted({r.status_code for r in rs})}"
        after = get_doc(c, sid).get("docRev")
        assert after == before + 20, f"docRev went from {before} to {after} after 20 concurrent changes"
    s.check("concurrent_changes_all_counted", counted)

    if late(seed):
        def seeded():
            for x in late(seed)["submissions"][1:6]:
                doc = get_doc(c, x["id"])
                ids = {i["id"] for _, i, _ in items_with_paths(fetch_sub(c, x["id"]).get("answers"))}
                assert set(doc.get("items") or {}) == ids, f"{x['id']}: entries for {len(doc.get('items') or {})}/{len(ids)} items"
                for iid in ids:
                    want = sorted(get_id(f) for f in get_item(c, x["id"], iid).get("findings") or [])
                    got = sorted(get_id(f) for f in doc["items"][iid].get("findings") or [])
                    assert got == want, f"{x['id']}/{iid}: findings {got}, expected {want}"
        s.check("seeded_documents_complete", seeded)

    return s.results

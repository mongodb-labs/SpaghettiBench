"""Checks introduced by the `findings` phase (findings and comments on items)."""
from __future__ import annotations

import datetime as dt

import httpx

from harness.suite import Suite, get_id, ok, rejected, status, strip_nulls
from scenarios.inspections.checks.helpers import (WALK_ANSWERS, add_finding, burst, fetch_sub, finding_of,
                                                  fresh_company, get_item, items_with_paths, late, patch_finding,
                                                  room_answers, walk_form, walk_sub)
from scenarios.inspections.common import parse_instant, strip_item_ids

FINDING = {"title": "Sounder dead", "severity": "high", "assignee": "worker-12", "dueDate": "2027-03-01"}


def v8(c: httpx.Client, seed: dict | None) -> dict:
    s = Suite("v8")
    st = s.state
    base = str(c.base_url)

    def fixtures():
        company = fresh_company(c, "quay-maintenance")
        st["form"] = walk_form(c, company)
        st["sub"] = walk_sub(c, st["form"])
        st["f1"] = get_id(add_finding(c, st["sub"], "i1", FINDING))
    s.check("create_finding", fixtures)

    def read_back():
        got = finding_of(c, s.need("sub"), "i1", s.need("f1"))
        assert strip_nulls({k: got.get(k) for k in FINDING}) == FINDING, f"finding {got}"
        assert got.get("status") == "open" and got.get("comments") == [], \
            f"status {got.get('status')!r}, comments {got.get('comments')!r}"
        assert parse_instant(got.get("createdAt")), f"createdAt {got.get('createdAt')!r}"
    s.check("finding_read_back", read_back)

    def any_depth():
        for iid in ("r1", "f2", "c1"):
            fid = get_id(add_finding(c, s.need("sub"), iid, {"title": f"Check {iid}", "severity": "low"}))
            finding_of(c, s.need("sub"), iid, fid)
    s.check("findings_at_any_depth", any_depth)

    invalid = {
        "missing_title": {"severity": "low"},
        "empty_title": {"title": "", "severity": "low"},
        "unknown_severity": {"title": "x", "severity": "urgent"},
        "invalid_due_date": {"title": "x", "severity": "low", "dueDate": "2027-02-30"},
        "assignee_not_a_string": {"title": "x", "severity": "low", "assignee": 12},
    }
    for name, body in invalid.items():
        s.check(f"rejects_finding_{name}", lambda body=body, name=name: rejected(
            c.post(f"/submissions/{s.need('sub')}/items/i2/findings", json=body), name))
    s.check("finding_on_unknown_item_404", lambda: status(c.post(
        f"/submissions/{s.need('sub')}/items/nope/findings", json={"title": "x", "severity": "low"}), 404, "unknown item"))

    def rejections_store_nothing():
        assert get_item(c, s.need("sub"), "i2").get("findings") == [], "a rejected finding was stored"
    s.check("rejected_findings_not_stored", rejections_store_nothing)

    def patch():
        sid, fid = s.need("sub"), s.need("f1")
        ok(patch_finding(c, sid, "i1", fid, {"status": "in_progress", "assignee": "worker-31"}), "PATCH finding")
        got = finding_of(c, sid, "i1", fid)
        assert (got.get("status"), got.get("assignee"), got.get("title")) == ("in_progress", "worker-31", "Sounder dead"), \
            f"status/assignee/title {got.get('status')!r}/{got.get('assignee')!r}/{got.get('title')!r}"
        ok(patch_finding(c, sid, "i1", fid, {"assignee": None, "dueDate": None}), "PATCH null")
        got = finding_of(c, sid, "i1", fid)
        assert got.get("assignee") is None and got.get("dueDate") is None, \
            f"assignee {got.get('assignee')!r}, dueDate {got.get('dueDate')!r} not cleared"
    s.check("patch_finding", patch)

    def patch_invalid():
        sid, fid = s.need("sub"), s.need("f1")
        rejected(patch_finding(c, sid, "i1", fid, {"status": "done"}), "unknown status")
        rejected(patch_finding(c, sid, "i1", fid, {"severity": "urgent"}), "unknown severity")
        assert finding_of(c, sid, "i1", fid).get("status") == "in_progress", "rejected PATCH changed the finding"
    s.check("patch_finding_rejects_invalid", patch_invalid)

    def comments():
        sid, fid = s.need("sub"), s.need("f1")
        url = f"/submissions/{sid}/items/i1/findings/{fid}/comments"
        a = ok(c.post(url, json={"author": "worker-12", "text": "Replaced the sounder"}), "comment")
        b = ok(c.post(url, json={"author": "worker-31", "text": "Retested OK"}), "comment")
        assert get_id(a) and get_id(b) and parse_instant(a.get("createdAt")), f"comment {a}"
        got = [(x.get("author"), x.get("text")) for x in finding_of(c, sid, "i1", fid).get("comments") or []]
        assert got == [("worker-12", "Replaced the sounder"), ("worker-31", "Retested OK")], f"comments {got}"
    s.check("comments_oldest_first", comments)

    s.check("rejects_comment_without_text", lambda: rejected(c.post(
        f"/submissions/{s.need('sub')}/items/i1/findings/{s.need('f1')}/comments", json={"author": "w"}), "no text"))

    def answers_unchanged():
        s.need("f1")
        got = strip_nulls(strip_item_ids(fetch_sub(c, s.need("sub")).get("answers")))
        assert got == strip_item_ids(WALK_ANSWERS), "findings changed the submission's answers"
    s.check("answers_unchanged_by_findings", answers_unchanged)

    def delete_removes():
        sid = s.need("sub")
        fid = get_id(add_finding(c, sid, "i3", {"title": "Loose cover", "severity": "low"}))
        r = c.delete(f"/submissions/{sid}/items/i3")
        assert r.status_code in (200, 204), f"DELETE: HTTP {r.status_code}"
        status(patch_finding(c, sid, "i3", fid, {"status": "resolved"}), 404, "finding of a deleted item")
    s.check("deleting_item_removes_findings", delete_removes)

    def concurrent_comments():
        sid, fid = s.need("sub"), s.need("f1")
        url = f"/submissions/{sid}/items/i1/findings/{fid}/comments"
        before = len(finding_of(c, sid, "i1", fid).get("comments") or [])
        rs = burst(base, 30, lambda k, own: own.post(url, json={"author": "tablet", "text": f"c{k}"}))
        assert all(r.status_code in (200, 201) for r in rs), f"statuses {sorted({r.status_code for r in rs})}"
        texts = [x.get("text") for x in finding_of(c, sid, "i1", fid).get("comments") or []][before:]
        assert sorted(texts) == sorted(f"c{k}" for k in range(30)), f"{30 - len(set(texts))} of 30 concurrent comments lost"
    s.check("concurrent_comments_never_lost", concurrent_comments)

    def concurrent_mixed():
        sid = walk_sub(c, s.need("form"), room_answers(15))
        fid = get_id(add_finding(c, sid, "k00", {"title": "Busy", "severity": "low"}))

        def call(k: int, own: httpx.Client) -> httpx.Response:
            if k < 15:
                return own.post(f"/submissions/{sid}/items/k00/findings/{fid}/comments", json={"author": "a", "text": f"c{k}"})
            return own.patch(f"/submissions/{sid}/items/k{k - 15:02d}", json={"notes": f"n{k}"})
        rs = burst(base, 30, call)
        assert all(r.status_code in (200, 201) for r in rs), f"statuses {sorted({r.status_code for r in rs})}"
        texts = {x.get("text") for x in finding_of(c, sid, "k00", fid).get("comments") or []}
        items = fetch_sub(c, sid)["answers"]["floors"][0]["rooms"][0]["items"]
        lost = 15 - len(texts) + sum(1 for k, i in enumerate(items) if i.get("notes") != f"n{k + 15}")
        assert not lost, f"{lost} of 30 concurrent comments and item changes lost"
    s.check("concurrent_comments_and_item_changes", concurrent_mixed)

    if late(seed):
        def seeded_findings():
            bad, failed = [], 0
            for x in late(seed)["submissions"]:
                at = dt.date.fromisoformat(x["payload"]["submittedAt"][:10])
                for group, item, _ in items_with_paths(fetch_sub(c, x["id"]).get("answers")):
                    if group != "items" or item.get("status") != "fail":
                        continue
                    failed += 1
                    title = f"{item.get('kind')} failed"
                    got = [f for f in get_item(c, x["id"], item["id"]).get("findings") or [] if f.get("title") == title]
                    want = {"severity": "high" if item.get("kind") == "alarm" else "medium", "status": "open",
                            "assignee": None, "dueDate": (at + dt.timedelta(days=14)).isoformat()}
                    if len(got) != 1 or {k: got[0].get(k) for k in want} != want:
                        bad.append(f"{x['id']}/{item['id']}: {[{k: f.get(k) for k in want} for f in got]}")
            assert failed, "no failed items in Tidewater's inspections"
            assert not bad, f"{len(bad)}/{failed} failed items without exactly one matching finding, e.g. {bad[:2]}"
        s.check("seeded_failed_items_have_findings", seeded_findings)

    return s.results

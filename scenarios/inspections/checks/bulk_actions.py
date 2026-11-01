"""Checks introduced by the `bulk_actions` phase (changing many findings at once)."""
from __future__ import annotations

import httpx

from harness.suite import Suite, created, get_id, ok, rejected, status
from scenarios.inspections.checks.helpers import (add_finding, all_findings, burst, fetch_sub, finding_of,
                                                  fresh_company, room_answers, walk_form, walk_sub)

PLAN = [("i1", "high", "ana", "open"), ("i2", "low", "ana", "open"), ("i3", "high", "ben", "open"),
        ("i4", "low", "ana", "resolved"), ("r1", "medium", None, "open"), ("c1", "low", "ben", "in_progress")]


def v11(c: httpx.Client, seed: dict | None) -> dict:
    s = Suite("v11")
    st = s.state
    base = str(c.base_url)

    def fixtures():
        company, other = fresh_company(c, "estuary-homes"), fresh_company(c, "upland-homes")
        form = walk_form(c, company)
        subs = [walk_sub(c, form), walk_sub(c, form)]
        found = {}
        for n, sid in enumerate(subs):
            for iid, severity, assignee, state in PLAN:
                body = {"title": f"{iid} issue", "severity": severity, "dueDate": "2027-05-01",
                        **({"assignee": assignee} if assignee else {})}
                fid = get_id(add_finding(c, sid, iid, body))
                if state != "open":
                    ok(c.patch(f"/submissions/{sid}/items/{iid}/findings/{fid}", json={"status": state}), "set status")
                found[(n, iid)] = (sid, iid, fid)
        foreign = walk_sub(c, walk_form(c, other))
        st["foreign"] = (foreign, "i1", get_id(add_finding(c, foreign, "i1", {"title": "x", "severity": "low",
                                                                             "assignee": "ana", "dueDate": "2027-05-01"})))
        st.update(company=company, other=other, subs=subs, found=found)
    s.check("create_fixtures", fixtures)

    def bulk(body: dict, headers: dict | None = None) -> httpx.Response:
        return c.post("/findings/bulk", json={"companyId": s.need("company"), **body}, headers=headers)

    def value(key: tuple, field: str) -> object:
        sid, iid, fid = s.need("found")[key]
        return finding_of(c, sid, iid, fid).get(field)

    def reassign():
        got = ok(bulk({"filter": {"assignee": "ana", "status": "open"}, "set": {"assignee": "cy"}}), "bulk reassign")
        assert got.get("matched") == 4, f"matched {got.get('matched')!r}, expected 4"
        for n in (0, 1):
            assert [value((n, i), "assignee") for i in ("i1", "i2", "i4", "i3")] == ["cy", "cy", "ana", "ben"], \
                f"submission {n}: assignees {[value((n, i), 'assignee') for i in ('i1', 'i2', 'i4', 'i3')]}"
    s.check("bulk_reassign", reassign)

    def resolve():
        got = ok(bulk({"filter": {"severity": "high"}, "set": {"status": "resolved"}}), "bulk resolve")
        assert got.get("matched") == 4, f"matched {got.get('matched')!r}, expected 4"
        assert all(value((n, i), "status") == "resolved" for n in (0, 1) for i in ("i1", "i3")), "not all resolved"
        assert value((0, "r1"), "status") == "open", "a finding that didn't match changed"
    s.check("bulk_resolve_by_severity", resolve)

    def clear_due():
        sid = s.need("subs")[1]
        got = ok(bulk({"filter": {"submissionId": sid}, "set": {"dueDate": None}}), "bulk clear due dates")
        assert got.get("matched") == len(PLAN), f"matched {got.get('matched')!r}, expected {len(PLAN)}"
        assert all(value((1, i), "dueDate") is None for i, *_ in PLAN), "due dates not cleared"
        assert value((0, "i1"), "dueDate") == "2027-05-01", "another submission's finding changed"
    s.check("bulk_clear_due_date", clear_due)

    def everything():
        got = ok(bulk({"filter": {}, "set": {"assignee": "dz"}}), "bulk with an empty filter")
        assert got.get("matched") == 2 * len(PLAN), f"matched {got.get('matched')!r}, expected {2 * len(PLAN)}"
        sid, iid, fid = s.need("foreign")
        assert finding_of(c, sid, iid, fid).get("assignee") == "ana", "another company's finding changed"
        assert {f.get("assignee") for f in all_findings(c, {"companyId": s.need("company")})} == {"dz"}, "not all changed"
    s.check("empty_filter_matches_company", everything)

    invalid = {"missing_company": lambda: c.post("/findings/bulk", json={"filter": {}, "set": {"assignee": "x"}}),
               "empty_set": lambda: bulk({"filter": {}, "set": {}}),
               "invalid_status": lambda: bulk({"filter": {}, "set": {"status": "done"}}),
               "unknown_field": lambda: bulk({"filter": {}, "set": {"title": "x"}})}
    for name, call in invalid.items():
        s.check(f"bulk_rejects_{name}", lambda call=call, name=name: rejected(call(), name))

    def roles():
        company = s.need("company")
        manager = get_id(created(c.post(f"/companies/{company}/users", json={
            "name": "Mo", "email": f"mo@{company}.example", "role": "manager"}), "manager"))
        inspector = get_id(created(c.post(f"/companies/{company}/users", json={
            "name": "In", "email": f"in@{company}.example", "role": "inspector"}), "inspector"))
        status(bulk({"filter": {}, "set": {"assignee": "x"}}, {"X-User-Id": inspector}), 403, "inspector bulk action")
        ok(bulk({"filter": {"assignee": "nobody"}, "set": {"assignee": "x"}}, {"X-User-Id": manager}), "manager bulk action")
    s.check("bulk_for_managers_only", roles)

    def concurrent():
        sid = walk_sub(c, walk_form(c, s.need("company")), room_answers(20, "z"))
        fids = {k: get_id(add_finding(c, sid, f"z{k:02d}", {"title": "Busy", "severity": "low", "assignee": "old",
                                                           "dueDate": "2027-05-01"})) for k in range(20)}

        def call(k: int, own: httpx.Client) -> httpx.Response:
            if k == 0:
                return own.post("/findings/bulk", json={"companyId": s.need("company"),
                                                        "filter": {"submissionId": sid}, "set": {"assignee": "zed"}})
            if k <= 20:
                return own.post(f"/submissions/{sid}/items/z{k - 1:02d}/findings/{fids[k - 1]}/comments",
                                json={"author": "tech", "text": f"c{k - 1}"})
            return own.patch(f"/submissions/{sid}/items/z{k - 21:02d}", json={"notes": f"n{k - 21}"})
        rs = burst(base, 31, call)
        assert all(r.status_code in (200, 201) for r in rs), f"statuses {sorted({r.status_code for r in rs})}"
        items = fetch_sub(c, sid)["answers"]["floors"][0]["rooms"][0]["items"]
        lost_notes = sum(1 for k in range(10) if items[k].get("notes") != f"n{k}")
        lost, stale = 0, 0
        for k in range(20):
            f = finding_of(c, sid, f"z{k:02d}", fids[k])
            lost += [x.get("text") for x in f.get("comments") or []] != [f"c{k}"]
            stale += f.get("assignee") != "zed"
        assert not (lost or stale or lost_notes), \
            f"{lost} comments and {lost_notes} item changes lost, {stale} findings not reassigned"
    s.check("concurrent_bulk_comments_and_edits", concurrent)

    return s.results

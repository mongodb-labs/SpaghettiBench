"""Checks introduced by the `findings_search` phase (company-wide finding list)."""
from __future__ import annotations

import httpx

from harness.suite import Suite, created, get_id, ok, rejected
from scenarios.inspections.checks.helpers import (PATHS, add_finding, all_findings, fetch_sub, fresh_company,
                                                  items_with_paths, late, patch_finding, path_of, walk_form, walk_sub)

VEHICLE_FORM = {"name": "Vehicle Check", "fields": [
    {"key": "plate", "label": "Plate", "type": "text", "required": True},
    {"key": "tyres", "label": "Tyres", "type": "group", "fields": [
        {"key": "position", "label": "Position", "type": "select", "options": ["FL", "FR", "RL", "RR"]},
        {"key": "tread_mm", "label": "Tread (mm)", "type": "number", "min": 0},
    ]},
]}


def v9(c: httpx.Client, seed: dict | None) -> dict:
    s = Suite("v9")
    st = s.state

    def fixtures():
        company, other = fresh_company(c, "dockside-services"), fresh_company(c, "inland-services")
        walk = walk_form(c, company)
        vehicle = get_id(created(c.post("/forms", json={**VEHICLE_FORM, "companyId": company}), "POST /forms"))
        s1, s2 = walk_sub(c, walk), walk_sub(c, walk, at="2027-02-02T09:00:00Z")
        s3 = get_id(created(c.post(f"/forms/{vehicle}/submissions", json={
            "submittedBy": "t.sato", "submittedAt": "2027-02-03T09:00:00Z",
            "answers": {"plate": "WX-1234", "tyres": [{"id": "t1", "position": "FL", "tread_mm": 1.2}]}}), "POST submission"))
        f = {
            "f1": (s1, "i1", {"title": "Sounder dead", "severity": "high", "assignee": "ana", "dueDate": "2027-03-01"}),
            "f2": (s1, "r2", {"title": "Door wedged", "severity": "low", "assignee": "ben", "dueDate": "2027-02-10"}),
            "f3": (s2, "i2", {"title": "Pressure low", "severity": "critical", "assignee": "ana", "dueDate": "2027-04-01"}),
            "f4": (s3, "t1", {"title": "Tread worn", "severity": "medium", "dueDate": "2027-02-10"}),
            "f5": (s2, "c1", {"title": "Phone number missing", "severity": "high", "dueDate": "2027-01-01"}),
        }
        ids = {k: get_id(add_finding(c, sid, iid, body)) for k, (sid, iid, body) in f.items()}
        ok(patch_finding(c, s2, "i2", ids["f3"], {"status": "resolved"}), "resolve f3")
        ok(patch_finding(c, s2, "c1", ids["f5"], {"dueDate": None}), "clear f5's due date")
        foreign_form = walk_form(c, other)
        foreign_sub = walk_sub(c, foreign_form)
        st["foreign"] = get_id(add_finding(c, foreign_sub, "i1", {"title": "Other company", "severity": "low",
                                                                  "dueDate": "2027-02-01"}))
        st.update(company=company, other=other, walk=walk, vehicle=vehicle, s1=s1, s2=s2, s3=s3, ids=ids)
    s.check("create_fixtures", fixtures)

    def search(params: dict) -> list[dict]:
        return all_findings(c, {"companyId": s.need("company"), **params})

    def names(items: list[dict]) -> list[str]:
        back = {v: k for k, v in s.need("ids").items()}
        return [back.get(get_id(i), get_id(i) or "?") for i in items]

    def everything():
        assert sorted(names(search({}))) == ["f1", "f2", "f3", "f4", "f5"], f"findings {sorted(names(search({})))}"
    s.check("lists_company_findings", everything)

    def location():
        got = {get_id(i): i for i in search({})}
        ids = s.need("ids")
        for key, sid, iid, group, form in (("f1", s.need("s1"), "i1", "items", s.need("walk")),
                                           ("f2", s.need("s1"), "r2", "rooms", s.need("walk")),
                                           ("f4", s.need("s3"), "t1", "tyres", s.need("vehicle"))):
            e = got.get(ids[key]) or {}
            want = {"submissionId": sid, "formId": form, "itemId": iid, "group": group,
                    "path": path_of(PATHS[iid]) if iid in PATHS else []}
            have = {k: e.get(k) for k in want}
            assert have == want, f"finding {ids[key]}: {have} != {want}"
            assert (e.get("title"), e.get("severity")) == (
                {"f1": ("Sounder dead", "high"), "f2": ("Door wedged", "low"), "f4": ("Tread worn", "medium")}[key]), \
                f"finding {ids[key]}: title/severity {e.get('title')!r}/{e.get('severity')!r}"
    s.check("entries_show_where_the_finding_is", location)

    s.check("ordered_by_due_date", lambda: _expect_order(names(search({})), ["f2", "f4", "f1", "f3", "f5"]))

    filters = {
        "filter_status": ({"status": "open"}, ["f1", "f2", "f4", "f5"]),
        "filter_status_list": ({"status": "resolved,open"}, ["f1", "f2", "f3", "f4", "f5"]),
        "filter_severity_list": ({"severity": "high,critical"}, ["f1", "f3", "f5"]),
        "filter_assignee": ({"assignee": "ana"}, ["f1", "f3"]),
        "filter_form": (lambda: {"formId": s.need("vehicle")}, ["f4"]),
        "filter_submission": (lambda: {"submissionId": s.need("s1")}, ["f1", "f2"]),
        "filter_due_before": ({"dueBefore": "2027-03-01"}, ["f2", "f4"]),
        "filter_combined": ({"status": "open", "assignee": "ana"}, ["f1"]),
    }
    for name, (params, want) in filters.items():
        def check(params=params, want=want):
            p = params() if callable(params) else params
            got = sorted(names(search(p)))
            assert got == sorted(want), f"{p}: {got}, expected {sorted(want)}"
        s.check(name, check)

    def paging():
        s.need("ids")
        pages, cursor = [], None
        for _ in range(10):
            r = c.get("/findings", params={"companyId": s.need("company"), "limit": 2, **({"cursor": cursor} if cursor else {})})
            body = ok(r, "GET /findings page")
            pages.append(names(body.get("items") or []))
            cursor = body.get("nextCursor")
            if not cursor:
                break
        assert [len(p) for p in pages] == [2, 2, 1], f"page sizes {[len(p) for p in pages]}, expected [2, 2, 1]"
        assert sum(pages, []) == ["f2", "f4", "f1", "f3", "f5"], f"pages {pages}"
    s.check("cursor_paging", paging)

    s.check("requires_company", lambda: rejected(c.get("/findings"), "no companyId"))

    def isolation():
        assert s.need("foreign") not in {get_id(i) for i in search({})}, "another company's finding listed"
        assert [get_id(i) for i in all_findings(c, {"companyId": s.need("other")})] == [s.need("foreign")], \
            "the other company's list is wrong"
    s.check("only_own_company", isolation)

    def follows_changes():
        ok(patch_finding(c, s.need("s1"), "i1", s.need("ids")["f1"], {"status": "resolved"}), "resolve f1")
        assert "f1" not in names(search({"status": "open"})), "resolved finding still listed as open"
    s.check("list_follows_changes", follows_changes)

    if late(seed):
        def seeded():
            failed = set()
            for x in late(seed)["submissions"]:
                for group, item, _ in items_with_paths(fetch_sub(c, x["id"]).get("answers")):
                    if group == "items" and item.get("status") == "fail":
                        failed.add((x["id"], item["id"]))
            items = all_findings(c, {"companyId": "tidewater-properties"})
            listed = {(i.get("submissionId"), i.get("itemId")) for i in items if str(i.get("title", "")).endswith(" failed")}
            assert failed and failed <= listed, f"{len(failed - listed)}/{len(failed)} findings of failed items not listed"
            dues = [i.get("dueDate") for i in items]
            dated = [d for d in dues if d]
            assert dated == sorted(dated) and dues[:len(dated)] == dated, "not ordered by due date, undated last"
        s.check("seeded_findings_listed", seeded)

    return s.results


def _expect_order(got: list[str], want: list[str]) -> None:
    assert got == want, f"order {got}, expected {want}"

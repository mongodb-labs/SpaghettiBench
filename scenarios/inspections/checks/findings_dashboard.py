"""Checks introduced by the `findings_dashboard` phase (finding reports, resolvedAt)."""
from __future__ import annotations

import datetime as dt

import httpx

from harness.suite import Suite, get_id, ok, rejected
from scenarios.inspections.checks.helpers import (add_finding, all_findings, finding_of, fresh_company, patch_finding,
                                                  walk_form, walk_sub)
from scenarios.inspections.common import parse_instant

PAST, FUTURE = "2020-01-01", "2099-01-01"


def today() -> str:
    return dt.datetime.now(dt.timezone.utc).date().isoformat()


def expected(findings: list[dict], keys: list[str]) -> dict[tuple, dict]:
    """Report groups computed from the app's own findings (each with an extra 'typeCode' and 'formId')."""
    out: dict[tuple, dict] = {}
    for f in findings:
        values = {"severity": f.get("severity"), "status": f.get("status"), "assignee": f.get("assignee"),
                  "formId": f.get("formId"), "typeCode": f.get("typeCode"),
                  "month": (f.get("createdAt") or "")[:7] or None}
        g = out.setdefault(tuple(values[k] for k in keys), {"count": 0, "open": 0, "overdue": 0, "_d": []})
        g["count"] += 1
        resolved = f.get("status") == "resolved"
        g["open"] += not resolved
        g["overdue"] += (not resolved) and bool(f.get("dueDate")) and f["dueDate"] < today()
        if resolved and f.get("resolvedAt"):
            g["_d"].append((parse_instant(f["resolvedAt"]) - parse_instant(f["createdAt"])).total_seconds() / 86400)  # type: ignore[operator]
    return {k: {"count": g["count"], "open": g["open"], "overdue": g["overdue"],
                "avgDaysToResolve": round(sum(g["_d"]) / len(g["_d"]), 2) if g["_d"] else None} for k, g in out.items()}


def problem(got: dict, want: dict[tuple, dict], keys: list[str]) -> str | None:
    groups = {tuple(g.get(k) for k in keys): g for g in got.get("groups") or []}
    if set(groups) != set(want):
        return f"groups {sorted(map(str, groups))}, expected {sorted(map(str, want))}"
    for key, w in want.items():
        g = groups[key]
        for m in ("count", "open", "overdue"):
            if g.get(m) != w[m]:
                return f"{dict(zip(keys, key))}: {m} {g.get(m)!r}, expected {w[m]}"
        a, b = g.get("avgDaysToResolve"), w["avgDaysToResolve"]
        if (a is None) != (b is None) or (a is not None and abs(a - b) > 0.011):
            return f"{dict(zip(keys, key))}: avgDaysToResolve {a!r}, expected {b!r}"
    return None


def v16(c: httpx.Client, seed: dict | None) -> dict:
    s = Suite("v16")
    st = s.state

    def fixtures():
        company = fresh_company(c, "saltmarsh-living")
        ok(c.post(f"/companies/{company}/item-types", json={"code": "T1", "name": "Panel", "intervalDays": 30}), "type")
        sid = walk_sub(c, walk_form(c, company))
        ok(c.put(f"/submissions/{sid}/items/i1/type", json={"code": "T1"}), "set type")
        plan = {"a": ("i1", "high", "ana", PAST), "b": ("i2", "high", "ana", FUTURE),
                "c": ("i3", "low", "ben", PAST), "d": ("i4", "medium", None, FUTURE)}
        ids = {}
        for k, (iid, severity, assignee, due) in plan.items():
            ids[k] = (iid, get_id(add_finding(c, sid, iid, {"title": k, "severity": severity, "dueDate": due,
                                                            **({"assignee": assignee} if assignee else {})})))
        ok(patch_finding(c, sid, "i3", ids["c"][1], {"status": "resolved"}), "resolve c")
        st.update(company=company, sid=sid, ids=ids)
    s.check("create_fixtures", fixtures)

    def resolved_at():
        sid, (iid, fid) = s.need("sid"), s.need("ids")["c"]
        assert parse_instant(finding_of(c, sid, iid, fid).get("resolvedAt")), "resolvedAt not set on resolving"
        ok(patch_finding(c, sid, iid, fid, {"status": "open"}), "reopen")
        assert finding_of(c, sid, iid, fid).get("resolvedAt") is None, "resolvedAt not cleared on reopening"
        ok(patch_finding(c, sid, iid, fid, {"status": "resolved"}), "resolve again")
        assert parse_instant(finding_of(c, sid, iid, fid).get("resolvedAt")), "resolvedAt not set again"
    s.check("resolved_at_follows_status", resolved_at)

    def own_findings() -> list[dict]:
        sid = s.need("sid")
        out = []
        for f in all_findings(c, {"companyId": s.need("company")}):
            full = finding_of(c, sid, f.get("itemId"), get_id(f))
            out.append({**full, "formId": f.get("formId"), "typeCode": (f.get("itemType") or {}).get("code")})
        return out

    def report(keys: list[str], **params: str) -> None:
        got = ok(c.get("/reports/findings", params={"companyId": s.need("company"), "groupBy": ",".join(keys), **params}),
                 "findings report")
        rows = own_findings()
        if params.get("status"):
            rows = [f for f in rows if f.get("status") in params["status"].split(",")]
        p = problem(got, expected(rows, keys), keys)
        assert not p, f"groupBy={','.join(keys)}: {p}"

    def by(keys: list[str], **params: str):
        def fn():
            s.need("ids")
            report(keys, **params)
        return fn

    for keys in (["severity"], ["assignee"], ["status"], ["typeCode"], ["month"], ["formId"], ["severity", "assignee"]):
        s.check(f"report_by_{'_and_'.join(keys)}", by(keys))

    def known_numbers():
        s.need("ids")
        got = ok(c.get("/reports/findings", params={"companyId": s.need("company"), "groupBy": "severity"}), "report")
        by = {g.get("severity"): (g.get("count"), g.get("open"), g.get("overdue")) for g in got.get("groups") or []}
        assert by == {"high": (2, 2, 1), "low": (1, 0, 0), "medium": (1, 1, 0)}, f"count/open/overdue by severity {by}"
    s.check("report_counts_open_and_overdue", known_numbers)

    s.check("report_with_filter", by(["severity"], status="open"))

    invalid = {"missing_group_by": lambda: {"companyId": s.need("company")},
               "unknown_key": lambda: {"companyId": s.need("company"), "groupBy": "colour"},
               "missing_company": lambda: {"groupBy": "severity"}}
    for name, params in invalid.items():
        s.check(f"report_rejects_{name}", lambda params=params, name=name: rejected(
            c.get("/reports/findings", params=params()), name))

    if seed and seed.get("late"):
        def seeded():
            got = ok(c.get("/reports/findings", params={"companyId": "tidewater-properties", "groupBy": "severity"}),
                     "Tidewater's report")
            rows = all_findings(c, {"companyId": "tidewater-properties"})
            want = expected([{**f, "resolvedAt": None} for f in rows], ["severity"])
            by = {tuple([g.get("severity")]): g for g in got.get("groups") or []}
            bad = [k for k, w in want.items() if any((by.get(k) or {}).get(m) != w[m] for m in ("count", "open", "overdue"))]
            assert not bad and set(by) == set(want), f"severities that differ: {bad or sorted(map(str, set(by) ^ set(want)))}"
        s.check("seeded_report_matches_findings", seeded)

    return s.results

"""Checks introduced by the `due_dates` phase (improvement: due dates from severity, overdue)."""
from __future__ import annotations

import datetime as dt

import httpx

from harness.suite import Suite, get_id, ok
from scenarios.inspections.checks.helpers import (add_finding, all_findings, finding_of, fresh_company, get_item,
                                                  patch_finding, walk_form, walk_sub)
from scenarios.inspections.common import parse_instant

DAYS = {"critical": 1, "high": 7, "medium": 14, "low": 30}


def due_for(created_at: str, severity: str) -> str:
    return (parse_instant(created_at).date() + dt.timedelta(days=DAYS[severity])).isoformat()  # type: ignore[union-attr]


def i4(c: httpx.Client, seed: dict | None) -> dict:
    s = Suite("i4")
    st = s.state

    def fixtures():
        company = fresh_company(c, "creekside-homes")
        sid = walk_sub(c, walk_form(c, company))
        st.update(company=company, sid=sid)
        st["auto"] = {sev: (iid, add_finding(c, sid, iid, {"title": f"Auto {sev}", "severity": sev}))
                      for sev, iid in zip(DAYS, ("i1", "i2", "i3", "i4"))}
    s.check("create_findings_without_due_date", fixtures)

    def auto():
        bad = [f"{sev}: {f.get('dueDate')!r} != {due_for(f.get('createdAt'), sev)}" for sev, (_, f) in s.need("auto").items()
               if f.get("dueDate") != due_for(f.get("createdAt"), sev)]
        assert not bad, f"due dates {bad}"
    s.check("due_date_from_severity", auto)

    def explicit_and_clear():
        sid = s.need("sid")
        fid = get_id(add_finding(c, sid, "r1", {"title": "Explicit", "severity": "critical", "dueDate": "2027-12-24"}))
        assert finding_of(c, sid, "r1", fid).get("dueDate") == "2027-12-24", "explicit due date replaced"
        ok(patch_finding(c, sid, "r1", fid, {"dueDate": None}), "clear due date")
        assert finding_of(c, sid, "r1", fid).get("dueDate") is None, "clearing the due date doesn't work any more"
    s.check("explicit_due_date_and_clearing_kept", explicit_and_clear)

    def overdue_flag():
        sid = s.need("sid")
        late_one = get_id(add_finding(c, sid, "c1", {"title": "Late", "severity": "low", "dueDate": "2020-01-01"}))
        done = get_id(add_finding(c, sid, "c1", {"title": "Done", "severity": "low", "dueDate": "2020-01-01"}))
        ok(patch_finding(c, sid, "c1", done, {"status": "resolved"}), "resolve")
        flags = {f.get("title"): f.get("overdue") for f in get_item(c, sid, "c1").get("findings") or []}
        assert (flags.get("Late"), flags.get("Done")) == (True, False), f"overdue flags {flags}"
        assert finding_of(c, sid, "i1", get_id(s.need("auto")["critical"][1])).get("overdue") is False, \
            "a finding due tomorrow is overdue"
        st["late"] = late_one
    s.check("overdue_flag", overdue_flag)

    def overdue_filter():
        late_one = s.need("late")
        got = [get_id(f) for f in all_findings(c, {"companyId": s.need("company"), "overdue": "true"})]
        assert got == [late_one], f"overdue=true returned {got}"
        rest = {get_id(f) for f in all_findings(c, {"companyId": s.need("company"), "overdue": "false"})}
        assert late_one not in rest and len(rest) == 6, f"overdue=false returned {len(rest)} findings, expected 6"
        got = ok(c.post("/findings/bulk", json={"companyId": s.need("company"), "filter": {"overdue": "true"},
                                                "set": {"assignee": "escalated"}}), "bulk with overdue filter")
        assert got.get("matched") == 1, f"bulk matched {got.get('matched')!r}"
    s.check("overdue_filter", overdue_filter)

    if seed and seed.get("late_findings"):
        def seeded():
            bad = []
            for x in seed["late_findings"]["findings"]:
                f = finding_of(c, x["submission"], x["item"], x["id"])
                if f.get("dueDate") != due_for(f.get("createdAt"), x["severity"]):
                    bad.append(f"{x['id']}: {f.get('dueDate')!r}")
            assert not bad, f"existing findings without a due date not given one: {bad}"
        s.check("seeded_findings_get_due_dates", seeded)

    return s.results

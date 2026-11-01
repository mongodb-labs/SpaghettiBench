"""Checks introduced by the `report_timezone` phase (improvement: reports in local time)."""
from __future__ import annotations

from typing import Callable

import httpx

from harness.suite import Suite, created, get_id, rejected
from scenarios.inspections.checks.compliance_reports import assert_report, expected_report, got_report
from scenarios.inspections.checks.helpers import company_subs, fresh_company, sub


TZ_FORM = {"name": "Night Shift Check", "fields": [
    {"key": "site_name", "label": "Site", "type": "text", "required": True},
    {"key": "status", "label": "Result", "type": "select", "options": ["pass", "fail"]},
]}
# Around month ends and daylight-saving changes, where the local month differs from the UTC month.
TZ_TIMES = ["2026-10-31T23:30:00Z", "2026-11-01T03:30:00Z", "2026-03-29T00:30:00Z", "2026-03-31T22:30:00Z",
            "2026-12-31T23:30:00Z", "2026-06-15T12:00:00Z"]


def i2(c: httpx.Client, seed: dict | None) -> dict:
    s = Suite("i2")

    def create_fixtures():
        company = fresh_company(c, "night-shift-security")
        fid = get_id(created(c.post("/forms", json={**TZ_FORM, "companyId": company}), "POST /forms"))
        subs = []
        for n, at in enumerate(TZ_TIMES):
            p = sub("k.mensah", at, {"site_name": f"Gate {n % 2 + 1}", "status": "fail" if n % 3 == 0 else "pass"})
            created(c.post(f"/forms/{fid}/submissions", json=p), "POST submission")
            subs.append({"formId": fid, **p})
        s.state.update(company=company, form=fid, subs=subs)
    s.check("create_fixtures", create_fixtures)

    def report(url: str, group_by: list[str], rate: str | None, extra: dict) -> Callable[[], None]:
        def fn():
            tz = extra.get("timezone")
            params = {"groupBy": ",".join(group_by), **({"rate": rate} if rate else {}), **extra}
            url_ = url.format(form=s.need("form"))
            if "companyId" in params:
                params["companyId"] = s.need("company")
            got = got_report(c.get(url_, params=params), group_by, rate)
            assert_report(got, expected_report(s.need("subs"), group_by, rate, tz))
        return fn
    s.check("report_month_in_timezone", report("/forms/{form}/report", ["month"], None, {"timezone": "Europe/Berlin"}))
    s.check("report_month_and_rate_in_timezone", report("/forms/{form}/report", ["month"], "answers.status:fail",
                                                        {"timezone": "America/New_York"}))
    s.check("report_default_utc", report("/forms/{form}/report", ["answers.site_name", "month"], None, {}))
    s.check("company_report_in_timezone", report("/reports", ["formId", "month"], None,
                                                 {"companyId": "", "timezone": "Asia/Tokyo"}))

    def rejects_unknown():
        rejected(c.get(f"/forms/{s.need('form')}/report", params={"groupBy": "month", "timezone": "Mars/Olympus_Mons"}),
                 "unknown timezone")
        rejected(c.get("/reports", params={"companyId": s.need("company"), "groupBy": "month",
                                           "timezone": "Mars/Olympus_Mons"}), "unknown timezone")
    s.check("report_rejects_unknown_timezone", rejects_unknown)

    def seeded_company_report():
        group_by, tz = ["formId", "month"], "Pacific/Auckland"
        got = got_report(c.get("/reports", params={"companyId": "acme", "groupBy": ",".join(group_by), "timezone": tz}),
                         group_by, None)
        assert_report(got, expected_report(company_subs(c, "acme"), group_by, None, tz))
    if seed:
        s.check("seeded_company_report_in_timezone", seeded_company_report)

    return s.results

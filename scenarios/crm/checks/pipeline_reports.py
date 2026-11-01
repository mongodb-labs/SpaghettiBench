"""Checks introduced by the `pipeline_reports` phase (4: pipeline reports)."""
from __future__ import annotations

from typing import Any, Callable

import httpx

from harness.suite import Suite, get_id, listing, mk, ok, rejected
from scenarios.crm.checks.helpers import fresh_agency, person


REPORT_DEALS = [  # contact, stage, value, closeDate, channel
    ("p1", "won", 10000, "2026-01-15", "direct"), ("p1", "lost", 5000, "2026-01-20", "partner"),
    ("p1", "proposal", 7000, None, "direct"), ("p2", "won", 3000, "2026-02-03", "partner"),
    ("p2", "won", 2000.5, "2026-02-28", None), ("p3", "lead", 1500, None, None),
    ("p3", "lost", 800, "2026-03-01", "direct"), ("p2", "qualified", 0, "2026-04-10", "direct"),
]


def month(date: Any) -> str | None:
    return date[:7] if isinstance(date, str) and len(date) >= 7 else None


def live_rows(c: httpx.Client, agency: str) -> list[dict]:
    """One row per deal with every value a report can group by, from the app's own data."""
    contacts = {get_id(x): x for x in listing(c, "/contacts", {"agencyId": agency})}
    rows = []
    for d in listing(c, "/deals", {"agencyId": agency}):
        row = {"stage": d.get("stage"), "closeMonth": month(d.get("closeDate")), "companyId": d.get("companyId"),
               "value": d.get("value") or 0}
        row.update({f"custom.{k}": v for k, v in (d.get("custom") or {}).items()})
        row.update({f"contact.custom.{k}": v for k, v in ((contacts.get(d.get("contactId")) or {}).get("custom") or {}).items()})
        rows.append(row)
    return rows


def expected_report(rows: list[dict], group_by: list[str]) -> dict:
    out: dict[tuple, dict] = {}
    for r in rows:
        g = out.setdefault(tuple(r.get(k) for k in group_by), {"count": 0, "value": 0.0, "won": 0, "closed": 0})
        g["count"] += 1
        g["value"] += r["value"]
        if r["stage"] in ("won", "lost"):
            g["closed"] += 1
            g["won"] += r["stage"] == "won"
    return {k: {"count": g["count"], "value": g["value"], "winRate": g["won"] / g["closed"] if g["closed"] else None}
            for k, g in out.items()}


def got_report(r: httpx.Response, group_by: list[str]) -> dict:
    assert r.status_code == 200, f"report: HTTP {r.status_code} {r.text[:200]}"
    body = r.json()
    groups = body.get("groups") if isinstance(body, dict) else body
    assert isinstance(groups, list), f"no groups list in {str(body)[:200]}"
    out: dict[tuple, dict] = {}
    for g in groups:
        key = tuple(g.get(k) for k in group_by)
        assert key not in out, f"group {key} listed twice"
        out[key] = {"count": g.get("count"), "value": g.get("value"), "winRate": g.get("winRate")}
    return out


def assert_report(got: dict, want: dict) -> None:
    missing, extra = set(want) - set(got), set(got) - set(want)
    assert not missing and not extra, f"groups missing {sorted(missing, key=str)[:4]}, unexpected {sorted(extra, key=str)[:4]}"
    for k, w in want.items():
        g = got[k]
        assert g["count"] == w["count"], f"{k}: count {g['count']!r} != {w['count']}"
        assert isinstance(g["value"], (int, float)) and abs(g["value"] - w["value"]) < 0.01, \
            f"{k}: value {g['value']!r} != {w['value']}"
        if w["winRate"] is None:
            assert g["winRate"] is None, f"{k}: winRate {g['winRate']!r} != null"
        else:
            assert isinstance(g["winRate"], (int, float)) and abs(g["winRate"] - w["winRate"]) < 1e-6, \
                f"{k}: winRate {g['winRate']!r} != {w['winRate']}"


def v4(c: httpx.Client, seed: dict | None) -> dict:
    s = Suite("v4")
    st = s.state
    ag = fresh_agency(c, "venue-scouts")

    def fixtures():
        ok(c.post(f"/agencies/{ag}/fields", json={"entity": "contact", "key": "segment", "label": "Segment",
                                                  "type": "select", "options": ["smb", "enterprise"]}), "define")
        ok(c.post(f"/agencies/{ag}/fields", json={"entity": "deal", "key": "channel", "label": "Channel",
                                                  "type": "select", "options": ["direct", "partner"]}), "define")
        a = mk(c, "/companies", {"agencyId": ag, "name": "Alpha Venues"}, "company")
        b = mk(c, "/companies", {"agencyId": ag, "name": "Beta Hotels"}, "company")
        people = {"p1": (a, "enterprise"), "p2": (b, "smb"), "p3": (None, None)}
        ids = {}
        for k, (company, segment) in people.items():
            extra = {**({"companyId": company} if company else {}), **({"custom": {"segment": segment}} if segment else {})}
            ids[k] = mk(c, "/contacts", person(ag, k.upper(), "Report", **extra), "contact")
        deals = []
        for contact, stage, value, close, channel in REPORT_DEALS:
            body = {"agencyId": ag, "contactId": ids[contact], "title": f"{stage} deal", "value": value, "stage": stage,
                    **({"closeDate": close} if close else {}), **({"custom": {"channel": channel}} if channel else {})}
            deals.append(mk(c, "/deals", body, "deal"))
        st.update(a=a, b=b, people=people, ids=ids, deals=deals, overrides={})
    s.check("create_fixtures", fixtures)

    def rows() -> list[dict]:
        people, over = s.need("people"), st.get("overrides", {})
        out = []
        for n, (contact, stage, value, close, channel) in enumerate(REPORT_DEALS):
            company, segment = over.get(contact, people[contact])
            out.append({"stage": over.get(n, stage), "closeMonth": month(close), "companyId": company,
                        "custom.channel": channel, "contact.custom.segment": segment, "value": value})
        return out

    def report(params: dict) -> httpx.Response:
        return c.get("/reports/deals", params={"agencyId": ag, **params})

    def rc(name: str, group_by: list[str], filters: Callable[[], dict] | None = None,
           keep: Callable[[dict], bool] | None = None) -> None:
        def fn():
            want = expected_report([r for r in rows() if not keep or keep(r)], group_by)
            assert_report(got_report(report({"groupBy": ",".join(group_by), **(filters() if filters else {})}), group_by), want)
        s.check(name, fn)

    rc("report_by_stage", ["stage"])
    rc("report_by_close_month", ["closeMonth"])
    rc("report_by_company", ["companyId"])
    rc("report_by_deal_custom", ["custom.channel"])
    rc("report_by_contact_custom", ["contact.custom.segment"])
    rc("report_multiple_keys", ["companyId", "stage"])
    rc("report_with_stage_filter", ["closeMonth"], lambda: {"stage": "won"}, lambda r: r["stage"] == "won")
    rc("report_with_custom_filter", ["stage"], lambda: {"custom.channel": "direct"}, lambda r: r["custom.channel"] == "direct")
    rc("report_with_company_filter", ["contact.custom.segment", "stage"], lambda: {"companyId": s.need("a")},
       lambda r: r["companyId"] == s.need("a"))

    for name, params in {"unknown_key": {"groupBy": "owner"}, "undefined_custom_field": {"groupBy": "custom.nope"},
                         "undefined_contact_field": {"groupBy": "contact.custom.nope"}, "missing_group_by": {}}.items():
        s.check(f"report_rejects_{name}", lambda params=params, name=name: rejected(report(params), name))
    s.check("report_requires_agency", lambda: rejected(c.get("/reports/deals", params={"groupBy": "stage"}), "no agency"))

    def current_state():
        deals, ids, people = s.need("deals"), s.need("ids"), s.need("people")
        ok(c.patch(f"/deals/{deals[2]}", json={"stage": "won"}), "PATCH deal")
        ok(c.patch(f"/contacts/{ids['p3']}", json={"companyId": s.need("b")}), "PATCH contact")
        st["overrides"] = {2: "won", "p3": (s.need("b"), people["p3"][1])}
        group_by = ["companyId", "stage"]
        assert_report(got_report(report({"groupBy": ",".join(group_by)}), group_by), expected_report(rows(), group_by))
    s.check("report_reflects_current_data", current_state)

    if seed:
        def seeded(agency: str, group_by: list[str], filters: dict | None = None,
                   keep: Callable[[dict], bool] | None = None) -> Callable[[], None]:
            def fn():
                want = expected_report([r for r in live_rows(c, agency) if not keep or keep(r)], group_by)
                got = got_report(c.get("/reports/deals", params={"agencyId": agency, "groupBy": ",".join(group_by),
                                                                  **(filters or {})}), group_by)
                assert_report(got, want)
            return fn
        s.check("seeded_report_by_stage_and_month", seeded("harbor-realty", ["stage", "closeMonth"]))
        s.check("seeded_report_by_seniority", seeded("talentbridge", ["contact.custom.seniority", "stage"]))
        s.check("seeded_report_won_by_company", seeded("fete-events", ["companyId"], {"stage": "won"},
                                                       lambda r: r["stage"] == "won"))

    return s.results

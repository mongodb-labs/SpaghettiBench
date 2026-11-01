"""Checks introduced by the `compliance_reports` phase (compliance reports)."""
from __future__ import annotations

import datetime as dt
from typing import Any, Callable
from zoneinfo import ZoneInfo

import httpx

from harness.suite import Suite, as_list, created, get_id, rejected
from scenarios.inspections.checks.helpers import company_subs, fresh_company, list_subs, sub
from scenarios.inspections.checks.multi_level import SITE_FORM


def month_of(timestamp: Any, tz: str | None = None) -> str:
    at = dt.datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    return at.astimezone(ZoneInfo(tz) if tz else dt.timezone.utc).strftime("%Y-%m")


def value_matches(stored: Any, value: str) -> bool:
    """Compare a stored answer with a query-string value, using the stored value's type."""
    if isinstance(stored, bool):
        return value in ("true", "false") and stored == (value == "true")
    if isinstance(stored, (int, float)):
        try:
            return stored == float(value)
        except ValueError:
            return False
    return isinstance(stored, str) and stored == value


def expected_report(subs: list[dict], group_by: list[str], rate: str | None, tz: str | None = None) -> dict:
    """Independent aggregation over submissions ({formId, submittedAt, answers}); keys are value tuples."""
    paths = [t.split(".")[1:] for t in group_by if t.startswith("answers.")]
    rate_path, rate_value = None, None
    if rate:
        target, rate_value = rate.split(":", 1)
        rate_path = target.split(".")[1:]
    chain = max((p[:-1] for p in paths + ([rate_path] if rate_path else [])), key=len, default=[])

    def walk(nodes: list, rest: list):
        if not rest:
            yield nodes
            return
        for item in nodes[-1].get(rest[0]) or []:
            if isinstance(item, dict):
                yield from walk(nodes + [item], rest[1:])

    def lookup(nodes: list, path: list) -> Any:
        return nodes[len(path) - 1].get(path[-1])

    def key_value(sub_: dict, nodes: list, token: str) -> Any:
        if token == "month":
            return month_of(sub_["submittedAt"], tz)
        if token == "formId":
            return str(sub_["formId"])
        return lookup(nodes, token.split(".")[1:])

    counts: dict[tuple, list] = {}
    for sub_ in subs:
        for nodes in walk([sub_.get("answers") or {}], chain):
            key = tuple(key_value(sub_, nodes, t) for t in group_by)
            entry = counts.setdefault(key, [0, 0])
            entry[0] += 1
            entry[1] += bool(rate_path and value_matches(lookup(nodes, rate_path), rate_value))
    return {k: (n, hits / n if rate else None) for k, (n, hits) in counts.items()}


def got_report(r: httpx.Response, group_by: list[str], rate: str | None) -> dict:
    assert r.status_code == 200, f"report: HTTP {r.status_code} {r.text[:200]}"
    out = {}
    for g in as_list(r.json()):
        key = tuple(g.get(t) for t in group_by)
        assert key not in out, f"group {key} appears twice"
        out[key] = (g.get("count"), g.get("rate") if rate else None)
    return out


def assert_report(got: dict, want: dict) -> None:
    missing, extra = set(want) - set(got), set(got) - set(want)
    assert not missing and not extra, \
        f"groups differ: missing {sorted(map(str, missing))[:4]}, unexpected {sorted(map(str, extra))[:4]}"
    for key, (count, rate) in want.items():
        got_count, got_rate = got[key]
        assert got_count == count, f"{key}: count {got_count}, expected {count}"
        if rate is not None:
            assert isinstance(got_rate, (int, float)) and abs(got_rate - rate) < 1e-6, \
                f"{key}: rate {got_rate!r}, expected {rate}"


def walkthrough_v1(company: str) -> dict:
    return {"companyId": company, "name": "Monthly Walkthrough", "fields": [
        {"key": "site_name", "label": "Site", "type": "text", "required": True},
        {"key": "status", "label": "Result", "type": "select", "options": ["pass", "fail"]},
        {"key": "score", "label": "Score", "type": "number", "min": 0, "max": 10},
    ]}


def walkthrough_v2(company: str) -> dict:
    v1 = walkthrough_v1(company)
    fields = [f if f["key"] != "status" else {**f, "options": ["pass", "fail", "not_applicable"]} for f in v1["fields"]]
    return {**v1, "fields": fields + [{"key": "inspector", "label": "Inspector", "type": "text"}]}


def floors(*floor_list: tuple) -> list:
    """floors(("Ground", [("Lobby", [("alarm", "pass"), ...]), ...]), ...) -> nested group answers."""
    return [{"floor_name": name, "rooms": [{"room_name": room, "items": [{"kind": k, "status": st} for k, st in items]}
                                            for room, items in rooms]}
            for name, rooms in floor_list]


V6_SITE_SUBS = {
    "r1": sub("t.sato", "2026-09-10T08:00:00Z", {"site_name": "Harbour Tower", "floors": floors(
        ("Ground", [("Lobby", [("alarm", "pass"), ("extinguisher", "fail")]), ("Kitchen", [("alarm", "fail")])]))}),
    "r2": sub("t.sato", "2026-10-05T08:00:00Z", {"site_name": "Harbour Tower", "floors": floors(
        ("Ground", [("Lobby", [("alarm", "pass"), ("extinguisher", "pass"), ("exit_sign", "fail")])]))}),
    "r3": sub("r.haddad", "2026-10-12T08:00:00Z", {"site_name": "Mill Street Depot", "floors": floors(
        ("Basement", [("Plant", [("extinguisher", "fail"), ("extinguisher", "fail")])]),
        ("First", [("Office", [("alarm", "pass")])]))}),
    "r4": sub("r.haddad", "2026-10-28T08:00:00Z", {"site_name": "Mill Street Depot"}),
}
V6_WALK_SUBS_V1 = {
    "w1": sub("k.mensah", "2026-09-20T09:00:00Z", {"site_name": "Harbour Tower", "status": "pass", "score": 8}),
    "w2": sub("k.mensah", "2026-10-02T09:00:00Z", {"site_name": "Mill Street Depot", "status": "fail", "score": 3}),
}
V6_WALK_SUBS_V2 = {
    "w3": sub("k.mensah", "2026-10-30T09:00:00Z",
              {"site_name": "Mill Street Depot", "status": "fail", "inspector": "k.mensah"}),
}

ITEM = "answers.floors.rooms.items"
V6_SITE_REPORTS = {
    "report_counts_submissions": (["answers.site_name"], None, {}),
    "report_counts_items": ([f"{ITEM}.kind"], f"{ITEM}.status:fail", {}),
    "report_compliance_matrix": (["answers.site_name", f"{ITEM}.kind", "month"], f"{ITEM}.status:fail", {}),
    "report_intermediate_level": (["answers.floors.floor_name"], None, {}),
    "report_rate_from_parent_level": ([f"{ITEM}.kind"], "answers.site_name:Harbour Tower", {}),
    "report_with_filter": ([f"{ITEM}.kind"], f"{ITEM}.status:fail", {"submittedAt[gte]": "2026-10-01T00:00:00Z"}),
}
V6_WALK_REPORTS = {
    "report_across_versions": (["answers.status"], None, {}),
    "report_rate_by_site_and_month": (["answers.site_name", "month"], "answers.status:fail", {}),
    "report_null_group": (["answers.score"], None, {}),
}


def v6(c: httpx.Client, seed: dict | None) -> dict:
    s = Suite("v6")
    seed = seed or {}

    def report(url: str, group_by: list[str], rate: str | None, extra: dict | None = None) -> dict:
        params = {"groupBy": ",".join(group_by), **({"rate": rate} if rate else {}), **(extra or {})}
        return got_report(c.get(url, params=params), group_by, rate)

    def create_fixtures():
        company = fresh_company(c, "harbourline-estates")
        site = get_id(created(c.post("/forms", json={**SITE_FORM, "companyId": company}), "POST site form"))
        walk = get_id(created(c.post("/forms", json=walkthrough_v1(company)), "POST walkthrough form"))
        subs = {}
        for fid, group in ((site, V6_SITE_SUBS), (walk, V6_WALK_SUBS_V1)):
            for k, p in group.items():
                subs[k] = {"formId": fid, **p}
                created(c.post(f"/forms/{fid}/submissions", json=p), "POST submission")
        r = c.put(f"/forms/{walk}", json=walkthrough_v2(company))
        assert r.status_code in (200, 201), f"PUT walkthrough form: HTTP {r.status_code}"
        for k, p in V6_WALK_SUBS_V2.items():
            subs[k] = {"formId": walk, **p}
            created(c.post(f"/forms/{walk}/submissions", json=p), "POST submission")
        s.state.update(company=company, site=site, walk=walk, subs=subs)
    s.check("create_fixtures", create_fixtures)

    def fixture_subs(form: str) -> list[dict]:
        fid = s.need(form)
        return [x for x in s.need("subs").values() if x["formId"] == fid]

    for form, reports in (("site", V6_SITE_REPORTS), ("walk", V6_WALK_REPORTS)):
        for name, (group_by, rate, extra) in reports.items():
            def check(form=form, group_by=group_by, rate=rate, extra=extra):
                subs = fixture_subs(form)
                if "submittedAt[gte]" in extra:
                    subs = [x for x in subs if x["submittedAt"] >= "2026-10-01"]
                got = report(f"/forms/{s.need(form)}/report", group_by, rate, extra)
                assert_report(got, expected_report(subs, group_by, rate))
            s.check(name, check)

    def reject(params: dict, what: str) -> Callable[[], None]:
        return lambda: rejected(c.get(f"/forms/{s.need('site')}/report", params=params), what)

    s.check("report_rejects_two_branches", reject(
        {"groupBy": "answers.floors.floor_name,answers.contacts.name"}, "groups on two branches"))
    s.check("report_rejects_unknown_key", reject({"groupBy": "week"}, "unknown groupBy key"))
    s.check("report_rejects_malformed_rate", reject(
        {"groupBy": f"{ITEM}.kind", "rate": f"{ITEM}.status"}, "rate without a value"))

    def company_report_by_form_and_month():
        group_by = ["formId", "month"]
        got = report("/reports", group_by, None, {"companyId": s.need("company")})
        assert_report(got, expected_report(list(s.need("subs").values()), group_by, None))
    s.check("company_report_by_form_and_month", company_report_by_form_and_month)

    def company_report_cross_form_rate():
        group_by, rate = ["answers.site_name"], "answers.status:fail"
        got = report("/reports", group_by, rate, {"companyId": s.need("company")})
        assert_report(got, expected_report(list(s.need("subs").values()), group_by, rate))
    s.check("company_report_cross_form_rate", company_report_cross_form_rate)

    def company_report_group_path():
        group_by, rate = ["formId", f"{ITEM}.kind"], f"{ITEM}.status:fail"
        got = report("/reports", group_by, rate, {"companyId": s.need("company")})
        assert_report(got, expected_report(list(s.need("subs").values()), group_by, rate))
    s.check("company_report_group_path", company_report_group_path)

    s.check("company_report_requires_company", lambda: rejected(
        c.get("/reports", params={"groupBy": "month"}), "missing companyId"))

    # Production data: reports must agree with the app's own records, whatever earlier phases added.
    seeded = {f["key"]: f for f in seed.get("forms", [])}

    def live(form_key: str) -> list[dict]:
        return list_subs(c, seeded[form_key]["id"])

    def seeded_acme_report():
        assert "acme_fire" in seeded, "Acme form was not seeded"
        group_by, rate = ["answers.extinguishers.location", "month"], "answers.extinguishers.pressure_ok:false"
        got = report(f"/forms/{seeded['acme_fire']['id']}/report", group_by, rate)
        assert_report(got, expected_report(live("acme_fire"), group_by, rate))
    s.check("seeded_acme_report_matches_data", seeded_acme_report)

    def seeded_kitchen_report():
        assert "northwind_kitchen" in seeded, "kitchen form was not seeded"
        group_by, rate = ["answers.kitchen_area", "month"], "answers.handwash_ok:false"
        got = report(f"/forms/{seeded['northwind_kitchen']['id']}/report", group_by, rate)
        assert_report(got, expected_report(live("northwind_kitchen"), group_by, rate))
    s.check("seeded_kitchen_report_across_versions", seeded_kitchen_report)

    def seeded_company_report():
        group_by, rate = ["formId", "month"], "answers.all_working:false"
        got = report("/reports", group_by, rate, {"companyId": "acme"})
        assert_report(got, expected_report(company_subs(c, "acme"), group_by, rate))
    s.check("seeded_company_report_matches_data", seeded_company_report)

    return s.results

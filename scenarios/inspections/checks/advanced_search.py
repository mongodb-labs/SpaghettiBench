"""Checks introduced by the `advanced_search` phase (advanced search)."""
from __future__ import annotations

from typing import Callable

import httpx

from harness.suite import Suite, created, get_id, rejected
from scenarios.inspections.checks.helpers import assert_filter, company_subs, fresh_company, list_subs, sub
from scenarios.inspections.checks.repeating_groups import converted


POOL_FORM = {"companyId": "coastal-leisure", "name": "Pool Safety Audit", "fields": [
    {"key": "site", "label": "Site", "type": "text", "required": True},
    {"key": "chlorine", "label": "Free chlorine (mg/l)", "type": "number", "min": 0, "max": 10},
    {"key": "inspected_on", "label": "Inspected on", "type": "date"},
    {"key": "status", "label": "Status", "type": "select", "options": ["pass", "fail", "advisory"]},
    {"key": "notes", "label": "Notes", "type": "text"},
    {"key": "checks", "label": "Checks", "type": "group", "fields": [
        {"key": "item", "label": "Item", "type": "text", "required": True},
        {"key": "reading", "label": "Reading", "type": "number"},
        {"key": "ok", "label": "OK", "type": "boolean"}]},
]}
GYM_FORM = {"companyId": "coastal-leisure", "name": "Gym Equipment Check", "fields": [
    {"key": "site", "label": "Site", "type": "text", "required": True},
    {"key": "inspected_on", "label": "Inspected on", "type": "date"},
    {"key": "status", "label": "Status", "type": "select", "options": ["pass", "fail"]},
    {"key": "machines", "label": "Machines", "type": "group", "fields": [
        {"key": "machine", "label": "Machine", "type": "text", "required": True},
        {"key": "ok", "label": "OK", "type": "boolean"},
        {"key": "hours", "label": "Hours used", "type": "number"}]},
]}

V4_SUBS = {
    "p1": ("pool", sub("s.okafor", "2026-11-02T08:00:00Z", {
        "site": "Harbour Baths", "chlorine": 1.2, "inspected_on": "2026-11-02", "status": "pass",
        "notes": "Water CLEAR, filters fine",
        "checks": [{"item": "pH", "reading": 7.2, "ok": True}, {"item": "Temp", "reading": 29, "ok": True}]})),
    "g1": ("gym", sub("s.okafor", "2026-11-03T09:00:00Z", {
        "site": "Harbour Baths", "inspected_on": "2026-11-03", "status": "pass",
        "machines": [{"machine": "Treadmill 1", "ok": True, "hours": 1200},
                     {"machine": "Rower", "ok": False, "hours": 300}]})),
    "p2": ("pool", sub("l.müller", "2026-11-09T08:00:00Z", {
        "site": "Harbour Baths", "chlorine": 3.5, "inspected_on": "2026-11-09", "status": "advisory",
        "notes": "Slight cloudiness",
        "checks": [{"item": "pH", "reading": 7.9, "ok": False}, {"item": "Temp", "reading": 27, "ok": True}]})),
    "p3": ("pool", sub("l.müller", "2026-11-16T08:00:00Z", {
        "site": "Northside Lido", "chlorine": 0.4, "inspected_on": "2026-11-16", "status": "fail",
        "checks": [{"item": "pH", "reading": 6.8, "ok": True}, {"item": "Temp", "reading": 31, "ok": False}]})),
    "g2": ("gym", sub("a.kowalski", "2026-11-17T10:00:00Z", {
        "site": "Northside Lido", "inspected_on": "2026-11-17", "status": "fail",
        "machines": [{"machine": "Treadmill 2", "ok": False, "hours": 4100}]})),
}

V4_POOL_FILTERS = {
    "range_gte": ({"answers.chlorine[gte]": "1.2"}, {"p1", "p2"}),
    "range_gt_is_exclusive": ({"answers.chlorine[gt]": "1.2"}, {"p2"}),
    "range_lt": ({"answers.chlorine[lt]": "1"}, {"p3"}),
    "range_date": ({"answers.inspected_on[gte]": "2026-11-09"}, {"p2", "p3"}),
    "range_date_between": ({"answers.inspected_on[gte]": "2026-11-01",
                            "answers.inspected_on[lt]": "2026-11-10"}, {"p1", "p2"}),
    "in_list": ({"answers.status[in]": "fail,advisory"}, {"p2", "p3"}),
    "contains_case_insensitive": ({"answers.notes[contains]": "clear"}, {"p1"}),
    "explicit_eq": ({"answers.status[eq]": "pass"}, {"p1"}),
    "range_same_item": ({"answers.checks.item": "pH", "answers.checks.reading[gt]": "7.5"}, {"p2"}),
    "range_same_item_2": ({"answers.checks.item": "Temp", "answers.checks.reading[gte]": "30"}, {"p3"}),
    "range_same_item_none": ({"answers.checks.ok": "false", "answers.checks.reading[lt]": "7"}, set()),
    "submitted_at_range": ({"submittedAt[gte]": "2026-11-09T00:00:00Z"}, {"p2", "p3"}),
}
V4_COMPANY_FILTERS = {
    "company_eq": ({"answers.status": "fail"}, {"p3", "g2"}),
    "company_in": ({"answers.status[in]": "fail,advisory"}, {"p2", "p3", "g2"}),
    "company_mixed": ({"answers.site": "Harbour Baths", "answers.inspected_on[lt]": "2026-11-05"}, {"p1", "g1"}),
    "company_field_on_one_form": ({"answers.chlorine[lt]": "1"}, {"p3"}),
    "company_same_item": ({"answers.machines.ok": "false", "answers.machines.hours[gt]": "1000"}, {"g2"}),
}


def v4(c: httpx.Client, seed: dict | None) -> dict:
    s = Suite("v4")
    seed = seed or {}

    def create_fixtures():
        company = fresh_company(c, "coastal-leisure")
        forms = {"pool": {**POOL_FORM, "companyId": company}, "gym": {**GYM_FORM, "companyId": company}}
        fids = {k: get_id(created(c.post("/forms", json=f), "POST /forms")) for k, f in forms.items()}
        ids = {k: get_id(created(c.post(f"/forms/{fids[form]}/submissions", json=p), "POST submission"))
               for k, (form, p) in V4_SUBS.items()}
        s.state.update(forms=fids, subs=ids, company=company)
    s.check("create_fixtures", create_fixtures)

    for name, (params, keys) in V4_POOL_FILTERS.items():
        def filt(params=params, keys=keys):
            ids = s.need("subs")
            got = {get_id(i) for i in list_subs(c, s.need("forms")["pool"], params)}
            want = {ids[k] for k in keys}
            assert got == want, f"{params}: got {len(got)} results, expected {len(want)} (exact set mismatch)"
        s.check(name, filt)

    s.check("rejects_unknown_operator", lambda: rejected(
        c.get(f"/forms/{s.need('forms')['pool']}/submissions", params={"answers.chlorine[between]": "1"}),
        "unknown operator"))

    def company_lists_all_forms_newest_first():
        ids, company = s.need("subs"), s.need("company")
        got = [get_id(i) for i in company_subs(c, company)]
        want = [ids[k] for k in ("g2", "p3", "p2", "g1", "p1")]
        assert got == want, f"company search returned {len(got)} submissions or the wrong order"
        got = [get_id(i) for i in company_subs(c, company, limit=2)]
        assert got == want[:2], "limit=2 on company search returned the wrong submissions"
    s.check("company_lists_all_forms_newest_first", company_lists_all_forms_newest_first)

    for name, (params, keys) in V4_COMPANY_FILTERS.items():
        def filt(params=params, keys=keys):
            ids = s.need("subs")
            got = {get_id(i) for i in company_subs(c, s.need("company"), params)}
            want = {ids[k] for k in keys}
            assert got == want, f"{params}: got {len(got)} results, expected {len(want)} (exact set mismatch)"
        s.check(name, filt)

    def company_search_is_scoped():
        ids = set(s.need("subs").values())
        leaked = ids & {get_id(i) for i in company_subs(c, "lakeside-dental")}
        assert not leaked, f"{len(leaked)} submissions of another company returned"
    s.check("company_search_is_scoped", company_search_is_scoped)

    s.check("company_search_requires_company", lambda: rejected(c.get("/submissions"), "missing companyId"))
    s.check("company_rejects_unknown_operator", lambda: rejected(
        c.get("/submissions", params={"companyId": s.need("company"), "answers.status[like]": "f"}),
        "unknown operator"))

    # Seeded production data: live data may contain extra records, so require superset + predicate.
    acme = next((f for f in seed.get("forms", []) if f["key"] == "acme_fire"), None)
    kitchen = next((f for f in seed.get("forms", []) if f["key"] == "northwind_kitchen"), None)
    acme_items = {x["id"]: converted(x["payload"]["answers"])["extinguishers"]
                  for x in seed.get("submissions", []) if x["form"] == "acme_fire"}
    kitchen_answers = {x["id"]: x["payload"]["answers"]
                       for x in seed.get("submissions", []) if x["form"] == "northwind_kitchen"}

    def ext_match(answers: dict, cond: Callable[[dict], bool]) -> bool:
        return any(isinstance(e, dict) and cond(e) for e in answers.get("extinguishers") or [])

    def seeded_nested_date_range():
        assert acme, "Acme form was not seeded"
        cond = lambda e: isinstance(e.get("expiry"), str) and e["expiry"] < "2026-01-01"
        want = {sid for sid, items in acme_items.items() if any(cond(e) for e in items)}
        items = list_subs(c, acme["id"], {"answers.extinguishers.expiry[lt]": "2026-01-01"})
        assert_filter(items, want, lambda a: ext_match(a, cond), "extinguishers.expiry[lt]")
    s.check("seeded_nested_date_range", seeded_nested_date_range)

    def seeded_range_same_item():
        assert acme, "Acme form was not seeded"
        cond = lambda e: e.get("pressure_ok") is False and isinstance(e.get("expiry"), str) and e["expiry"] < "2027-01-01"
        want = {sid for sid, items in acme_items.items() if any(cond(e) for e in items)}
        items = list_subs(c, acme["id"], {"answers.extinguishers.expiry[lt]": "2027-01-01",
                                          "answers.extinguishers.pressure_ok": "false"})
        assert_filter(items, want, lambda a: ext_match(a, cond), "expiry[lt] + pressure_ok on the same item")
    s.check("seeded_range_same_item", seeded_range_same_item)

    def seeded_number_range():
        assert kitchen, "kitchen form was not seeded"
        cond = lambda a: isinstance(a.get("fridge_temp"), (int, float)) and a["fridge_temp"] > 5
        want = {sid for sid, a in kitchen_answers.items() if cond(a)}
        assert_filter(list_subs(c, kitchen["id"], {"answers.fridge_temp[gt]": "5"}), want, cond, "fridge_temp[gt]")
    s.check("seeded_number_range", seeded_number_range)

    def seeded_contains_across_versions():
        assert kitchen, "kitchen form was not seeded"
        cond = lambda a: isinstance(a.get("notes"), str) and "fine" in a["notes"].lower()
        want = {sid for sid, a in kitchen_answers.items() if cond(a)}
        assert want, "no seeded notes contain 'fine'"
        assert_filter(list_subs(c, kitchen["id"], {"answers.notes[contains]": "FINE"}), want, cond,
                      "notes[contains] (a field removed in a later version)")
    s.check("seeded_contains_across_versions", seeded_contains_across_versions)

    def seeded_company_search():
        cond = lambda a: ext_match(a, lambda e: e.get("pressure_ok") is False)
        want = {sid for sid, items in acme_items.items() if any(e.get("pressure_ok") is False for e in items)}
        items = company_subs(c, "acme", {"answers.extinguishers.pressure_ok": "false"})
        assert_filter(items, want, cond, "company-wide extinguishers.pressure_ok=false")
    s.check("seeded_company_search", seeded_company_search)

    def seeded_company_date_range():
        cond = lambda a: isinstance(a.get("audit_date"), str) and a["audit_date"] >= "2026-09-01"
        want = {sid for sid, a in kitchen_answers.items() if cond(a)}
        items = company_subs(c, "northwind-foods", {"answers.audit_date[gte]": "2026-09-01"})
        assert_filter(items, want, cond, "company-wide audit_date[gte]")
    s.check("seeded_company_date_range", seeded_company_date_range)

    return s.results

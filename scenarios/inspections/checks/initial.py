"""Checks introduced by the `initial` phase (initial build)."""
from __future__ import annotations

import httpx

from harness.suite import Suite, as_list, created, difference, get_id, rejected
from scenarios.inspections.checks.helpers import (assert_filter, assert_submission, fetch_sub, list_subs, sub,
                                                  version_content)
from scenarios.inspections.common import FIRE_FIELDS, subset


V0_SUBS = {
    "s1": sub("j.ramirez", "2026-09-01T08:00:00Z", {
        "site_name": "Depot 4", "extinguisher_location": "Loading bay", "extinguisher_expiry": "2026-11-30",
        "pressure_ok": False, "condition": "poor", "score": 6}),
    "s2": sub("a.kowalski", "2026-09-02T08:15:00Z", {
        "site_name": "Depot 4", "pressure_ok": True, "condition": "poor", "score": 3}),
    "s3": sub("m.nguyen", "2026-09-03T12:30:00Z", {
        "site_name": "Head office", "pressure_ok": False, "condition": "good", "score": 6}),
    "s4": sub("l.müller", "2026-09-04T07:05:00Z", {
        "site_name": "Zürich Hbf – Gleis 7", "condition": "fair", "score": 10}),
    "s5": sub("s.okafor", "2026-09-05T16:45:00Z", {
        "site_name": "Riverside Plant", "extinguisher_expiry": "2027-02-28",
        "pressure_ok": True, "condition": "good", "score": 0}),
}

V0_FILTERS = {
    "filter_select": ({"answers.condition": "poor"}, {"s1", "s2"}),
    "filter_and": ({"answers.condition": "poor", "answers.pressure_ok": "false"}, {"s1"}),
    "filter_boolean": ({"answers.pressure_ok": "false"}, {"s1", "s3"}),
    "filter_number": ({"answers.score": "6"}, {"s1", "s3"}),
    "filter_date": ({"answers.extinguisher_expiry": "2026-11-30"}, {"s1"}),
    "filter_unicode_text": ({"answers.site_name": "Zürich Hbf – Gleis 7"}, {"s4"}),
}

V0_VALID = {"site_name": "Depot 9", "condition": "good", "score": 5}
V0_INVALID = {
    "missing_required": {"condition": "good"},
    "wrong_type_number": {**V0_VALID, "score": "5"},
    "number_above_max": {**V0_VALID, "score": 11},
    "number_below_min": {**V0_VALID, "score": -1},
    "select_not_in_options": {**V0_VALID, "condition": "broken"},
    "unknown_key": {**V0_VALID, "colour": "red"},
    "invalid_date": {**V0_VALID, "extinguisher_expiry": "2026-13-01"},
    "wrong_type_boolean": {**V0_VALID, "pressure_ok": "yes"},
}


def v0(c: httpx.Client, seed: dict | None, exclude_forms: set[str] = frozenset()) -> dict:
    s = Suite("v0")
    form = {"companyId": "lakeside-dental", "name": "Clinic Fire Safety Walkthrough", "fields": FIRE_FIELDS}

    def create_and_get_form():
        fid = get_id(created(c.post("/forms", json=form), "POST /forms"))
        s.state["form_id"] = fid
        r = c.get(f"/forms/{fid}")
        assert r.status_code == 200, f"GET /forms/{fid}: HTTP {r.status_code}"
        assert subset(form, r.json()), f"form differs: {difference(form, r.json())}"
    s.check("create_and_get_form", create_and_get_form)

    def list_forms_by_company():
        fid = s.need("form_id")
        other = get_id(created(c.post("/forms", json={**form, "companyId": "harbor-marine",
                                                      "name": "Deck Safety Round"}), "POST /forms"))
        r = c.get("/forms", params={"companyId": "lakeside-dental"})
        assert r.status_code == 200, f"GET /forms: HTTP {r.status_code}"
        listed = {get_id(f) for f in as_list(r.json())}
        assert fid in listed, "company's form not listed"
        assert other not in listed, "another company's form listed"
    s.check("list_forms_by_company", list_forms_by_company)

    def create_submissions():
        fid = s.need("form_id")
        ids = {}
        for k, payload in V0_SUBS.items():
            body = created(c.post(f"/forms/{fid}/submissions", json=payload), "POST submission")
            ids[k] = get_id(body)
        s.state["subs"] = ids
    s.check("create_submissions", create_submissions)

    def read_back_exact():
        ids = s.need("subs")
        for k, payload in V0_SUBS.items():
            got = fetch_sub(c, ids[k])
            assert str(got.get("formId")) == s.need("form_id"), f"submission {ids[k]}: formId {got.get('formId')!r}"
            assert_submission(got, payload)
    s.check("read_back_exact", read_back_exact)

    for case, answers in V0_INVALID.items():
        def reject(answers=answers, case=case):
            fid = s.need("form_id")
            r = c.post(f"/forms/{fid}/submissions", json=sub("t.sato", "2026-09-06T09:00:00Z", answers))
            rejected(r, case)
        s.check(f"rejects_{case}", reject)

    def invalid_not_stored():
        ids = s.need("subs")
        got = {get_id(i) for i in list_subs(c, s.need("form_id"))}
        assert got == set(ids.values()), f"expected {len(ids)} submissions on the form, found {len(got)}"
    s.check("invalid_not_stored", invalid_not_stored)

    for name, (params, keys) in V0_FILTERS.items():
        def filt(params=params, keys=keys):
            ids = s.need("subs")
            got = {get_id(i) for i in list_subs(c, s.need("form_id"), params)}
            want = {ids[k] for k in keys}
            assert got == want, f"{params}: got {len(got)} results, expected {len(want)} (exact set mismatch)"
        s.check(name, filt)

    def newest_first():
        ids = s.need("subs")
        got = [get_id(i) for i in list_subs(c, s.need("form_id"))]
        assert got == [ids[k] for k in ("s5", "s4", "s3", "s2", "s1")], "not ordered by submittedAt desc"
    s.check("list_newest_first", newest_first)

    def limit_param():
        ids = s.need("subs")
        got = [get_id(i) for i in list_subs(c, s.need("form_id"), limit=2)]
        assert got == [ids["s5"], ids["s4"]], f"limit=2 returned {len(got)} items or the wrong ones"
    s.check("limit_param", limit_param)

    if seed is not None:
        forms = [f for f in seed["forms"] if f["key"] not in exclude_forms]
        subs = [x for x in seed["submissions"] if x["form"] not in exclude_forms]

        def seeded_forms_intact():
            bad = []
            for f in forms:
                want = f["payload"]
                r = c.get(f"/forms/{f['id']}")
                # Once forms are versioned and a seeded form was edited, compare its original version.
                if r.status_code == 200 and isinstance(r.json().get("version"), int) and r.json()["version"] > 1:
                    r, want = c.get(f"/forms/{f['id']}/versions/1"), version_content(f["payload"])
                if r.status_code != 200 or not subset(want, r.json()):
                    why = f"HTTP {r.status_code}" if r.status_code != 200 else difference(want, r.json())
                    bad.append(f"{f['payload']['companyId']}'s \"{f['payload']['name']}\" ({f['id']}): {why}")
            assert not bad, f"{len(bad)}/{len(forms)} seeded forms differ: {bad}"
        s.check("seeded_forms_intact", seeded_forms_intact)

        def seeded_submissions_intact():
            assert subs, "no seeded submissions"
            bad = []
            for x in subs:
                try:
                    assert_submission(fetch_sub(c, x["id"]), x["payload"])
                except AssertionError as e:
                    bad.append(f"submission {x['id']}: {e}")
            assert not bad, f"{len(bad)}/{len(subs)} seeded submissions differ, e.g. {bad[0][:300]}"
        s.check("seeded_submissions_intact", seeded_submissions_intact)

        kitchen = next((f for f in seed["forms"] if f["key"] == "northwind_kitchen"), None)
        kitchen_subs = [x for x in seed["submissions"] if x["form"] == "northwind_kitchen"]

        def seeded_filter_boolean():
            assert kitchen, "kitchen form was not seeded"
            want = {x["id"] for x in kitchen_subs if x["payload"]["answers"].get("handwash_ok") is False}
            items = list_subs(c, kitchen["id"], {"answers.handwash_ok": "false"})
            assert_filter(items, want, lambda a: a.get("handwash_ok") is False, "handwash_ok=false")
        s.check("seeded_filter_boolean", seeded_filter_boolean)

        def seeded_filter_and():
            assert kitchen, "kitchen form was not seeded"
            want = {x["id"] for x in kitchen_subs if x["payload"]["answers"].get("kitchen_area") == "prep"
                    and x["payload"]["answers"].get("pest_signs") is True}
            items = list_subs(c, kitchen["id"], {"answers.kitchen_area": "prep", "answers.pest_signs": "true"})
            assert_filter(items, want, lambda a: a.get("kitchen_area") == "prep" and a.get("pest_signs") is True,
                          "kitchen_area=prep&pest_signs=true")
        s.check("seeded_filter_and", seeded_filter_and)

    return s.results

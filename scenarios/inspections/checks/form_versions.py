"""Checks introduced by the `form_versions` phase (form versions)."""
from __future__ import annotations

from typing import Any

import httpx

from harness.suite import Suite, as_list, created, difference, get_id, rejected
from scenarios.inspections.checks.helpers import (assert_filter, assert_submission, fetch_sub, list_subs, sub,
                                                  version_content)
from scenarios.inspections.checks.multi_level import nested_form
from scenarios.inspections.checks.repeating_groups import converted
from scenarios.inspections.common import subset
from scenarios.inspections.seed import FORMS as SEED_FORMS


CREW_V1 = {"companyId": "harbor-marine", "name": "Crew Welfare Check", "fields": [
    {"key": "site_name", "label": "Site", "type": "text", "required": True},
    {"key": "water_ok", "label": "Drinking water OK", "type": "boolean"},
    {"key": "temp", "label": "Hot water temperature", "type": "number", "min": 0, "max": 100},
    {"key": "notes", "label": "Notes", "type": "text"},
    {"key": "score", "label": "Score", "type": "number", "min": 0, "max": 10},
]}
# Relabels, removes, adds (incl. a required field and a group) and retypes `score` from number to select.
CREW_V2 = {"companyId": "harbor-marine", "name": "Crew Welfare Check", "fields": [
    {"key": "site_name", "label": "Vessel", "type": "text", "required": True},
    {"key": "inspector", "label": "Inspector", "type": "text", "required": True},
    {"key": "temp", "label": "Hot water temperature", "type": "number", "min": 0, "max": 100},
    {"key": "score", "label": "Rating", "type": "select", "options": ["low", "medium", "high"]},
    {"key": "cabins", "label": "Cabins", "type": "group", "fields": [
        {"key": "cabin", "label": "Cabin", "type": "text", "required": True},
        {"key": "clean", "label": "Clean", "type": "boolean"}]},
]}

V3_OLD = {
    "a": sub("e.lindqvist", "2026-11-01T08:00:00Z", {
        "site_name": "MV Nordlys", "water_ok": False, "temp": 62, "notes": "Filter replaced", "score": 6}),
    "b": sub("k.mensah", "2026-11-02T08:30:00Z", {"site_name": "Sea Breeze", "water_ok": True, "score": 9}),
}
V3_NEW = {
    "c": sub("k.mensah", "2026-11-03T09:00:00Z", {
        "site_name": "MV Nordlys", "inspector": "k.mensah", "score": "high",
        "cabins": [{"cabin": "C1", "clean": True}, {"cabin": "C2", "clean": False}]}),
    "d": sub("t.sato", "2026-11-04T09:30:00Z", {
        "site_name": "Sea Breeze", "inspector": "t.sato", "score": "low", "temp": 55}),
}
V3_FILTERS = {
    "version_filter_old_type": ({"answers.score": "6"}, {"a"}),
    "version_filter_new_type": ({"answers.score": "high"}, {"c"}),
    "version_filter_across_versions": ({"answers.site_name": "Sea Breeze"}, {"b", "d"}),
    "version_filter_removed_field": ({"answers.water_ok": "false"}, {"a"}),
    "version_filter_added_field": ({"answers.inspector": "t.sato"}, {"d"}),
    "version_filter_added_group": ({"answers.cabins.clean": "false"}, {"c"}),
    "version_filter_kept_field": ({"answers.temp": "62"}, {"a"}),
}
V3_INVALID_NEW = {
    "removed_field": {"site_name": "MV Nordlys", "inspector": "t.sato", "water_ok": True},
    "missing_new_required_field": {"site_name": "MV Nordlys"},
    "old_field_type": {"site_name": "MV Nordlys", "inspector": "t.sato", "score": 6},
}


def kitchen_v2() -> dict:
    base = SEED_FORMS["northwind_kitchen"][0]
    fields = [f for f in base["fields"] if f["key"] != "notes"]
    fields.append({"key": "allergen_board_ok", "label": "Allergen board up to date", "type": "boolean",
                   "required": True})
    return {**base, "fields": fields}


def render_expected(fields: list, answers: dict) -> list:
    out = []
    for f in fields:
        value = answers.get(f["key"])
        if f.get("type") == "group" and isinstance(value, list):
            value = [render_expected(f.get("fields") or [], i) for i in value]
        out.append((f.get("key"), f.get("label"), f.get("type"), value))
    return out


def render_got(fields: Any) -> list:
    out = []
    for f in fields or []:
        value = f.get("value")
        if f.get("type") == "group" and isinstance(value, list):
            value = [render_got(i.get("fields")) for i in value if isinstance(i, dict)]
        out.append((f.get("key"), f.get("label"), f.get("type"), value))
    return out


def assert_rendered(c: httpx.Client, sid: str, version: int, fields: list, answers: dict) -> None:
    r = c.get(f"/submissions/{sid}/rendered")
    assert r.status_code == 200, f"GET /submissions/{sid}/rendered: HTTP {r.status_code}"
    body = r.json()
    assert body.get("formVersion") == version, f"rendered formVersion {body.get('formVersion')!r}, expected {version}"
    want, got = render_expected(fields, answers), render_got(body.get("fields"))
    assert got == want, f"rendered fields differ: got {got}, expected {want}"


def form_version(c: httpx.Client, fid: str) -> Any:
    r = c.get(f"/forms/{fid}")
    assert r.status_code == 200, f"GET /forms/{fid}: HTTP {r.status_code}"
    return r.json().get("version")


def v3(c: httpx.Client, seed: dict | None) -> dict:
    s = Suite("v3")
    seed = seed or {}
    seeded_forms = {f["key"]: f for f in seed.get("forms", [])}

    # Existing data first, before anything in this suite edits a seeded form. Later phases re-run this
    # suite, by which time the kitchen form has been edited before; everything else stays version 1.
    def existing_forms_are_version_1():
        assert seeded_forms, "no seeded forms"
        for key, f in seeded_forms.items():
            label = f"{f['payload']['companyId']}'s \"{f['payload']['name']}\""
            version = form_version(c, f["id"])
            ok = isinstance(version, int) and version >= 1 if key == "northwind_kitchen" else version == 1
            assert ok, f"{label}: version {version!r}, expected 1"
            r = c.get(f"/forms/{f['id']}/versions/1")
            assert r.status_code == 200, f"{label}: GET versions/1 HTTP {r.status_code}"
            if key != "acme_fire":
                want = version_content(f["payload"])
                assert subset(want, r.json()), \
                    f"{label}: version 1 differs from the original form: {difference(want, r.json())}"
    s.check("existing_forms_are_version_1", existing_forms_are_version_1)

    def existing_submissions_are_version_1():
        subs = seed.get("submissions", [])
        assert subs, "no seeded submissions"
        bad = [x["id"] for x in subs if fetch_sub(c, x["id"]).get("formVersion") != 1]
        assert not bad, f"{len(bad)}/{len(subs)} existing submissions don't report formVersion 1"
    s.check("existing_submissions_are_version_1", existing_submissions_are_version_1)

    def acme_renders_converted_version_1():
        acme = seeded_forms["acme_fire"]
        r = c.get(f"/forms/{acme['id']}/versions/1")
        assert r.status_code == 200, f"GET acme versions/1: HTTP {r.status_code}"
        fields = r.json().get("fields") or []
        assert [f.get("key") for f in fields] == ["site_name", "extinguishers", "condition", "score"], \
            "Acme's version 1 is not the converted form"
        x = next(x for x in seed["submissions"] if x["form"] == "acme_fire" and
                 x["payload"]["answers"].get("extinguisher_location"))
        # Labels of the converted group weren't specified, so compare against the app's own version 1.
        assert_rendered(c, x["id"], 1, fields, converted(x["payload"]["answers"]))
    s.check("acme_renders_converted_version_1", acme_renders_converted_version_1)

    def create_form_is_version_1():
        fid = get_id(created(c.post("/forms", json=CREW_V1), "POST /forms"))
        s.state["form_id"] = fid
        assert form_version(c, fid) == 1, "a new form is not version 1"
        ids = {k: get_id(created(c.post(f"/forms/{fid}/submissions", json=p), "POST submission"))
               for k, p in V3_OLD.items()}
        s.state["subs"] = ids
    s.check("create_form_is_version_1", create_form_is_version_1)

    def edit_creates_version_2():
        fid = s.need("form_id")
        s.need("subs")
        r = c.put(f"/forms/{fid}", json=CREW_V2)
        assert r.status_code in (200, 201), f"PUT /forms/{fid}: HTTP {r.status_code} {r.text[:200]}"
        assert r.json().get("version") == 2, f"PUT returned version {r.json().get('version')!r}"
        got = c.get(f"/forms/{fid}").json()
        assert got.get("version") == 2, f"GET /forms/:id returned version {got.get('version')!r}, expected 2"
        assert subset(CREW_V2, got), f"GET /forms/:id is not version 2: {difference(CREW_V2, got)}"
        assert get_id(got) == fid, "form id changed"
        s.state["edited"] = True
        ids = {k: get_id(created(c.post(f"/forms/{fid}/submissions", json=p), "POST submission"))
               for k, p in V3_NEW.items()}
        s.state["subs"] = {**s.state["subs"], **ids}
    s.check("edit_creates_version_2", edit_creates_version_2)

    def versions_are_retrievable():
        fid = s.need("form_id")
        s.need("edited")
        for version, definition in ((1, CREW_V1), (2, CREW_V2)):
            r = c.get(f"/forms/{fid}/versions/{version}")
            assert r.status_code == 200, f"GET versions/{version}: HTTP {r.status_code}"
            want = version_content(definition)
            assert subset(want, r.json()), \
                f"version {version} differs from what was saved: {difference(want, r.json())}"
        r = c.get(f"/forms/{fid}/versions/99")
        assert r.status_code == 404, f"GET versions/99: expected 404, got HTTP {r.status_code}"
    s.check("versions_are_retrievable", versions_are_retrievable)

    def listed_forms_show_latest_version():
        fid = s.need("form_id")
        s.need("edited")
        r = c.get("/forms", params={"companyId": "harbor-marine"})
        form = next((f for f in as_list(r.json()) if get_id(f) == fid), None)
        assert form, "the form is not listed"
        assert form.get("version") == 2, f"listed form has version {form.get('version')!r}, expected 2"
        assert subset(CREW_V2, form), f"listed form is not the latest version: {difference(CREW_V2, form)}"
    s.check("listed_forms_show_latest_version", listed_forms_show_latest_version)

    def rejects_company_change():
        fid = s.need("form_id")
        s.need("edited")
        rejected(c.put(f"/forms/{fid}", json={**CREW_V2, "companyId": "someone-else"}), "companyId change")
        assert form_version(c, fid) == 2, "a rejected edit still created a version"
    s.check("rejects_company_change", rejects_company_change)

    def rejects_invalid_edit():
        fid = s.need("form_id")
        s.need("edited")
        rejected(c.put(f"/forms/{fid}", json={**nested_form(5), "companyId": "harbor-marine"}), "5-level edit")
        assert form_version(c, fid) == 2, "a rejected edit still created a version"
    s.check("rejects_invalid_edit", rejects_invalid_edit)

    def submissions_record_version():
        ids = s.need("subs")
        s.need("edited")
        want = {**{k: 1 for k in V3_OLD}, **{k: 2 for k in V3_NEW}}
        for k, version in want.items():
            got = fetch_sub(c, ids[k]).get("formVersion")
            assert got == version, f"submission {ids[k]}: formVersion {got!r}, expected {version}"
        listed = {get_id(i): i.get("formVersion") for i in list_subs(c, s.need("form_id"))}
        assert all(listed.get(ids[k]) == v for k, v in want.items()), "list responses lack the right formVersion"
    s.check("submissions_record_version", submissions_record_version)

    def old_answers_unchanged():
        ids = s.need("subs")
        s.need("edited")
        for k, payload in {**V3_OLD, **V3_NEW}.items():
            assert_submission(fetch_sub(c, ids[k]), payload)
    s.check("old_answers_unchanged", old_answers_unchanged)

    for case, answers in V3_INVALID_NEW.items():
        def reject(answers=answers, case=case):
            s.need("edited")
            payload = sub("t.sato", "2026-11-05T09:00:00Z", answers)
            rejected(c.post(f"/forms/{s.need('form_id')}/submissions", json=payload), case)
        s.check(f"rejects_new_{case}", reject)

    def renders_old_version():
        ids = s.need("subs")
        s.need("edited")
        for k in V3_OLD:
            assert_rendered(c, ids[k], 1, CREW_V1["fields"], V3_OLD[k]["answers"])
    s.check("renders_old_version", renders_old_version)

    def renders_new_version():
        ids = s.need("subs")
        s.need("edited")
        for k in V3_NEW:
            assert_rendered(c, ids[k], 2, CREW_V2["fields"], V3_NEW[k]["answers"])
    s.check("renders_new_version", renders_new_version)

    for name, (params, keys) in V3_FILTERS.items():
        def filt(params=params, keys=keys):
            ids = s.need("subs")
            s.need("edited")
            got = {get_id(i) for i in list_subs(c, s.need("form_id"), params)}
            want = {ids[k] for k in keys}
            assert got == want, f"{params}: got {len(got)} results, expected {len(want)} (exact set mismatch)"
        s.check(name, filt)

    # Editing a seeded form: its existing submissions must be unaffected.
    kitchen = seeded_forms.get("northwind_kitchen")
    kitchen_subs = [x for x in seed.get("submissions", []) if x["form"] == "northwind_kitchen"]

    def edit_seeded_form():
        assert kitchen, "kitchen form was not seeded"
        before = form_version(c, kitchen["id"])
        r = c.put(f"/forms/{kitchen['id']}", json=kitchen_v2())
        assert r.status_code in (200, 201), f"PUT kitchen form: HTTP {r.status_code} {r.text[:200]}"
        after = form_version(c, kitchen["id"])
        assert isinstance(before, int) and after == before + 1, f"kitchen form version {before!r} -> {after!r}"
        s.state["kitchen_version"] = after
    s.check("edit_seeded_form", edit_seeded_form)

    def seeded_submissions_unchanged_after_edit():
        s.need("kitchen_version")
        bad = []
        for x in kitchen_subs:
            got = fetch_sub(c, x["id"])
            try:
                assert_submission(got, x["payload"])
                assert got.get("formVersion") == 1, f"formVersion {got.get('formVersion')!r}"
            except AssertionError as e:
                bad.append(f"{x['id']}: {e}")
        assert not bad, f"{len(bad)}/{len(kitchen_subs)} kitchen submissions changed, e.g. {bad[0][:300]}"
    s.check("seeded_submissions_unchanged_after_edit", seeded_submissions_unchanged_after_edit)

    def seeded_renders_old_version():
        s.need("kitchen_version")
        x = next(x for x in kitchen_subs if x["payload"]["answers"].get("notes"))
        assert_rendered(c, x["id"], 1, SEED_FORMS["northwind_kitchen"][0]["fields"], x["payload"]["answers"])
    s.check("seeded_renders_old_version", seeded_renders_old_version)

    def seeded_new_submission_uses_new_version():
        version = s.need("kitchen_version")
        base = {"kitchen_area": "prep", "handwash_ok": True, "audit_date": "2026-11-06"}
        rejected(c.post(f"/forms/{kitchen['id']}/submissions", json=sub("m.nguyen", "2026-11-06T10:00:00Z", base)),
                 "missing new required field")
        body = created(c.post(f"/forms/{kitchen['id']}/submissions", json=sub(
            "m.nguyen", "2026-11-06T10:05:00Z", {**base, "allergen_board_ok": True})), "POST new-version submission")
        assert fetch_sub(c, get_id(body)).get("formVersion") == version, \
            f"new submission is not on the latest version ({version})"
    s.check("seeded_new_submission_uses_new_version", seeded_new_submission_uses_new_version)

    def seeded_filter_on_removed_field():
        s.need("kitchen_version")
        want = {x["id"] for x in kitchen_subs if x["payload"]["answers"].get("notes") == "All fine."}
        items = list_subs(c, kitchen["id"], {"answers.notes": "All fine."})
        assert_filter(items, want, lambda a: a.get("notes") == "All fine.", "notes (removed in v2)")
    s.check("seeded_filter_on_removed_field", seeded_filter_on_removed_field)

    def seeded_filter_on_kept_field():
        s.need("kitchen_version")
        want = {x["id"] for x in kitchen_subs if x["payload"]["answers"].get("handwash_ok") is False}
        items = list_subs(c, kitchen["id"], {"answers.handwash_ok": "false"})
        assert_filter(items, want, lambda a: a.get("handwash_ok") is False, "handwash_ok=false")
    s.check("seeded_filter_on_kept_field", seeded_filter_on_kept_field)

    return s.results

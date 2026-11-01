"""Checks introduced by the `offline_sync` phase (offline sync)."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx

from harness.suite import Suite, created, get_id, rejected
from scenarios.inspections.checks.form_versions import assert_rendered, form_version
from scenarios.inspections.checks.helpers import assert_submission, fetch_sub, list_subs, sub
from scenarios.inspections.checks.repeating_groups import converted
from scenarios.inspections.seed import FORMS as SEED_FORMS


VAN_V1 = {"companyId": "meridian-logistics", "name": "Van Daily Check", "fields": [
    {"key": "van_id", "label": "Van", "type": "text", "required": True},
    {"key": "fuel_level", "label": "Fuel", "type": "select", "options": ["full", "half", "low"]},
    {"key": "tyres_ok", "label": "Tyres OK", "type": "boolean"},
    {"key": "mileage", "label": "Mileage", "type": "number", "min": 0},
]}
# Retypes fuel_level (select -> percentage) and adds a required driver.
VAN_V2 = {"companyId": "meridian-logistics", "name": "Van Daily Check", "fields": [
    {"key": "van_id", "label": "Van", "type": "text", "required": True},
    {"key": "fuel_level", "label": "Fuel (%)", "type": "number", "min": 0, "max": 100},
    {"key": "tyres_ok", "label": "Tyres OK", "type": "boolean", "required": True},
    {"key": "driver", "label": "Driver", "type": "text", "required": True},
    {"key": "mileage", "label": "Mileage", "type": "number", "min": 0},
]}
WEEKLY_FORM = {"companyId": "meridian-logistics", "name": "Van Weekly Check", "fields": [
    {"key": "van_id", "label": "Van", "type": "text", "required": True},
    {"key": "oil_ok", "label": "Oil level OK", "type": "boolean"},
]}

# Every version-2 fixture below uses fuel_level >= 50, so the fuel filters have exact expected sets.
LATE_V1 = sub("p.o'connor", "2026-11-18T07:30:00Z",
              {"van_id": "VAN-07", "fuel_level": "half", "tyres_ok": True, "mileage": 48210})
CURRENT_V2 = sub("t.sato", "2026-11-21T07:45:00Z",
                 {"van_id": "VAN-07", "fuel_level": 20, "tyres_ok": True, "driver": "t.sato", "mileage": 48390})


def v2_van(van: str, fuel: int, at: str, **extra: Any) -> dict:
    return {**sub("r.haddad", at, {"van_id": van, "fuel_level": fuel, "tyres_ok": True, "driver": "r.haddad"}),
            **extra}


def v5(c: httpx.Client, seed: dict | None) -> dict:
    s = Suite("v5")
    seed = seed or {}

    def post(fid: str, payload: dict) -> httpx.Response:
        return c.post(f"/forms/{fid}/submissions", json=payload)

    def vans(van: str) -> list:
        return list_subs(c, s.need("van"), {"answers.van_id": van})

    def create_fixtures():
        van = get_id(created(c.post("/forms", json=VAN_V1), "POST van form"))
        r = c.put(f"/forms/{van}", json=VAN_V2)
        assert r.status_code in (200, 201) and form_version(c, van) == 2, f"PUT van form: HTTP {r.status_code}"
        weekly = get_id(created(c.post("/forms", json=WEEKLY_FORM), "POST weekly form"))
        s.state.update(van=van, weekly=weekly)
    s.check("create_fixtures", create_fixtures)

    # --- submitting against a version
    def late_upload_old_version():
        body = created(post(s.need("van"), {**LATE_V1, "formVersion": 1}), "late upload against version 1")
        sid = get_id(body)
        got = fetch_sub(c, sid)
        assert got.get("formVersion") == 1, f"formVersion {got.get('formVersion')!r}, expected 1"
        assert_submission(got, LATE_V1)
        assert_rendered(c, sid, 1, VAN_V1["fields"], LATE_V1["answers"])
        s.state["late"] = sid
    s.check("late_upload_old_version", late_upload_old_version)

    def late_upload_validated_against_its_version():
        base = {**LATE_V1, "formVersion": 1}
        rejected(post(s.need("van"), {**base, "answers": {**LATE_V1["answers"], "fuel_level": 50}}),
                 "version-2 value in a version-1 upload")
        rejected(post(s.need("van"), {**base, "answers": {**LATE_V1["answers"], "driver": "x"}}),
                 "version-2 field in a version-1 upload")
    s.check("late_upload_validated_against_its_version", late_upload_validated_against_its_version)

    def latest_version_by_default():
        body = created(post(s.need("van"), CURRENT_V2), "upload without formVersion")
        assert fetch_sub(c, get_id(body)).get("formVersion") == 2, "upload without formVersion is not on version 2"
        s.state["current"] = get_id(body)
        rejected(post(s.need("van"), LATE_V1), "version-1 answers without formVersion")
    s.check("latest_version_by_default", latest_version_by_default)

    def explicit_latest_version():
        body = created(post(s.need("van"), {**v2_van("VAN-08", 75, "2026-11-21T08:00:00Z"), "formVersion": 2}),
                       "upload with formVersion 2")
        assert fetch_sub(c, get_id(body)).get("formVersion") == 2, "formVersion 2 not recorded"
    s.check("explicit_latest_version", explicit_latest_version)

    s.check("rejects_unknown_version", lambda: rejected(
        post(s.need("van"), {**v2_van("VAN-09", 70, "2026-11-21T08:05:00Z"), "formVersion": 9}), "formVersion 9"))

    def late_upload_filter_old_version():
        got = {get_id(i) for i in list_subs(c, s.need("van"), {"answers.fuel_level": "half"})}
        assert got == {s.need("late")}, f"fuel_level=half returned {len(got)} submissions, expected the late upload"
    s.check("late_upload_filter_old_version", late_upload_filter_old_version)

    def late_upload_filter_new_version():
        got = {get_id(i) for i in list_subs(c, s.need("van"), {"answers.fuel_level[lt]": "30"})}
        assert got == {s.need("current")}, f"fuel_level[lt]=30 returned {len(got)} submissions, expected 1"
    s.check("late_upload_filter_new_version", late_upload_filter_new_version)

    # --- safe retries
    retry = v2_van("VAN-11", 80, "2026-11-22T06:00:00Z", clientSubmissionId="tab-17-000123")

    def retry_returns_stored():
        first = created(post(s.need("van"), retry), "first upload")
        assert first.get("clientSubmissionId") == "tab-17-000123", "clientSubmissionId not returned"
        r = post(s.need("van"), retry)
        assert r.status_code == 200, f"retry: expected 200, got HTTP {r.status_code}"
        assert get_id(r.json()) == get_id(first), "retry returned a different submission"
        assert len(vans("VAN-11")) == 1, f"{len(vans('VAN-11'))} submissions stored for one upload"
        s.state["retry_id"] = get_id(first)
    s.check("retry_returns_stored", retry_returns_stored)

    def retry_with_different_content_409():
        sid = s.need("retry_id")
        changed = {**retry, "answers": {**retry["answers"], "fuel_level": 10}}
        r = post(s.need("van"), changed)
        assert r.status_code == 409, f"expected 409, got HTTP {r.status_code}"
        assert_submission(fetch_sub(c, sid), retry)
    s.check("retry_with_different_content_409", retry_with_different_content_409)

    def client_id_scoped_per_form():
        body = created(post(s.need("weekly"), {**sub("r.haddad", "2026-11-22T06:10:00Z",
                                                     {"van_id": "VAN-11", "oil_ok": True}),
                                               "clientSubmissionId": "tab-17-000123"}),
                       "same clientSubmissionId on another form")
        assert get_id(body) != s.need("retry_id"), "an upload to another form was treated as a retry"
    s.check("client_id_scoped_per_form", client_id_scoped_per_form)

    def concurrent_retries_single_submission():
        payload = v2_van("VAN-12", 90, "2026-11-22T06:20:00Z", clientSubmissionId="tab-17-000200")
        fid, base = s.need("van"), str(c.base_url)

        def upload(_: int) -> httpx.Response:
            with httpx.Client(base_url=base, timeout=60) as own:
                return own.post(f"/forms/{fid}/submissions", json=payload)

        with ThreadPoolExecutor(max_workers=10) as pool:
            responses = list(pool.map(upload, range(10)))
        bad = [r.status_code for r in responses if r.status_code not in (200, 201)]
        assert not bad, f"{len(bad)}/10 concurrent retries failed (e.g. HTTP {bad[0]})"
        assert len({get_id(r.json()) for r in responses}) == 1, "concurrent retries returned different submissions"
        assert len(vans("VAN-12")) == 1, f"{len(vans('VAN-12'))} submissions stored for one upload"
    s.check("concurrent_retries_single_submission", concurrent_retries_single_submission)

    # --- batch upload
    def batch_mixed_results():
        van, weekly = s.need("van"), s.need("weekly")
        retry_id = s.need("retry_id")
        entries = [
            {"formId": van, **v2_van("VAN-20", 60, "2026-11-23T07:00:00Z", clientSubmissionId="tab-9-000001")},
            {"formId": van, "formVersion": 1,
             **sub("m.nguyen", "2026-11-19T07:00:00Z", {"van_id": "VAN-21", "fuel_level": "low"})},
            {"formId": van, **sub("m.nguyen", "2026-11-23T07:05:00Z",
                                  {"van_id": "VAN-22", "fuel_level": 55, "tyres_ok": True})},
            {"formId": "no-such-form", **sub("m.nguyen", "2026-11-23T07:06:00Z", {"van_id": "VAN-23"})},
            {"formId": van, **retry},
            {"formId": van, **{**retry, "answers": {**retry["answers"], "fuel_level": 15}}},
            {"formId": weekly, **sub("m.nguyen", "2026-11-23T07:10:00Z", {"van_id": "VAN-20", "oil_ok": False})},
        ]
        r = c.post("/submissions/batch", json={"submissions": entries})
        assert r.status_code == 200, f"POST /submissions/batch: HTTP {r.status_code} {r.text[:200]}"
        results = r.json().get("results")
        assert isinstance(results, list) and len(results) == len(entries), "one result per entry expected"
        statuses = [x.get("status") for x in results]
        want = [201, 201, 400, 404, 200, 409, 201]
        ok = all(got == w or (w == 404 and got == 400) for got, w in zip(statuses, want))
        assert ok, f"statuses {statuses}, expected {want}"
        ids = [get_id(x.get("submission")) for x in results]
        assert ids[4] == retry_id, "the retried entry didn't return the stored submission"
        assert fetch_sub(c, ids[1]).get("formVersion") == 1, "version-1 entry not stored on version 1"
        for i in (0, 6):
            assert fetch_sub(c, ids[i]).get("answers", {}).get("van_id") == "VAN-20", f"entry {i} not stored"
        assert not vans("VAN-22"), "an invalid entry was stored"
        assert len(vans("VAN-11")) == 1, "the retried entry was stored twice"
    s.check("batch_mixed_results", batch_mixed_results)

    def batch_dedupes_within_batch():
        entry = {"formId": s.need("van"),
                 **v2_van("VAN-30", 65, "2026-11-24T07:00:00Z", clientSubmissionId="tab-9-000007")}
        r = c.post("/submissions/batch", json={"submissions": [entry, entry]})
        assert r.status_code == 200, f"POST /submissions/batch: HTTP {r.status_code}"
        results = r.json().get("results") or []
        assert [x.get("status") for x in results] == [201, 200], f"statuses {[x.get('status') for x in results]}"
        assert len({get_id(x.get("submission")) for x in results}) == 1, "duplicate entries stored separately"
        assert len(vans("VAN-30")) == 1, "duplicate entries stored twice"
    s.check("batch_dedupes_within_batch", batch_dedupes_within_batch)

    # --- production data: tablets still holding the kitchen checklist as it was before it was edited
    kitchen = next((f for f in seed.get("forms", []) if f["key"] == "northwind_kitchen"), None)
    acme = next((f for f in seed.get("forms", []) if f["key"] == "acme_fire"), None)
    kitchen_late = sub("l.müller", "2026-11-20T06:40:00Z", {
        "kitchen_area": "cold store", "handwash_ok": True, "audit_date": "2026-11-20",
        "notes": "Late upload from depot tablet"})

    def seeded_late_upload_old_version():
        assert kitchen, "kitchen form was not seeded"
        body = created(post(kitchen["id"], {**kitchen_late, "formVersion": 1}), "late kitchen upload on version 1")
        got = fetch_sub(c, get_id(body))
        assert got.get("formVersion") == 1, f"formVersion {got.get('formVersion')!r}"
        assert_submission(got, kitchen_late)
        s.state["kitchen_late"] = get_id(body)
    s.check("seeded_late_upload_old_version", seeded_late_upload_old_version)

    def seeded_late_upload_rendered():
        assert_rendered(c, s.need("kitchen_late"), 1, SEED_FORMS["northwind_kitchen"][0]["fields"],
                        kitchen_late["answers"])
    s.check("seeded_late_upload_rendered", seeded_late_upload_rendered)

    def seeded_late_upload_filterable():
        sid = s.need("kitchen_late")
        got = {get_id(i) for i in list_subs(c, kitchen["id"], {"answers.notes[contains]": "depot tablet"})}
        assert sid in got, "late upload not found by a filter on a field only its version has"
    s.check("seeded_late_upload_filterable", seeded_late_upload_filterable)

    def seeded_old_shape_late_upload():
        assert acme, "Acme form was not seeded"
        payload = sub("t.sato", "2026-11-19T11:00:00Z", {
            "site_name": "Depot 7", "extinguisher_location": "Yard", "pressure_ok": False, "condition": "fair"})
        body = created(post(acme["id"], {**payload, "formVersion": 1}), "old-shape Acme upload on version 1")
        got = fetch_sub(c, get_id(body))
        assert got.get("formVersion") == 1, f"formVersion {got.get('formVersion')!r}"
        assert_submission(got, payload, converted(payload["answers"]))
    s.check("seeded_old_shape_late_upload", seeded_old_shape_late_upload)

    return s.results

"""Checks introduced by the `repeating_groups` phase (repeating groups)."""
from __future__ import annotations

from typing import Any

import httpx

from harness.suite import Suite, created, difference, get_id, rejected
from scenarios.inspections.checks.helpers import assert_filter, assert_submission, fetch_sub, list_subs, sub
from scenarios.inspections.common import FIRE_FIELDS, subset


GROUP_FORM = {"companyId": "harbor-marine", "name": "Vessel Safety Round", "fields": [
    {"key": "site_name", "label": "Vessel", "type": "text", "required": True},
    {"key": "extinguishers", "label": "Extinguishers", "type": "group", "minItems": 1, "maxItems": 3, "fields": [
        {"key": "location", "label": "Location", "type": "text", "required": True},
        {"key": "expiry", "label": "Expiry", "type": "date"},
        {"key": "pressure_ok", "label": "Pressure OK", "type": "boolean"},
    ]},
    {"key": "inspector_notes", "label": "Notes", "type": "text"},
]}

V1_SUBS = {
    "a": sub("e.lindqvist", "2026-10-01T08:00:00Z", {"site_name": "MV Nordlys", "extinguishers": [
        {"location": "Lobby", "expiry": "2027-03-01", "pressure_ok": True},
        {"location": "Kitchen", "expiry": "2026-11-15", "pressure_ok": False}]}),
    "b": sub("k.mensah", "2026-10-02T09:30:00Z", {"site_name": "MV Nordlys", "extinguishers": [
        {"location": "Lobby", "expiry": "2026-12-01", "pressure_ok": False}],
        "inspector_notes": "Replaced seal – see photo №2"}),
    "c": sub("r.haddad", "2026-10-03T10:00:00Z", {"site_name": "Sea Breeze", "extinguishers": [
        {"location": "Engine room", "expiry": "2027-01-10", "pressure_ok": True}]}),
    "d": sub("p.o'connor", "2026-10-04T11:45:00Z", {"site_name": "Sea Breeze", "extinguishers": [
        {"location": "Deck 2 – Bow", "pressure_ok": False},
        {"location": "Galley", "pressure_ok": True},
        {"location": "Lobby", "pressure_ok": True}]}),
}

V1_FILTERS = {
    "group_filter_any_item": ({"answers.extinguishers.pressure_ok": "false"}, {"a", "b", "d"}),
    "group_filter_same_item": ({"answers.extinguishers.pressure_ok": "false",
                                "answers.extinguishers.location": "Lobby"}, {"b"}),
    "group_filter_same_item_2": ({"answers.extinguishers.location": "Lobby",
                                  "answers.extinguishers.pressure_ok": "true"}, {"a", "d"}),
    "group_filter_date": ({"answers.extinguishers.expiry": "2027-01-10"}, {"c"}),
    "group_form_top_level_filter": ({"answers.site_name": "Sea Breeze"}, {"c", "d"}),
    "group_filter_mixed": ({"answers.site_name": "MV Nordlys",
                            "answers.extinguishers.pressure_ok": "false"}, {"a", "b"}),
}

V1_VALID = {"site_name": "Pier 3", "extinguishers": [{"location": "Bridge", "pressure_ok": True}]}
V1_INVALID = {
    "item_missing_required": {**V1_VALID, "extinguishers": [{"pressure_ok": True}]},
    "too_many_items": {**V1_VALID, "extinguishers": [{"location": f"L{i}"} for i in range(4)]},
    "too_few_items": {**V1_VALID, "extinguishers": []},
    "item_unknown_key": {**V1_VALID, "extinguishers": [{"location": "Bridge", "colour": "red"}]},
    "item_wrong_type": {**V1_VALID, "extinguishers": [{"location": "Bridge", "pressure_ok": "no"}]},
    "group_not_array": {**V1_VALID, "extinguishers": {"location": "Bridge"}},
}

OLD_TO_NEW = (("extinguisher_location", "location"), ("extinguisher_expiry", "expiry"), ("pressure_ok", "pressure_ok"))


def converted(answers: dict) -> dict:
    out = dict(answers)
    item = {new: out.pop(old) for old, new in OLD_TO_NEW if old in out}
    out["extinguishers"] = [item] if item else []
    return out


def any_ext(answers: dict, key: str, value: Any) -> bool:
    return any(isinstance(e, dict) and e.get(key) == value for e in answers.get("extinguishers") or [])


def v1(c: httpx.Client, seed: dict | None) -> dict:
    s = Suite("v1")

    def create_group_form():
        fid = get_id(created(c.post("/forms", json=GROUP_FORM), "POST /forms (group)"))
        s.state["form_id"] = fid
        r = c.get(f"/forms/{fid}")
        assert r.status_code == 200 and subset(GROUP_FORM, r.json()), \
            f"group form differs: {difference(GROUP_FORM, r.json()) if r.status_code == 200 else f'HTTP {r.status_code}'}"
    s.check("create_group_form", create_group_form)

    def group_submissions_read_back():
        fid = s.need("form_id")
        ids = {}
        for k, payload in V1_SUBS.items():
            ids[k] = get_id(created(c.post(f"/forms/{fid}/submissions", json=payload), f"POST group submission"))
        s.state["subs"] = ids
        for k, payload in V1_SUBS.items():
            assert_submission(fetch_sub(c, ids[k]), payload)
    s.check("group_submissions_read_back", group_submissions_read_back)

    for case, answers in V1_INVALID.items():
        def reject(answers=answers, case=case):
            r = c.post(f"/forms/{s.need('form_id')}/submissions", json=sub("t.sato", "2026-10-05T09:00:00Z", answers))
            rejected(r, case)
        s.check(f"rejects_{case}", reject)

    def group_invalid_not_stored():
        ids = s.need("subs")
        got = {get_id(i) for i in list_subs(c, s.need("form_id"))}
        assert got == set(ids.values()), f"expected {len(ids)} submissions on the form, found {len(got)}"
    s.check("group_invalid_not_stored", group_invalid_not_stored)

    for name, (params, keys) in V1_FILTERS.items():
        def filt(params=params, keys=keys):
            ids = s.need("subs")
            got = {get_id(i) for i in list_subs(c, s.need("form_id"), params)}
            want = {ids[k] for k in keys}
            assert got == want, f"{params}: got {len(got)} results, expected {len(want)} (exact set mismatch)"
        s.check(name, filt)

    # Acme's seeded "Fire Safety Check" must have been converted in place.
    acme = next((f for f in (seed or {}).get("forms", []) if f["key"] == "acme_fire"), None)
    acme_subs = [x for x in (seed or {}).get("submissions", []) if x["form"] == "acme_fire"]
    if acme:
        s.state["acme_id"] = acme["id"]

    def acme_form_converted():
        fid = s.need("acme_id")
        r = c.get(f"/forms/{fid}")
        assert r.status_code == 200, f"GET /forms/{fid}: HTTP {r.status_code}"
        fields = r.json().get("fields") or []
        keys = [f.get("key") for f in fields]
        assert keys == ["site_name", "extinguishers", "condition", "score"], f"field order {keys}"
        group = fields[1]
        assert group.get("type") == "group", f"extinguishers type {group.get('type')!r}"
        sub_fields = [(f.get("key"), f.get("type")) for f in group.get("fields") or []]
        assert sub_fields == [("location", "text"), ("expiry", "date"), ("pressure_ok", "boolean")], \
            f"subfields {sub_fields}"
        for want, got in ((FIRE_FIELDS[0], fields[0]), (FIRE_FIELDS[4], fields[2]), (FIRE_FIELDS[5], fields[3])):
            assert subset(want, got), f"unchanged field {want['key']} differs: {difference(want, got)}"
    s.check("acme_form_converted", acme_form_converted)

    def acme_submissions_converted():
        s.need("acme_id")
        assert acme_subs, "no seeded Acme submissions"
        bad = []
        for x in acme_subs:
            try:
                assert_submission(fetch_sub(c, x["id"]), x["payload"], converted(x["payload"]["answers"]))
            except AssertionError as e:
                bad.append(f"{x['id']}: {e}")
        assert not bad, f"{len(bad)}/{len(acme_subs)} not converted correctly, e.g. {bad[0][:300]}"
    s.check("acme_submissions_converted", acme_submissions_converted)

    def acme_old_key_filter_boolean():
        want = {x["id"] for x in acme_subs if x["payload"]["answers"].get("pressure_ok") is False}
        items = list_subs(c, s.need("acme_id"), {"answers.pressure_ok": "false"})
        assert_filter(items, want, lambda a: any_ext(a, "pressure_ok", False), "old key pressure_ok=false")
    s.check("acme_old_key_filter_boolean", acme_old_key_filter_boolean)

    def acme_old_key_filter_text():
        want = {x["id"] for x in acme_subs if x["payload"]["answers"].get("extinguisher_location") == "Lobby"}
        items = list_subs(c, s.need("acme_id"), {"answers.extinguisher_location": "Lobby"})
        assert_filter(items, want, lambda a: any_ext(a, "location", "Lobby"), "old key extinguisher_location=Lobby")
    s.check("acme_old_key_filter_text", acme_old_key_filter_text)

    def acme_new_path_filter():
        want = {x["id"] for x in acme_subs if x["payload"]["answers"].get("pressure_ok") is False}
        items = list_subs(c, s.need("acme_id"), {"answers.extinguishers.pressure_ok": "false"})
        assert_filter(items, want, lambda a: any_ext(a, "pressure_ok", False), "extinguishers.pressure_ok=false")
    s.check("acme_new_path_filter", acme_new_path_filter)

    old_full = sub("t.sato", "2026-10-06T09:30:00Z", {
        "site_name": "Depot 7", "extinguisher_location": "Stairwell B", "extinguisher_expiry": "2027-05-31",
        "pressure_ok": True, "condition": "good", "score": 8})
    old_partial = sub("k.mensah", "2026-10-06T10:10:00Z", {
        "site_name": "Depot 7", "pressure_ok": False, "condition": "poor", "score": 2})
    new_shape = sub("j.ramirez", "2026-10-06T11:00:00Z", {"site_name": "Head office", "extinguishers": [
        {"location": "Lobby", "expiry": "2027-08-01", "pressure_ok": True},
        {"location": "Server room", "pressure_ok": False}], "condition": "fair"})

    for name, payload, expected in (
        ("acme_accepts_old_shape", old_full, converted(old_full["answers"])),
        ("acme_accepts_old_shape_partial", old_partial, converted(old_partial["answers"])),
        ("acme_accepts_new_shape", new_shape, new_shape["answers"]),
    ):
        def post_and_read(payload=payload, expected=expected, name=name):
            fid = s.need("acme_id")
            sid = get_id(created(c.post(f"/forms/{fid}/submissions", json=payload), name))
            assert_submission(fetch_sub(c, sid), payload, expected)
        s.check(name, post_and_read)

    return s.results

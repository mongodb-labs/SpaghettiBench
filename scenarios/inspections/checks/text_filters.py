"""Checks introduced by the `text_filters` phase (improvement: case-insensitive text filters)."""
from __future__ import annotations

from typing import Callable

import httpx

from harness.suite import Suite, created, get_id
from scenarios.inspections.checks.helpers import assert_filter, fresh_company, list_subs, sub


CASE_FORM = {"name": "Harbour Patrol Log", "fields": [
    {"key": "site_name", "label": "Site", "type": "text", "required": True},
    {"key": "zone", "label": "Zone", "type": "select", "options": ["North", "South"]},
    {"key": "rooms", "label": "Rooms", "type": "group", "fields": [
        {"key": "room_name", "label": "Room", "type": "text", "required": True},
        {"key": "status", "label": "Status", "type": "select", "options": ["ok", "fail"]},
    ]},
]}
CASE_SUBS = {
    "t1": sub("t.sato", "2026-11-02T08:00:00Z", {"site_name": "Pier 7", "zone": "North", "rooms": [
        {"room_name": "Galley", "status": "fail"}, {"room_name": "Mess", "status": "ok"}]}),
    "t2": sub("t.sato", "2026-11-02T09:00:00Z", {"site_name": "PIER 7", "zone": "South", "rooms": [
        {"room_name": "galley", "status": "ok"}, {"room_name": "Mess", "status": "fail"}]}),
    "t3": sub("t.sato", "2026-11-02T10:00:00Z", {"site_name": "pier 70", "zone": "North", "rooms": []}),
}


def i1(c: httpx.Client, seed: dict | None) -> dict:
    s = Suite("i1")
    seed = seed or {}

    def create_fixtures():
        company = fresh_company(c, "pier-patrol")
        fid = get_id(created(c.post("/forms", json={**CASE_FORM, "companyId": company}), "POST /forms"))
        ids = {k: get_id(created(c.post(f"/forms/{fid}/submissions", json=p), "POST submission"))
               for k, p in CASE_SUBS.items()}
        s.state.update(form=fid, subs=ids)
    s.check("create_fixtures", create_fixtures)

    def filt(params: dict, keys: list[str]) -> Callable[[], None]:
        def fn():
            ids = s.need("subs")
            got = {get_id(i) for i in list_subs(c, s.need("form"), params)}
            want = {ids[k] for k in keys}
            assert got == want, f"{params}: got {len(got)} results, expected {len(want)} (exact set mismatch)"
        return fn
    s.check("text_filter_ignores_case", filt({"answers.site_name": "pier 7"}, ["t1", "t2"]))
    s.check("text_filter_exact_case_still_matches", filt({"answers.site_name": "Pier 7"}, ["t1", "t2"]))
    s.check("text_filter_whole_value", filt({"answers.site_name": "pier"}, []))
    s.check("text_filter_in_group", filt({"answers.rooms.room_name": "GALLEY"}, ["t1", "t2"]))
    s.check("text_filter_same_item", filt({"answers.rooms.room_name": "galley", "answers.rooms.status": "fail"}, ["t1"]))

    def select_unchanged():
        r = c.get(f"/forms/{s.need('form')}/submissions", params={"answers.zone": "north", "limit": 500})
        if 400 <= r.status_code < 500 and r.status_code != 404:
            return  # rejecting a value that isn't one of the options is as good as finding nothing
        filt({"answers.zone": "north"}, [])()
    s.check("select_filter_unchanged", select_unchanged)
    s.check("select_filter_exact_value", filt({"answers.zone": "North"}, ["t1", "t3"]))

    seeded = {f["key"]: f for f in seed.get("forms", [])}
    seeded_subs = seed.get("submissions", [])

    def seeded_text_filter():
        for form, key in (("brightpath_hvac", "unit_id"), ("acme_lighting", "building")):
            assert form in seeded, f"{form} was not seeded"
            values = [x["payload"]["answers"][key] for x in seeded_subs if x["form"] == form]
            query = values[0].swapcase()
            want = {x["id"] for x in seeded_subs
                    if x["form"] == form and x["payload"]["answers"][key].casefold() == query.casefold()}
            items = list_subs(c, seeded[form]["id"], {f"answers.{key}": query})
            assert_filter(items, want, lambda a: str(a.get(key, "")).casefold() == query.casefold(), f"{key}={query}")
    if seeded:
        s.check("seeded_text_filter_ignores_case", seeded_text_filter)

    def seeded_old_key_filter():
        assert "acme_fire" in seeded, "Acme form was not seeded"
        query = "LOADING BAY"
        want = {x["id"] for x in seeded_subs if x["form"] == "acme_fire"
                and str(x["payload"]["answers"].get("extinguisher_location", "")).casefold() == query.casefold()}
        items = list_subs(c, seeded["acme_fire"]["id"], {"answers.extinguisher_location": query})
        assert_filter(items, want, lambda a: any(str(e.get("location", "")).casefold() == query.casefold()
                                                 for e in a.get("extinguishers") or []),
                      f"extinguisher_location={query}")
    if seeded:
        s.check("seeded_old_key_filter_ignores_case", seeded_old_key_filter)

    return s.results

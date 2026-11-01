"""Checks introduced by the `custom_fields` phase (2: custom fields)."""
from __future__ import annotations

from typing import Callable

import httpx

from harness.suite import Suite, first_mismatches, get, listing, mk, ok, rejected, same_set, superset_matching
from scenarios.crm.checks.helpers import custom_equal, fresh_agency, person
from scenarios.crm.common import seniority


CONTACT_FIELDS = [
    {"entity": "contact", "key": "budget", "label": "Budget (EUR)", "type": "number"},
    {"entity": "contact", "key": "vip", "label": "VIP", "type": "boolean"},
    {"entity": "contact", "key": "source", "label": "Source", "type": "select", "options": ["referral", "website", "event"]},
    {"entity": "contact", "key": "birthday", "label": "Birthday", "type": "date"},
    {"entity": "contact", "key": "notes", "label": "Notes", "type": "text"},
]
DEAL_FIELDS = [
    {"entity": "deal", "key": "probability", "label": "Probability", "type": "number"},
    {"entity": "deal", "key": "channel", "label": "Channel", "type": "select", "options": ["direct", "portal"]},
    {"entity": "deal", "key": "notes", "label": "Notes", "type": "text"},
]
P1_CUSTOM = {"budget": 450000, "vip": True, "source": "referral", "birthday": "1985-04-12", "notes": "Prefers calls"}
P2_CUSTOM = {"budget": 450000.0, "vip": False, "source": "website"}


def v2(c: httpx.Client, seed: dict | None) -> dict:
    s = Suite("v2")
    st = s.state
    ag, bare = fresh_agency(c, "madrid-talent"), fresh_agency(c, "bare-agency")

    def define():
        for f in CONTACT_FIELDS + DEAL_FIELDS:
            ok(c.post(f"/agencies/{ag}/fields", json=f), f"define {f['entity']}.{f['key']}")
        st["defined"] = True
    s.check("define_fields", define)

    def list_fields():
        s.need("defined")
        got = {(f.get("entity"), f.get("key")) for f in listing(c, f"/agencies/{ag}/fields")}
        assert got == {(f["entity"], f["key"]) for f in CONTACT_FIELDS + DEAL_FIELDS}, f"definitions {sorted(got)}"
        deal_only = {f.get("key") for f in listing(c, f"/agencies/{ag}/fields", {"entity": "deal"})}
        assert deal_only == {"probability", "channel", "notes"}, f"?entity=deal gave {deal_only}"
    s.check("list_fields", list_fields)

    s.check("rejects_duplicate_key", lambda: rejected(c.post(f"/agencies/{ag}/fields", json=CONTACT_FIELDS[0]), "duplicate"))
    bad_defs = {
        "unknown_type": {"entity": "contact", "key": "shoe", "label": "Shoe", "type": "currency"},
        "select_without_options": {"entity": "contact", "key": "tier", "label": "Tier", "type": "select"},
        "select_with_empty_options": {"entity": "contact", "key": "tier", "label": "Tier", "type": "select", "options": []},
        "invalid_key": {"entity": "contact", "key": "bad key!", "label": "Bad", "type": "text"},
        "unknown_entity": {"entity": "company", "key": "size", "label": "Size", "type": "number"},
    }
    for name, body in bad_defs.items():
        s.check(f"rejects_definition_{name}", lambda body=body, name=name: rejected(
            c.post(f"/agencies/{ag}/fields", json=body), name))

    def contacts():
        s.need("defined")
        st["p1"] = mk(c, "/contacts", person(ag, "Lucía", "Méndez", custom=P1_CUSTOM), "contact with custom values")
        st["p2"] = mk(c, "/contacts", person(ag, "Pablo", "Méndez", custom=P2_CUSTOM), "contact with custom values")
        st["p3"] = mk(c, "/contacts", person(ag, "Inés", "Ruiz"), "contact without custom values")
    s.check("create_contacts_with_custom", contacts)

    def read_back():
        got = get(c, f"/contacts/{s.need('p1')}")
        assert custom_equal(P1_CUSTOM, got.get("custom")), f"custom {got.get('custom')}"
    s.check("custom_read_back", read_back)

    def empty():
        got = get(c, f"/contacts/{s.need('p3')}")
        assert got.get("custom") == {}, f"custom {got.get('custom')!r}"
    s.check("custom_empty_when_none", empty)

    invalid = {
        "undefined_key": {"shoe_size": 42},
        "wrong_type_number": {"budget": "450k"},
        "wrong_type_boolean": {"vip": "yes"},
        "option_not_allowed": {"source": "tv"},
        "invalid_date": {"birthday": "1985-02-30"},
        "deal_field_on_contact": {"probability": 0.5},
    }
    for name, custom in invalid.items():
        s.check(f"rejects_custom_{name}", lambda custom=custom, name=name: rejected(
            c.post("/contacts", json=person(ag, "Rex", "Rejected", custom=custom)), name))
    s.check("rejects_field_of_other_agency", lambda: rejected(
        c.post("/contacts", json=person(bare, "Rex", "Rejected", custom={"budget": 1})), "other agency's field"))

    def not_stored():
        same_set(listing(c, "/contacts", {"agencyId": ag}), {s.need("p1"), s.need("p2"), s.need("p3")}, "after rejections")
    s.check("invalid_custom_not_stored", not_stored)

    def cfilter(params: dict, keys: list[str]) -> Callable[[], None]:
        return lambda: same_set(listing(c, "/contacts", {"agencyId": ag, **params}), {s.need(k) for k in keys}, str(params))
    s.check("filter_custom_number", cfilter({"custom.budget": "450000"}, ["p1", "p2"]))
    s.check("filter_custom_boolean", cfilter({"custom.vip": "false"}, ["p2"]))
    s.check("filter_custom_select", cfilter({"custom.source": "referral"}, ["p1"]))
    s.check("filter_custom_date", cfilter({"custom.birthday": "1985-04-12"}, ["p1"]))
    s.check("filter_custom_text", cfilter({"custom.notes": "Prefers calls"}, ["p1"]))
    s.check("filter_custom_with_last_name", cfilter({"lastName": "Méndez", "custom.vip": "true"}, ["p1"]))
    s.check("rejects_filter_on_undefined_key", lambda: rejected(
        c.get("/contacts", params={"agencyId": ag, "custom.shoe_size": "42"}), "undefined filter key"))

    def patch_merges():
        p2 = s.need("p2")
        ok(c.patch(f"/contacts/{p2}", json={"custom": {"vip": True}}), "PATCH custom")
        got = get(c, f"/contacts/{p2}")
        assert custom_equal({**P2_CUSTOM, "vip": True}, got.get("custom")), f"custom {got.get('custom')}"
    s.check("patch_custom_sets_given_keys", patch_merges)

    def patch_null():
        p1 = s.need("p1")
        ok(c.patch(f"/contacts/{p1}", json={"custom": {"notes": None}}), "PATCH custom null")
        got = get(c, f"/contacts/{p1}")
        want = {k: v for k, v in P1_CUSTOM.items() if k != "notes"}
        assert custom_equal(want, got.get("custom")), f"custom {got.get('custom')}"
    s.check("patch_custom_null_removes", patch_null)

    def deals():
        st["d1"] = mk(c, "/deals", {"agencyId": ag, "contactId": s.need("p1"), "title": "Placement", "value": 9000,
                                    "stage": "proposal", "custom": {"probability": 0.4, "channel": "portal", "notes": "Call back"}},
                      "deal with custom values")
        st["d2"] = mk(c, "/deals", {"agencyId": ag, "contactId": s.need("p2"), "title": "Retainer", "value": 3000,
                                    "stage": "lead", "custom": {"channel": "direct"}}, "deal with custom values")
        got = get(c, f"/deals/{st['d1']}")
        assert custom_equal({"probability": 0.4, "channel": "portal", "notes": "Call back"}, got.get("custom")), \
            f"custom {got.get('custom')}"
    s.check("deal_custom_read_back", deals)

    s.check("deal_filter_custom_select", lambda: same_set(
        listing(c, "/deals", {"agencyId": ag, "custom.channel": "portal"}), {s.need("d1")}, "custom.channel"))
    s.check("deal_filter_custom_number", lambda: same_set(
        listing(c, "/deals", {"agencyId": ag, "custom.probability": "0.4"}), {s.need("d1")}, "custom.probability"))
    s.check("rejects_contact_field_on_deal", lambda: rejected(c.post("/deals", json={
        "agencyId": ag, "contactId": s.need("p1"), "title": "Rejected", "value": 1, "stage": "lead",
        "custom": {"budget": 1}}), "contact field on a deal"))

    if seed:
        tb = [x for x in seed["contacts"] if x["agency"] == "talentbridge"]

        def definition():
            defs = [f for f in listing(c, "/agencies/talentbridge/fields")
                    if f.get("key") == "seniority" and f.get("entity") == "contact"]
            assert len(defs) == 1, f"{len(defs)} seniority definitions"
            assert defs[0].get("type") == "select", f"type {defs[0].get('type')!r}"
            assert sorted(defs[0].get("options") or []) == ["junior", "lead", "mid", "senior"], f"options {defs[0].get('options')}"
        s.check("seniority_field_defined", definition)

        def backfilled():
            first_mismatches(tb, lambda x: get(c, f"/contacts/{x['id']}"), lambda x, got: (
                (got.get("custom") or {}).get("seniority") != seniority(x["payload"].get("title"))
                and f"title {x['payload'].get('title')!r} -> {(got.get('custom') or {}).get('seniority')!r}"))
        s.check("seniority_backfilled", backfilled)

        def seniority_filter():
            for value in ("lead", "senior", "junior"):
                expected = {x["id"] for x in tb if seniority(x["payload"].get("title")) == value}
                items = listing(c, "/contacts", {"agencyId": "talentbridge", "custom.seniority": value})
                superset_matching(items, expected, lambda i: (i.get("custom") or {}).get("seniority") == value,
                                  f"custom.seniority={value}")
        s.check("seniority_filter", seniority_filter)

    return s.results

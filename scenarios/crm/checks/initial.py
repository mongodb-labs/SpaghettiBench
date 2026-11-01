"""Checks introduced by the `initial` phase (initial build)."""
from __future__ import annotations

from typing import Any, Callable

import httpx

from harness.suite import Suite, expect, first_mismatches, get, listing, mk, ok, rejected, same_set, superset_matching
from scenarios.crm.checks.helpers import RENAMED, addresses, fresh_agency, person, seed_contacts, seed_deals
from scenarios.crm.common import email, fields_match


def v0(c: httpx.Client, seed: dict | None, exclude: frozenset = frozenset()) -> dict:
    s = Suite("v0")
    st = s.state
    ag, other = fresh_agency(c, "lisbon-homes"), fresh_agency(c, "kestrel-talent")

    def companies():
        st["sol"] = mk(c, "/companies", {"agencyId": ag, "name": "Sol Properties", "domain": "solproperties.es"}, "company")
        st["costa"] = mk(c, "/companies", {"agencyId": ag, "name": "Costa Homes"}, "company")
        st["foreign"] = mk(c, "/companies", {"agencyId": other, "name": "Foreign Partners"}, "company")
    s.check("create_companies", companies)

    def get_company():
        got = get(c, f"/companies/{s.need('sol')}")
        bad = fields_match({"agencyId": ag, "name": "Sol Properties", "domain": "solproperties.es"}, got)
        assert not bad, f"differs in {bad}: {got}"
    s.check("get_company", get_company)

    def list_companies():
        same_set(listing(c, "/companies", {"agencyId": ag}), {s.need("sol"), s.need("costa")}, "own agency")
        same_set(listing(c, "/companies", {"agencyId": other}), {s.need("foreign")}, "other agency")
    s.check("list_companies_by_agency", list_companies)

    def company_rejects():
        rejected(c.post("/companies", json={"agencyId": ag}), "no name")
        rejected(c.post("/companies", json={"agencyId": ag, "name": ""}), "empty name")
        assert len(listing(c, "/companies", {"agencyId": ag})) == 2, "a rejected company was stored"
    s.check("rejects_company_without_name", company_rejects)

    def contacts():
        sol, costa = s.need("sol"), s.need("costa")
        st["cp"] = {
            "maria": person(ag, "María", "García", email="Maria.Garcia@SolProperties.es", phone="+34 600 111 222",
                            title="Head of Acquisitions", companyId=sol),
            "tom": person(ag, "Tom", "O'Brien", email="tom@costahomes.pt", companyId=costa),
            "li": person(ag, "李", "娜", phone="+86 138 0000 0000"),
            "tomas": person(ag, "Tomás", "O'Brien", email="tomas@example.com", title="Broker", companyId=sol),
        }
        st["ct"] = {k: mk(c, "/contacts", p, f"contact {k}") for k, p in st["cp"].items()}
        st["other_contact"] = mk(c, "/contacts", person(other, "Otto", "Other"), "other agency's contact")
    s.check("create_contacts", contacts)

    def contact_read_back():
        names = {s.need("sol"): "Sol Properties", s.need("costa"): "Costa Homes"}
        for k, p in s.need("cp").items():
            cid = s.need("ct")[k]
            got = get(c, f"/contacts/{cid}")
            bad = fields_match(p, got)
            assert not bad, f"contact {cid}: differs in {bad}"
            assert got.get("companyName") == names.get(p.get("companyId")), \
                f"contact {cid}: companyName {got.get('companyName')!r}"
    s.check("contact_read_back", contact_read_back)

    invalid = {
        "missing_first_name": lambda: {"agencyId": ag, "lastName": "Rejected"},
        "empty_last_name": lambda: person(ag, "Rex", ""),
        "invalid_email": lambda: person(ag, "Rex", "Rejected", email="rex.rejected.example.com"),
        "email_with_two_at_signs": lambda: person(ag, "Rex", "Rejected", email="rex@rejected@example.com"),
        "wrong_type_name": lambda: person(ag, 42, "Rejected"),
        "company_of_other_agency": lambda: person(ag, "Rex", "Rejected", companyId=s.need("foreign")),
    }
    for name, body in invalid.items():
        s.check(f"rejects_{name}", lambda body=body, name=name: rejected(c.post("/contacts", json=body()), name))
    s.check("rejects_unknown_company", lambda: rejected(c.post("/contacts", json=person(
        ag, "Rex", "Rejected", companyId="ffffffffffffffffffffffff")), "unknown company", allow_404=True))

    def contacts_not_stored():
        same_set(listing(c, "/contacts", {"agencyId": ag}), set(s.need("ct").values()), "contacts after rejections")
    s.check("invalid_contacts_not_stored", contacts_not_stored)

    def contact_filter(params: Callable[[], dict], keys: list[str]) -> Callable[[], None]:
        def fn():
            ct = s.need("ct")
            same_set(listing(c, "/contacts", {"agencyId": ag, **params()}), {ct[k] for k in keys}, str(params()))
        return fn
    s.check("filter_email_case_insensitive", contact_filter(lambda: {"email": "maria.garcia@solproperties.es"}, ["maria"]))
    s.check("filter_last_name", contact_filter(lambda: {"lastName": "O'Brien"}, ["tom", "tomas"]))
    s.check("filter_unicode_name", contact_filter(lambda: {"lastName": "娜"}, ["li"]))
    s.check("filter_company", contact_filter(lambda: {"companyId": s.need("sol")}, ["maria", "tomas"]))
    s.check("filter_and", contact_filter(lambda: {"lastName": "O'Brien", "companyId": s.need("sol")}, ["tomas"]))
    s.check("limit_param", lambda: expect(len(listing(c, "/contacts", {"agencyId": ag}, limit=2)) == 2,
                                          lambda: "limit=2 did not return 2 contacts"))

    def agency_isolation():
        same_set(listing(c, "/contacts", {"agencyId": other}), {s.need("other_contact")}, "other agency's contacts")
    s.check("lists_only_own_agency", agency_isolation)

    def patch_partial():
        tid = s.need("ct")["tomas"]
        ok(c.patch(f"/contacts/{tid}", json={"title": "Senior Broker"}), "PATCH")
        bad = fields_match({**s.need("cp")["tomas"], "title": "Senior Broker"}, get(c, f"/contacts/{tid}"))
        assert not bad, f"differs in {bad}"
    s.check("patch_contact_partial", patch_partial)

    def patch_null():
        tid = s.need("ct")["tomas"]
        ok(c.patch(f"/contacts/{tid}", json={"title": None}), "PATCH")
        got = get(c, f"/contacts/{tid}")
        assert got.get("title") is None, f"title {got.get('title')!r} not cleared"
        assert email(got.get("email")) == "tomas@example.com", "email changed"
    s.check("patch_null_clears_field", patch_null)

    def patch_invalid():
        tid = s.need("ct")["tomas"]
        rejected(c.patch(f"/contacts/{tid}", json={"email": "broken"}), "PATCH invalid email")
        assert email(get(c, f"/contacts/{tid}").get("email")) == "tomas@example.com", "invalid PATCH changed the contact"
    s.check("patch_rejects_invalid", patch_invalid)

    def deals():
        ct = s.need("ct")
        st["dp"] = {
            "d1": {"agencyId": ag, "contactId": ct["maria"], "title": "Harbour View apartments", "value": 250000,
                   "stage": "proposal", "closeDate": "2026-11-30"},
            "d2": {"agencyId": ag, "contactId": ct["maria"], "title": "Old Town loft", "value": 180000.5,
                   "stage": "won", "closeDate": "2026-09-15"},
            "d3": {"agencyId": ag, "contactId": ct["tom"], "title": "Costa retainer", "value": 12000, "stage": "lead"},
            "d4": {"agencyId": ag, "contactId": ct["li"], "title": "Shanghai referral", "value": 0, "stage": "lost",
                   "closeDate": "2026-08-01"},
        }
        st["dl"] = {k: mk(c, "/deals", p, f"deal {k}") for k, p in st["dp"].items()}
    s.check("create_deals", deals)

    def deal_read_back():
        sol, costa = s.need("sol"), s.need("costa")
        want = {"d1": ("María García", sol, "Sol Properties"), "d2": ("María García", sol, "Sol Properties"),
                "d3": ("Tom O'Brien", costa, "Costa Homes"), "d4": ("李 娜", None, None)}
        for k, p in s.need("dp").items():
            did = s.need("dl")[k]
            got = get(c, f"/deals/{did}")
            bad = fields_match(p, got)
            assert not bad, f"deal {did}: differs in {bad}"
            names = (got.get("contactName"), got.get("companyId"), got.get("companyName"))
            assert names == want[k], f"deal {did}: contactName/companyId/companyName {names} != {want[k]}"
    s.check("deal_read_back", deal_read_back)

    def base_deal() -> dict:
        return {"agencyId": ag, "contactId": s.need("ct")["tom"], "title": "Rejected deal", "value": 100, "stage": "lead"}
    deal_invalid = {
        "negative_value": lambda: {**base_deal(), "value": -1},
        "string_value": lambda: {**base_deal(), "value": "100"},
        "unknown_stage": lambda: {**base_deal(), "stage": "closed"},
        "invalid_close_date": lambda: {**base_deal(), "closeDate": "2026-02-30"},
        "missing_title": lambda: {k: v for k, v in base_deal().items() if k != "title"},
        "contact_of_other_agency": lambda: {**base_deal(), "contactId": s.need("other_contact")},
    }
    for name, body in deal_invalid.items():
        s.check(f"rejects_deal_{name}", lambda body=body, name=name: rejected(c.post("/deals", json=body()), name))

    def deals_not_stored():
        same_set(listing(c, "/deals", {"agencyId": ag}), set(s.need("dl").values()), "deals after rejections")
    s.check("invalid_deals_not_stored", deals_not_stored)

    def deal_filter(params: Callable[[], dict], keys: list[str]) -> Callable[[], None]:
        def fn():
            dl = s.need("dl")
            same_set(listing(c, "/deals", {"agencyId": ag, **params()}), {dl[k] for k in keys}, str(params()))
        return fn
    s.check("deal_filter_stage", deal_filter(lambda: {"stage": "won"}, ["d2"]))
    s.check("deal_filter_contact", deal_filter(lambda: {"contactId": s.need("ct")["maria"]}, ["d1", "d2"]))
    s.check("deal_filter_company", deal_filter(lambda: {"companyId": s.need("sol")}, ["d1", "d2"]))

    def patch_deal():
        d3 = s.need("dl")["d3"]
        ok(c.patch(f"/deals/{d3}", json={"stage": "qualified", "value": 15000}), "PATCH deal")
        bad = fields_match({**s.need("dp")["d3"], "stage": "qualified", "value": 15000}, get(c, f"/deals/{d3}"))
        assert not bad, f"differs in {bad}"
        same_set(listing(c, "/deals", {"agencyId": ag, "stage": "qualified", "companyId": s.need("costa")}), {d3},
                 "stage + company filter after PATCH")
    s.check("patch_deal", patch_deal)

    def names_follow_contact():
        ok(c.patch(f"/contacts/{s.need('ct')['maria']}", json={"lastName": "García López"}), "PATCH contact")
        for k in ("d1", "d2"):
            got = get(c, f"/deals/{s.need('dl')[k]}").get("contactName")
            assert got == "María García López", \
                f"deal {s.need('dl')[k]}: contactName {got!r} after the contact was renamed"
    s.check("deal_shows_current_contact_name", names_follow_contact)

    def company_follows_contact():
        ok(c.patch(f"/contacts/{s.need('ct')['tom']}", json={"companyId": s.need("sol")}), "PATCH contact")
        got = get(c, f"/deals/{s.need('dl')['d3']}")
        assert (got.get("companyId"), got.get("companyName")) == (s.need("sol"), "Sol Properties"), \
            f"deal company {got.get('companyId')!r}/{got.get('companyName')!r} after the contact moved"
        dl = s.need("dl")
        same_set(listing(c, "/deals", {"agencyId": ag, "companyId": s.need("sol")}), {dl["d1"], dl["d2"], dl["d3"]},
                 "company filter after the contact moved")
    s.check("deal_follows_contact_company", company_follows_contact)

    if seed:
        company_names: dict[str, str] = {}

        def seeded_companies():
            bad = []
            for co in seed["companies"]:
                got = get(c, f"/companies/{co['id']}")
                want = dict(co["payload"])
                if want["name"] == RENAMED[0] and got.get("name") in RENAMED:
                    want["name"] = got["name"]  # the company-roles phase renames this one
                if fields_match(want, got):
                    bad.append(f"company {co['id']}: {fields_match(want, got)}")
                company_names[co["id"]] = got.get("name")
            assert not bad, f"{len(bad)} seeded companies differ, e.g. {bad[:3]}"
        s.check("seeded_companies_intact", seeded_companies)

        def seeded_contacts():
            def problem(x: dict, got: dict) -> Any:
                want_company = company_names.get(x["payload"].get("companyId"))
                return fields_match(x["payload"], got) or (
                    got.get("companyName") != want_company and f"companyName {got.get('companyName')!r}")
            first_mismatches(seed_contacts(seed, exclude), lambda x: get(c, f"/contacts/{x['id']}"), problem)
        s.check("seeded_contacts_intact", seeded_contacts)

        def seeded_deals():
            first_mismatches(seed_deals(seed, exclude), lambda d: get(c, f"/deals/{d['id']}"),
                             lambda d, got: fields_match(d["payload"], got))
        s.check("seeded_deals_intact", seeded_deals)

        def seeded_filter_email():
            picked = [x for x in seed_contacts(seed, exclude) if x["payload"].get("email")][:8]
            for x in picked:
                addr = x["payload"]["email"]
                items = listing(c, "/contacts", {"agencyId": x["agency"], "email": addr.upper()})
                superset_matching(items, {x["id"]}, lambda i: email(addr) in addresses(i), f"email={addr.upper()}")
        s.check("seeded_filter_email", seeded_filter_email)

        def seeded_filter_deals_by_company():
            contact_company = {x["id"]: x["payload"].get("companyId") for x in seed["contacts"]}
            for co in seed["companies"][:3]:
                expected = {d["id"] for d in seed_deals(seed, exclude)
                            if contact_company.get(d["payload"]["contactId"]) == co["id"]}
                items = listing(c, "/deals", {"agencyId": co["agency"], "companyId": co["id"]})
                superset_matching(items, expected, lambda i: i.get("companyId") == co["id"], f"companyId={co['id']}")
        s.check("seeded_filter_deals_by_company", seeded_filter_deals_by_company)

    return s.results

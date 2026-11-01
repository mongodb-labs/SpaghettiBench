"""Checks introduced by the `company_roles` phase (6: several companies, renames)."""
from __future__ import annotations

import httpx

from harness.suite import (Suite, expect, first_mismatches, get, get_id, listing, mk, ok, rejected, same_set,
                           superset_matching)
from scenarios.crm.checks.helpers import RENAMED, company_list, fresh_agency, person, seed_contacts, seed_deals
from scenarios.crm.checks.pipeline_reports import got_report


def v6(c: httpx.Client, seed: dict | None, exclude: frozenset = frozenset()) -> dict:
    s = Suite("v6")
    st = s.state
    ag, other = fresh_agency(c, "riviera-brokers"), fresh_agency(c, "fjord-brokers")

    def contact_of(key: str) -> dict:
        return get(c, f"/contacts/{s.need(key)}")

    def fixtures():
        a = mk(c, "/companies", {"agencyId": ag, "name": "Alpha Holdings"}, "company")
        b = mk(c, "/companies", {"agencyId": ag, "name": "Beta Build"}, "company")
        cc = mk(c, "/companies", {"agencyId": ag, "name": "Coast Capital"}, "company")
        foreign = mk(c, "/companies", {"agencyId": other, "name": "Foreign Firm"}, "company")
        st.update(a=a, b=b, c=cc, foreign=foreign)
        st["r1"] = mk(c, "/contacts", person(ag, "Rui", "One", companies=[{"companyId": a, "role": "Board member"},
                                                                          {"companyId": b}]), "contact")
        st["r2"] = mk(c, "/contacts", person(ag, "Rita", "Two", companyId=b), "old-shape contact")
        st["r3"] = mk(c, "/contacts", person(ag, "Raul", "Three"), "contact")
        st["r4"] = mk(c, "/contacts", person(ag, "Rosa", "Four", companyId=a), "contact")
        st["deal1"] = mk(c, "/deals", {"agencyId": ag, "contactId": st["r1"], "title": "Board deal", "value": 1000,
                                       "stage": "won", "closeDate": "2026-12-01"}, "deal")
        st["deal4"] = mk(c, "/deals", {"agencyId": ag, "contactId": st["r4"], "title": "Alpha deal", "value": 500,
                                       "stage": "lead"}, "deal")
    s.check("create_fixtures", fixtures)

    def read_back():
        got = contact_of("r1")
        a, b = s.need("a"), s.need("b")
        assert company_list(got) == [(a, "Board member"), (b, None)], f"companies {got.get('companies')}"
        names = [x.get("companyName") for x in got.get("companies") or []]
        assert names == ["Alpha Holdings", "Beta Build"], f"company names {names}"
        assert (got.get("companyId"), got.get("companyName")) == (a, "Alpha Holdings"), "companyId/companyName not the primary"
    s.check("companies_read_back", read_back)

    s.check("old_shape_company", lambda: expect(company_list(contact_of("r2")) == [(s.need("b"), None)],
                                                lambda: f"companies {contact_of('r2').get('companies')}"))
    s.check("companies_empty_when_none", lambda: expect(contact_of("r3").get("companies") == [],
                                                        lambda: f"companies {contact_of('r3').get('companies')!r}"))

    invalid = {
        "same_company_twice": lambda: {"companies": [{"companyId": s.need("a")}, {"companyId": s.need("a"), "role": "x"}]},
        "company_of_other_agency": lambda: {"companies": [{"companyId": s.need("foreign")}]},
        "company_id_and_companies": lambda: {"companyId": s.need("a"), "companies": [{"companyId": s.need("b")}]},
    }
    for name, extra in invalid.items():
        s.check(f"rejects_{name}", lambda extra=extra, name=name: rejected(
            c.post("/contacts", json=person(ag, "Rex", "Rejected", **extra())), name))

    def company_contacts():
        a, b = s.need("a"), s.need("b")
        items = listing(c, f"/companies/{a}/contacts")
        same_set(items, {s.need("r1"), s.need("r4")}, "contacts of A")
        roles = {get_id(i): i.get("role") for i in items}
        assert roles[s.need("r1")] == "Board member" and roles[s.need("r4")] is None, f"roles {roles}"
        same_set(listing(c, f"/companies/{b}/contacts"), {s.need("r1"), s.need("r2")}, "contacts of B")
    s.check("company_contacts_with_roles", company_contacts)

    s.check("filter_company_any_position", lambda: same_set(
        listing(c, "/contacts", {"agencyId": ag, "companyId": s.need("b")}), {s.need("r1"), s.need("r2")}, "companyId=B"))
    s.check("deal_uses_primary_company", lambda: expect(
        get(c, f"/deals/{s.need('deal1')}").get("companyId") == s.need("a"),
        lambda: "deal's company is not the contact's primary company"))

    def add_company():
        r3 = s.need("r3")
        ok(c.post(f"/contacts/{r3}/companies", json={"companyId": s.need("c"), "role": "Advisor"}), "add company")
        assert company_list(contact_of("r3")) == [(s.need("c"), "Advisor")], f"companies {contact_of('r3').get('companies')}"
        rejected(c.post(f"/contacts/{r3}/companies", json={"companyId": s.need("c")}), "add the same company again")
    s.check("add_company", add_company)

    def remove_company():
        r2, b = s.need("r2"), s.need("b")
        r = c.delete(f"/contacts/{r2}/companies/{b}")
        assert r.status_code in (200, 204), f"DELETE: HTTP {r.status_code}"
        assert contact_of("r2").get("companies") == [], f"companies {contact_of('r2').get('companies')}"
        rejected(c.delete(f"/contacts/{r2}/companies/{b}"), "remove again", allow_404=True)
    s.check("remove_company", remove_company)

    def patch_companies():
        r2, a, cc = s.need("r2"), s.need("a"), s.need("c")
        ok(c.patch(f"/contacts/{r2}", json={"companies": [{"companyId": cc, "role": "CFO"}, {"companyId": a}]}), "PATCH")
        got = contact_of("r2")
        assert company_list(got) == [(cc, "CFO"), (a, None)], f"companies {got.get('companies')}"
        assert got.get("companyId") == cc, "companyId is not the new primary"
    s.check("patch_companies_replaces", patch_companies)

    def patch_old():
        r2, b = s.need("r2"), s.need("b")
        ok(c.patch(f"/contacts/{r2}", json={"companyId": b}), "PATCH companyId")
        assert company_list(contact_of("r2")) == [(b, None)], f"companies {contact_of('r2').get('companies')}"
        ok(c.patch(f"/contacts/{r2}", json={"companyId": None}), "PATCH companyId null")
        assert contact_of("r2").get("companies") == [], f"companies {contact_of('r2').get('companies')}"
    s.check("patch_company_id_as_before", patch_old)

    def reorder():
        a, b = s.need("a"), s.need("b")
        ok(c.patch(f"/contacts/{s.need('r1')}", json={"companies": [{"companyId": b}, {"companyId": a, "role": "Board member"}]}),
           "PATCH")
        got = get(c, f"/deals/{s.need('deal1')}")
        assert (got.get("companyId"), got.get("companyName")) == (b, "Beta Build"), \
            f"deal company {got.get('companyId')!r}/{got.get('companyName')!r} after the primary changed"
    s.check("deal_follows_primary_company", reorder)

    def report_primary():
        rep = got_report(c.get("/reports/deals", params={"agencyId": ag, "groupBy": "companyId"}), ["companyId"])
        counts = {k[0]: v["count"] for k, v in rep.items()}
        assert counts == {s.need("b"): 1, s.need("a"): 1}, f"deals per company {counts}"
    s.check("report_uses_primary_company", report_primary)

    def rename():
        a = s.need("a")
        ok(c.patch(f"/companies/{a}", json={"name": "Alpha Group"}), "PATCH company")
        assert get(c, f"/companies/{a}").get("name") == "Alpha Group", "company not renamed"
        assert "Alpha Group" in [co.get("name") for co in listing(c, "/companies", {"agencyId": ag})], "list shows the old name"
        r4 = contact_of("r4")
        assert r4.get("companyName") == "Alpha Group", f"primary companyName {r4.get('companyName')!r}"
        r1_names = {x.get("companyId"): x.get("companyName") for x in contact_of("r1").get("companies") or []}
        assert r1_names.get(a) == "Alpha Group", f"secondary companyName {r1_names.get(a)!r}"
        assert get(c, f"/deals/{s.need('deal4')}").get("companyName") == "Alpha Group", "deal shows the old name"
        roles = {get_id(i) for i in listing(c, f"/companies/{a}/contacts")}
        assert roles == {s.need("r1"), s.need("r4")}, "company contacts changed by the rename"
    s.check("rename_shows_everywhere", rename)

    def merge_companies():
        a, b = s.need("a"), s.need("b")
        m1 = mk(c, "/contacts", person(ag, "Mia", "Merge", companies=[{"companyId": a, "role": "CEO"}]), "contact")
        m2 = mk(c, "/contacts", person(ag, "Mia", "Merge", companies=[{"companyId": b, "role": "Advisor"},
                                                                     {"companyId": a, "role": "Chair"}]), "contact")
        ok(c.post(f"/contacts/{m1}/merge", json={"duplicateId": m2}), "merge")
        assert company_list(get(c, f"/contacts/{m1}")) == [(a, "CEO"), (b, "Advisor")], "merged companies"
    s.check("merge_combines_companies", merge_companies)

    if seed:
        def seeded_converted():
            first_mismatches(seed_contacts(seed, exclude), lambda x: get(c, f"/contacts/{x['id']}"), lambda x, got: (
                company_list(got) != ([(x["payload"]["companyId"], None)] if x["payload"].get("companyId") else [])
                and f"companies {got.get('companies')}"))
        s.check("seeded_contacts_converted", seeded_converted)

        def seeded_company_contacts():
            for co in seed["companies"][:4]:
                expected = {x["id"] for x in seed_contacts(seed, exclude) if x["payload"].get("companyId") == co["id"]}
                superset_matching(listing(c, f"/companies/{co['id']}/contacts"), expected,
                                  lambda i: co["id"] in {cid for cid, _ in company_list(i)}, f"contacts of company {co['id']}")
        s.check("seeded_company_contacts", seeded_company_contacts)

        def seeded_rename():
            co = next(x for x in seed["companies"] if x["payload"]["name"] == RENAMED[0])
            new = RENAMED[1] if get(c, f"/companies/{co['id']}").get("name") == RENAMED[0] else RENAMED[0]
            ok(c.patch(f"/companies/{co['id']}", json={"name": new}), "rename")
            people = [x for x in seed_contacts(seed, exclude) if x["payload"].get("companyId") == co["id"]]
            assert people, "no seeded contacts at this company"
            first_mismatches(people, lambda x: get(c, f"/contacts/{x['id']}"),
                             lambda x, got: got.get("companyName") != new and f"companyName {got.get('companyName')!r}")
            ids = {x["id"] for x in people}
            deals = [d for d in seed_deals(seed, exclude) if d["payload"]["contactId"] in ids]
            first_mismatches(deals, lambda d: get(c, f"/deals/{d['id']}"),
                             lambda d, got: got.get("companyName") != new and f"companyName {got.get('companyName')!r}")
        s.check("seeded_rename_shows_everywhere", seeded_rename)

    return s.results

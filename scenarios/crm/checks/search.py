"""Checks introduced by the `search` phase: one search over contacts, companies and deals."""
from __future__ import annotations

import httpx

from harness.suite import Suite, get, get_id, mk, ok, rejected
from scenarios.crm.checks.helpers import fresh_agency, person, seed_contacts


def v9(c: httpx.Client, seed: dict | None, exclude: frozenset = frozenset()) -> dict:
    s = Suite("v9")
    st = s.state
    ag = fresh_agency(c, "costa-search")

    def results(q: str, **params) -> list[dict]:
        body = get(c, "/search", {"agencyId": ag, "q": q, **params})
        got = body.get("results") if isinstance(body, dict) else None
        assert isinstance(got, list), f"search {q!r}: expected {{'results': [...]}}, got {str(body)[:200]}"
        return got

    def found(q: str) -> set[str]:
        return {get_id(r) for r in results(q, limit=100)}

    def fixtures():
        costa = mk(c, "/companies", {"agencyId": ag, "name": "Costa Apartments", "domain": "costa-homes.pt"}, "company")
        lind_co = mk(c, "/companies", {"agencyId": ag, "name": "Lindqvist Partners"}, "company")
        ids = {
            "joaquin": mk(c, "/contacts", person(ag, "Joaquín", "Ibáñez", title="Sales Director", companyId=costa,
                                                 emails=[{"address": "joaquin@costa-homes.pt", "label": "work"}]), "c"),
            "ana": mk(c, "/contacts", person(ag, "Ana", "Lindqvist", emails=[
                {"address": "ana.l@example.org", "label": "home"}]), "contact"),
            "bo": mk(c, "/contacts", person(ag, "Bo", "Berg", companyId=lind_co), "contact"),
        }
        deal = mk(c, "/deals", {"agencyId": ag, "contactId": ids["ana"], "title": "Seaside villa", "value": 1,
                                "stage": "lead"}, "deal")
        mk(c, "/activities", {"agencyId": ag, "type": "note", "contactId": ids["bo"], "at": "2027-03-01T10:00:00Z",
                              "text": "Wants a penthouse with a rooftop pool"}, "note")
        mk(c, "/activities", {"agencyId": ag, "type": "email", "contactId": ids["joaquin"], "direction": "in",
                              "at": "2027-03-01T11:00:00Z", "subject": "Marina berth"}, "email")
        st.update(ids=ids, costa=costa, lind_co=lind_co, deal=deal)
    s.check("create_fixtures", fixtures)

    def kinds():
        ids = s.need("ids")
        got = {get_id(r): r for r in results("lindqvist")}
        want = {ids["ana"]: ("contact", "Ana Lindqvist"), s.need("lind_co"): ("company", "Lindqvist Partners"),
                s.need("deal"): ("deal", "Seaside villa"), ids["bo"]: ("contact", "Bo Berg")}
        assert set(got) == set(want), f"'lindqvist' found {len(got)} records, expected {len(want)}"
        for rid, (kind, title) in want.items():
            assert (got[rid].get("kind"), got[rid].get("title")) == (kind, title), f"result {got[rid]}"
    s.check("finds_contacts_companies_and_deals", kinds)

    def ranking():
        ids = s.need("ids")
        order = [get_id(r) for r in results("Lindqvist")]
        names = {ids["ana"], s.need("lind_co")}
        assert set(order[:2]) == names, f"records named Lindqvist should come first, got {order}"
    s.check("names_rank_first", ranking)

    def words():
        ids = s.need("ids")
        assert ids["joaquin"] in found("joaquin director"), "two words, both matching"
        assert ids["joaquin"] not in found("joaquin lindqvist"), "every word must match: 'joaquin lindqvist'"
        assert found("berg penthouse") == {ids["bo"]}, f"'berg penthouse' found {found('berg penthouse')}"
    s.check("every_word_must_match", words)

    def accents():
        ids = s.need("ids")
        for q in ("ibanez", "IBÁÑEZ", "Joaquin Ibanez"):
            assert ids["joaquin"] in found(q), f"{q!r} doesn't find Joaquín Ibáñez"
    s.check("ignores_case_and_accents", accents)

    def forms():
        ids = s.need("ids")
        assert s.need("costa") in found("apartment"), "'apartment' doesn't find Costa Apartments"
        assert ids["joaquin"] in found("apartment"), "'apartment' doesn't find a contact through its company"
        assert s.need("deal") in found("villas"), "'villas' doesn't find 'Seaside villa'"
    s.check("matches_word_forms", forms)

    def related():
        ids = s.need("ids")
        assert ids["bo"] in found("rooftop"), "note text"
        assert ids["joaquin"] in found("marina"), "email subject"
        assert s.need("costa") in found("costa-homes.pt") or s.need("costa") in found("costa homes"), "company domain"
        assert found("ana.l@example.org") == {ids["ana"]}, f"email address found {found('ana.l@example.org')}"
        assert found("ANA.L@EXAMPLE.ORG") == {ids["ana"]}, "email address ignoring case"
    s.check("searches_related_text", related)

    def current():
        ids = s.need("ids")
        ok(c.patch(f"/companies/{s.need('lind_co')}", json={"name": "Nordic Advisors"}), "rename company")
        assert ids["bo"] in found("nordic"), "contact not found by its company's new name"
        assert ids["bo"] not in found("partners"), "contact still found by its company's old name"
        ok(c.patch(f"/contacts/{ids['ana']}", json={"lastName": "Holm"}), "rename contact")
        assert {ids["ana"], s.need("deal")} <= found("holm"), "contact and deal not found by the new name"
        new = mk(c, "/contacts", person(ag, "Zed", "Quorra"), "contact")
        assert found("quorra") == {new}, "new contact not found at once"
        dup = mk(c, "/contacts", person(ag, "Zed", "Quorra"), "duplicate")
        ok(c.post(f"/contacts/{new}/merge", json={"duplicateId": dup}), "merge")
        assert found("quorra") == {new}, "merged-away contact still found"
    s.check("reflects_current_data", current)

    def other():
        rejected(c.get("/search", params={"agencyId": ag}), "search without q")
        other_ag = fresh_agency(c, "costa-search-other")
        body = get(c, "/search", {"agencyId": other_ag, "q": "apartment"})
        assert not body.get("results"), "another agency's records found"
        assert len(results("apartment", limit=1)) == 1, "limit"
    s.check("isolation_limit_and_validation", other)

    if seed:
        def seeded():
            bad = []
            for x in seed_contacts(seed, exclude)[::9]:
                p = x["payload"]
                r = c.get("/search", params={"agencyId": x["agency"], "q": f"{p['firstName']} {p['lastName']}", "limit": 100})
                ids = {get_id(i) for i in (r.json().get("results") or [])} if r.status_code == 200 else set()
                if x["id"] not in ids:
                    bad.append(f"{p['firstName']} {p['lastName']}")
            assert not bad, f"{len(bad)} seeded contacts not found by name, e.g. {bad[:3]}"
        s.check("seeded_contacts_found", seeded)
    return s.results

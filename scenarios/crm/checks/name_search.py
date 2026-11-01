"""Checks introduced by the `name_search` phase (improvement: name search)."""
from __future__ import annotations

from typing import Callable

import httpx

from harness.suite import Suite, listing, mk, same_set, superset_matching
from scenarios.crm.checks.helpers import fresh_agency, person, seed_contacts
from scenarios.crm.common import fold


NAMES = {"garcia_accent": ("Lucía", "García"), "garcia": ("Luis", "Garcia"), "garcia_lower": ("Lola", "garcía"),
         "muller": ("Max", "Müller"), "mueller": ("Mia", "Mueller"), "nunez": ("Nora", "Nuñez"),
         "nunez_plain": ("Nils", "Nunez"), "obrien": ("Owen", "O'Brien")}
GARCIAS = ["garcia_accent", "garcia", "garcia_lower"]


def i1(c: httpx.Client, seed: dict | None, exclude: frozenset = frozenset()) -> dict:
    s = Suite("i1")
    st = s.state
    ag = fresh_agency(c, "sevilla-homes")

    def fixtures():
        st["co"] = mk(c, "/companies", {"agencyId": ag, "name": "Sol Properties"}, "company")
        st["ct"] = {k: mk(c, "/contacts", person(ag, first, last, **({"companyId": st["co"]} if k in GARCIAS[:2] else {})),
                          f"contact {last}") for k, (first, last) in NAMES.items()}
    s.check("create_contacts", fixtures)

    def by_name(query: str, keys: list[str], extra: dict | None = None) -> Callable[[], None]:
        def fn():
            ct = s.need("ct")
            same_set(listing(c, "/contacts", {"agencyId": ag, "lastName": query, **(extra or {})}),
                     {ct[k] for k in keys}, f"lastName={query}")
        return fn
    s.check("name_filter_ignores_case", by_name("o'BRIEN", ["obrien"]))
    s.check("name_filter_ignores_accents", by_name("garcia", GARCIAS))
    s.check("name_filter_accented_query", by_name("GARCÍA", GARCIAS))
    s.check("name_filter_tilde", by_name("Nunez", ["nunez", "nunez_plain"]))
    s.check("name_filter_different_letters_stay_different", by_name("muller", ["muller"]))
    s.check("name_filter_whole_name", by_name("garc", []))
    s.check("name_filter_exact_still_works", by_name("Mueller", ["mueller"]))
    s.check("name_filter_with_company", lambda: by_name("GARCIA", GARCIAS[:2], {"companyId": s.need("co")})())

    if seed:
        def seeded_names():
            harbor = [x for x in seed_contacts(seed, exclude) if x["agency"] == "harbor-realty"]
            for query in ("garcia", "MULLER", "fernandez", "novak"):
                expected = {x["id"] for x in harbor if fold(x["payload"]["lastName"]) == fold(query)}
                items = listing(c, "/contacts", {"agencyId": "harbor-realty", "lastName": query})
                superset_matching(items, expected, lambda i: fold(i.get("lastName")) == fold(query), f"lastName={query}")
        s.check("seeded_name_filter", seeded_names)

    return s.results

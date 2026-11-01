"""Checks introduced by the `open_pipeline` phase (improvement: open pipeline per contact)."""
from __future__ import annotations

import httpx

from harness.suite import Suite, get, get_id, listing, mk, ok
from scenarios.crm.checks.helpers import fresh_agency, person


OPEN_STAGES = {"lead", "qualified", "proposal"}


def open_pipeline(deals: list[dict], contact_id: str) -> float:
    return sum(d.get("value") or 0 for d in deals if d.get("contactId") == contact_id and d.get("stage") in OPEN_STAGES)


def i2(c: httpx.Client, seed: dict | None, exclude: frozenset = frozenset()) -> dict:
    s = Suite("i2")
    st = s.state
    ag = fresh_agency(c, "pipeline-partners")

    def fixtures():
        ids = {k: mk(c, "/contacts", person(ag, first, last), "contact")
               for k, (first, last) in {"a": ("Ada", "Zeller"), "b": ("Ben", "Young"), "c": ("Cy", "Young"),
                                         "d": ("Dee", "Adams")}.items()}
        deals = {}
        for key, contact, value, stage in (("a1", "a", 100, "lead"), ("a2", "a", 250.5, "proposal"), ("a3", "a", 1000, "won"),
                                           ("a4", "a", 400, "lost"), ("b1", "b", 300, "qualified"), ("c1", "c", 300, "lead")):
            deals[key] = mk(c, "/deals", {"agencyId": ag, "contactId": ids[contact], "title": f"Deal {key}",
                                         "value": value, "stage": stage}, "deal")
        st.update(ids=ids, deals=deals)
    s.check("create_fixtures", fixtures)

    def values(want: dict[str, float], items: list[dict] | None = None) -> None:
        ids = s.need("ids")
        got = {get_id(i): i.get("openPipeline") for i in items} if items is not None else \
            {cid: get(c, f"/contacts/{cid}").get("openPipeline") for cid in ids.values()}
        for k, v in want.items():
            g = got.get(ids[k])
            assert isinstance(g, (int, float)) and not isinstance(g, bool) and abs(g - v) < 0.01, \
                f"contact {ids[k]}: openPipeline {g!r}, expected {v}"

    s.check("open_pipeline_on_contact", lambda: values({"a": 350.5, "b": 300, "c": 300, "d": 0}))
    s.check("open_pipeline_in_list", lambda: values({"a": 350.5, "b": 300, "c": 300, "d": 0},
                                                    listing(c, "/contacts", {"agencyId": ag})))

    def order(params: dict, keys: list[str]) -> None:
        ids = s.need("ids")
        limit = params.pop("_limit", 500)
        got = [get_id(i) for i in listing(c, "/contacts", {"agencyId": ag, "sort": "openPipeline", **params}, limit=limit)]
        assert got == [ids[k] for k in keys], f"order {got}, expected {[ids[k] for k in keys]}"

    s.check("sort_by_open_pipeline", lambda: order({}, ["a", "b", "c", "d"]))
    s.check("sort_with_filter_and_limit", lambda: order({"lastName": "Young", "_limit": 1}, ["b"]))

    def follows_deals():
        ids, deals = s.need("ids"), s.need("deals")
        ok(c.patch(f"/deals/{deals['a1']}", json={"stage": "won"}), "PATCH deal stage")
        ok(c.patch(f"/deals/{deals['b1']}", json={"value": 50}), "PATCH deal value")
        mk(c, "/deals", {"agencyId": ag, "contactId": ids["d"], "title": "New deal", "value": 500, "stage": "lead"}, "deal")
        values({"a": 250.5, "b": 50, "c": 300, "d": 500})
        order({}, ["d", "c", "a", "b"])
    s.check("open_pipeline_follows_deals", follows_deals)

    if seed:
        def seeded_values():
            for agency in ("harbor-realty", "talentbridge"):
                deals = listing(c, "/deals", {"agencyId": agency})
                bad = [f"{get_id(x)}: {x.get('openPipeline')!r} != {open_pipeline(deals, get_id(x))}"
                       for x in listing(c, "/contacts", {"agencyId": agency})
                       if not isinstance(x.get("openPipeline"), (int, float))
                       or abs(x["openPipeline"] - open_pipeline(deals, get_id(x))) >= 0.01]
                assert not bad, f"{agency}: {len(bad)} contacts with a wrong openPipeline, e.g. {bad[:3]}"
        s.check("seeded_open_pipeline", seeded_values)

        def seeded_sort():
            got = [x.get("openPipeline") for x in listing(c, "/contacts", {"agencyId": "fete-events", "sort": "openPipeline"})]
            assert got and all(isinstance(v, (int, float)) for v in got), "no openPipeline values"
            assert got == sorted(got, reverse=True), "contacts not sorted by openPipeline, largest first"
        s.check("seeded_sort_by_open_pipeline", seeded_sort)

    return s.results

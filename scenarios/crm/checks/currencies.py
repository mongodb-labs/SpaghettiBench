"""Checks introduced by the `currencies` phase: deals in several currencies converted to euros."""
from __future__ import annotations

import datetime as dt

import httpx

from harness.suite import Suite, as_list, get, get_id, listing, mk, ok, rejected, same_set
from scenarios.crm.checks.helpers import fresh_agency, num, person, seed_deals, today


def day(offset: int) -> str:
    return (today() + dt.timedelta(days=offset)).isoformat()


def v10(c: httpx.Client, seed: dict | None, exclude: frozenset = frozenset()) -> dict:
    s = Suite("v10")
    st = s.state
    ag = fresh_agency(c, "global-brokers")

    def rates():
        for date, body in ((day(-400), {"USD": 1.25, "GBP": 0.8}), (day(-30), {"USD": 1.1}), (day(400), {"USD": 2.0})):
            ok(c.put(f"/agencies/{ag}/rates/{date}", json=body), f"PUT rates {date}")
        got = get(c, f"/agencies/{ag}/rates")
        assert isinstance(got, dict) and got.get(day(-30)) == {"USD": 1.1} and got.get(day(-400)) == {"USD": 1.25, "GBP": 0.8}, \
            f"rates {str(got)[:300]}"
    s.check("put_and_get_rates", rates)

    def bad_rates():
        for what, date, body in (("lower-case code", day(-5), {"usd": 1.1}), ("negative rate", day(-5), {"USD": -1}),
                                 ("text rate", day(-5), {"USD": "1.1"}), ("4-letter code", day(-5), {"USDT": 1})):
            rejected(c.put(f"/agencies/{ag}/rates/{date}", json=body), what)
        rejected(c.put(f"/agencies/{ag}/rates/2027-13-01", json={"USD": 1}), "invalid date", allow_404=True)
        assert day(-5) not in get(c, f"/agencies/{ag}/rates"), "a rejected rate was stored"
    s.check("rejects_invalid_rates", bad_rates)

    def fixtures():
        cid = mk(c, "/contacts", person(ag, "Uma", "Dollar"), "contact")
        other = mk(c, "/contacts", person(ag, "Gil", "Pound"), "contact")
        specs = {  # key: (contact, currency, value, stage, closeDate, expected valueEur)
            "early": (cid, "USD", 1100, "lead", day(-800), 880.0),
            "old": (cid, "USD", 1100, "qualified", day(-100), 880.0),
            "recent": (cid, "USD", 1100, "proposal", day(10), 1000.0),
            "future": (cid, "USD", 1100, "won", day(500), 550.0),
            "undated": (other, "USD", 1100, "lead", None, 1000.0),
            "pounds": (other, "GBP", 800, "lead", day(500), 1000.0),
            "euros": (other, None, 500, "won", None, 500.0),
        }
        deals = {}
        for k, (contact, cur, value, stage, close, _) in specs.items():
            body = {"agencyId": ag, "contactId": contact, "title": k, "value": value, "stage": stage}
            body.update({"currency": cur} if cur else {}, **({"closeDate": close} if close else {}))
            got = ok(c.post("/deals", json=body), f"create {k} deal")
            assert got.get("currency") == (cur or "EUR"), f"{k}: currency {got.get('currency')!r}"
            deals[k] = get_id(got)
        st.update(cid=cid, other=other, deals=deals, want={k: v[5] for k, v in specs.items()})
    s.check("create_fixtures", fixtures)

    def value_eur(want: dict[str, float]) -> None:
        deals = s.need("deals")
        listed = {get_id(d): d for d in listing(c, "/deals", {"agencyId": ag})}
        for k, v in want.items():
            num(get(c, f"/deals/{deals[k]}").get("valueEur"), v, f"{k}: valueEur")
            num(listed.get(deals[k], {}).get("valueEur"), v, f"{k} (list): valueEur")

    s.check("value_in_euros", lambda: value_eur(s.need("want")))

    def bad_deals():
        cid = s.need("cid")
        for cur in ("JPY", "usd", 7):
            rejected(c.post("/deals", json={"agencyId": ag, "contactId": cid, "title": "x", "value": 1, "stage": "lead",
                                            "currency": cur}), f"currency {cur!r}")
        rejected(c.patch(f"/deals/{s.need('deals')['euros']}", json={"currency": "JPY"}), "PATCH to a currency without rate")
        same_set(listing(c, "/deals", {"agencyId": ag, "currency": "GBP"}), {s.need("deals")["pounds"]}, "?currency=GBP")
    s.check("currency_validation_and_filter", bad_deals)

    def totals(want: dict[str, float]) -> None:
        cid, other = s.need("cid"), s.need("other")
        num(get(c, f"/contacts/{cid}").get("openPipeline"), want["cid"], "Uma's openPipeline", 0.05)
        num(get(c, f"/contacts/{other}").get("openPipeline"), want["other"], "Gil's openPipeline", 0.05)
        groups = as_list(get(c, "/reports/deals", {"agencyId": ag, "groupBy": "stage"}))
        by_stage = {g.get("stage"): g.get("value") for g in groups}
        for stage, v in want["report"].items():
            num(by_stage.get(stage), v, f"report value for {stage}", 0.05)

    s.check("totals_in_euros", lambda: totals({"cid": 880 + 880 + 1000, "other": 2000, "report": {
        "lead": 880 + 1000 + 1000, "qualified": 880, "proposal": 1000, "won": 1050}}))

    def segment():
        seg = ok(c.post(f"/agencies/{ag}/segments", json={"name": "Big", "filter": {
            "field": "openPipeline", "op": "gt", "value": 2500}}), "segment")
        same_set(listing(c, f"/segments/{get_id(seg)}/contacts"), {s.need("cid")}, "segment on openPipeline in euros")
    s.check("segments_use_euros", segment)

    def follows():
        ok(c.put(f"/agencies/{ag}/rates/{day(-30)}", json={"USD": 1.0}), "replace rate")
        value_eur({**s.need("want"), "recent": 1100.0, "undated": 1100.0})
        totals({"cid": 880 + 880 + 1100, "other": 2100, "report": {"lead": 880 + 1100 + 1000, "proposal": 1100}})
        ok(c.patch(f"/deals/{s.need('deals')['old']}", json={"closeDate": day(-10)}), "move close date")
        value_eur({"old": 1100.0})
    s.check("rate_changes_apply_everywhere", follows)

    if seed:
        def seeded():
            got = {get_id(d): d for agency in ("harbor-realty", "talentbridge", "fete-events")
                   for d in listing(c, "/deals", {"agencyId": agency})}
            bad = [f"{d['id']}: {got.get(d['id'], {}).get('currency')!r} {got.get(d['id'], {}).get('valueEur')!r}"
                   for d in seed_deals(seed, exclude)
                   if got.get(d["id"], {}).get("currency") != "EUR"
                   or got[d["id"]].get("valueEur") != got[d["id"]].get("value")]
            assert not bad, f"{len(bad)} seeded deals not in EUR with valueEur = value, e.g. {bad[:3]}"
        s.check("seeded_deals_in_euros", seeded)
    return s.results

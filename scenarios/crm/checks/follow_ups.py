"""Checks introduced by the `follow_ups` phase (improvement: follow-up dates on deals)."""
from __future__ import annotations

import datetime as dt

import httpx

from harness.suite import Suite, get, get_id, listing, mk, ok, rejected, same_set
from scenarios.crm.checks.helpers import fresh_agency, person, seed_deals, today

DAYS = {"lead": 2, "qualified": 5, "proposal": 3}


def default_for(stage: str, since: dt.date) -> set[str | None]:
    """Acceptable defaults: counted from `since` or any later day up to today (a run may cross midnight)."""
    if stage not in DAYS:
        return {None}
    return {(since + dt.timedelta(days=n + DAYS[stage])).isoformat() for n in range((today() - since).days + 1)}


def i3(c: httpx.Client, seed: dict | None, exclude: frozenset = frozenset()) -> dict:
    s = Suite("i3")
    st = s.state
    ag = fresh_agency(c, "follow-up-realty")
    start = today()

    def defaults():
        cid = mk(c, "/contacts", person(ag, "Fay", "Follow"), "contact")
        deals = {}
        for stage in ("lead", "qualified", "proposal", "won", "lost"):
            d = ok(c.post("/deals", json={"agencyId": ag, "contactId": cid, "title": stage, "value": 100, "stage": stage}),
                   f"create {stage} deal")
            assert d.get("followUpOn") in default_for(stage, start), \
                f"{stage}: followUpOn {d.get('followUpOn')!r}, expected {sorted(default_for(stage, start), key=str)}"
            assert d.get("followUpOverdue") is False, f"{stage}: followUpOverdue {d.get('followUpOverdue')!r}"
            deals[stage] = get_id(d)
        st.update(cid=cid, deals=deals)
    s.check("default_by_stage", defaults)

    def deal(key: str) -> dict:
        return get(c, f"/deals/{s.need('deals')[key]}")

    def changes():
        deals = s.need("deals")
        got = ok(c.patch(f"/deals/{deals['lead']}", json={"stage": "qualified"}), "move to qualified").get("followUpOn")
        assert got in default_for("qualified", start), f"after a stage change followUpOn {got!r}"
        ok(c.patch(f"/deals/{deals['lead']}", json={"followUpOn": "2031-01-01"}), "set followUpOn")
        ok(c.patch(f"/deals/{deals['lead']}", json={"value": 5}), "PATCH value")
        assert deal("lead").get("followUpOn") == "2031-01-01", "PATCH without a stage change reset followUpOn"
        got = ok(c.patch(f"/deals/{deals['qualified']}", json={"stage": "proposal", "followUpOn": "2031-02-02"}),
                 "stage change with an explicit date").get("followUpOn")
        assert got == "2031-02-02", f"explicit followUpOn should win over the default, got {got!r}"
        got = ok(c.patch(f"/deals/{deals['proposal']}", json={"stage": "won"}), "win deal").get("followUpOn")
        assert got is None, f"won deal followUpOn {got!r}"
        got = ok(c.post("/deals", json={"agencyId": ag, "contactId": s.need("cid"), "title": "Explicit", "value": 1,
                                        "stage": "lead", "followUpOn": "2030-05-05"}), "create with a date")
        assert got.get("followUpOn") == "2030-05-05", f"explicit followUpOn on create {got.get('followUpOn')!r}"
        st["explicit"] = get_id(got)
        ok(c.patch(f"/deals/{deals['qualified']}", json={"followUpOn": None}), "clear followUpOn")
        assert deal("qualified").get("followUpOn") is None, "followUpOn not cleared"
        rejected(c.patch(f"/deals/{deals['qualified']}", json={"followUpOn": "next week"}), "invalid date")
    s.check("changes_and_explicit_dates", changes)

    def overdue():
        cid = s.need("cid")
        late = mk(c, "/deals", {"agencyId": ag, "contactId": cid, "title": "Late", "value": 1, "stage": "lead",
                                "followUpOn": "2020-01-01"}, "late deal")
        assert get(c, f"/deals/{late}").get("followUpOverdue") is True, "past follow-up not overdue"
        same_set(listing(c, "/deals", {"agencyId": ag, "overdue": "true"}), {late}, "overdue=true")
        assert late not in {get_id(d) for d in listing(c, "/deals", {"agencyId": ag, "overdue": "false"})}, "overdue=false"
        same_set(listing(c, "/deals", {"agencyId": ag, "followUpBefore": "2031-01-01"}), {late, s.need("explicit")},
                 "followUpBefore")
        st["late"] = late
    s.check("overdue_flag_and_filters", overdue)

    def next_follow_up():
        cid = s.need("cid")
        open_dates = [d["followUpOn"] for d in listing(c, "/deals", {"agencyId": ag, "contactId": cid})
                      if d.get("stage") in DAYS and d.get("followUpOn")]
        got = get(c, f"/contacts/{cid}").get("nextFollowUpOn")
        assert got == min(open_dates) == "2020-01-01", f"nextFollowUpOn {got!r}, expected {min(open_dates)}"
        ok(c.patch(f"/deals/{s.need('late')}", json={"stage": "lost"}), "lose the late deal")
        got = get(c, f"/contacts/{cid}").get("nextFollowUpOn")
        assert got != "2020-01-01", "nextFollowUpOn still shows a lost deal's date"
        empty = mk(c, "/contacts", person(ag, "No", "Deals"), "contact")
        assert get(c, f"/contacts/{empty}").get("nextFollowUpOn") is None, "contact without deals"
    s.check("next_follow_up_on_contact", next_follow_up)

    if seed:
        def seeded():
            since = start - dt.timedelta(days=3)
            got = {get_id(d): d for agency in ("harbor-realty", "talentbridge", "fete-events")
                   for d in listing(c, "/deals", {"agencyId": agency})}
            bad = []
            for d in seed_deals(seed, exclude):
                g = got.get(d["id"], {})
                if g.get("stage") != d["payload"]["stage"]:
                    continue  # changed by an earlier check
                if g.get("followUpOn") not in default_for(d["payload"]["stage"], since):
                    bad.append(f"{d['id']} ({d['payload']['stage']}): {g.get('followUpOn')!r}")
            assert not bad, f"{len(bad)} seeded deals without the right follow-up, e.g. {bad[:3]}"
        s.check("seeded_deals_backfilled", seeded)
    return s.results

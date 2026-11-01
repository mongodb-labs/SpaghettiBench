"""Checks introduced by the `trash` phase: deleting contacts with everything they own, and restoring them."""
from __future__ import annotations

import httpx

from harness.suite import Suite, as_list, get, get_id, listing, mk, ok, status
from scenarios.crm.checks.helpers import fresh_agency, person, uid


def v13(c: httpx.Client, seed: dict | None) -> dict:
    s = Suite("v13")
    st = s.state
    ag = fresh_agency(c, "trash-realty")

    def fixtures():
        mail = f"gone.{uid()}@example.com"
        x = mk(c, "/contacts", person(ag, "Xena", "Gone", tags=["gone"], emails=[{"address": mail, "label": "work"}]), "x")
        y = mk(c, "/contacts", person(ag, "Yuri", "Stays"), "y")
        z = mk(c, "/contacts", person(ag, "Zoe", "Stays"), "z")
        deal = mk(c, "/deals", {"agencyId": ag, "contactId": x, "title": "Penthouse Gone", "value": 700, "stage": "lead"},
                  "deal")
        call = mk(c, "/activities", {"agencyId": ag, "type": "call", "contactId": x, "at": "2027-05-01T10:00:00Z",
                                     "durationMinutes": 5, "outcome": "connected"}, "call")
        meeting = mk(c, "/activities", {"agencyId": ag, "type": "meeting", "contactId": y, "at": "2027-05-02T10:00:00Z",
                                        "endsAt": "2027-05-02T11:00:00Z", "attendeeIds": [x, z]}, "meeting")
        r = c.post("/webhooks/formflow", json={"event": "lead.created", "id": f"trash-{uid()}", "agency": ag,
                                               "fields": {"name": "Xena Gone", "email": mail}})
        lead = get_id(ok(r, "lead"))
        assert get(c, f"/leads/{lead}").get("contactId") == x, "lead not linked to the contact"
        seg = get_id(ok(c.post(f"/agencies/{ag}/segments", json={"name": "Gone", "filter": {
            "field": "tags", "op": "has", "value": "gone"}}), "segment"))
        st.update(x=x, y=y, z=z, deal=deal, call=call, meeting=meeting, lead=lead, mail=mail, segment=seg)
        status(c.delete(f"/contacts/{x}"), 204, "DELETE contact")
    s.check("delete_contact", fixtures)

    def everywhere():
        x, deal = s.need("x"), s.need("deal")
        for path in (f"/contacts/{x}", f"/deals/{deal}", f"/activities/{s.need('call')}", f"/leads/{s.need('lead')}"):
            status(c.get(path), 404, f"GET {path} after delete")
        assert x not in {get_id(i) for i in listing(c, "/contacts", {"agencyId": ag})}, "contact still listed"
        assert not listing(c, "/deals", {"agencyId": ag}), "deal still listed"
        assert not listing(c, "/leads", {"agencyId": ag}), "lead still listed"
        assert s.need("call") not in {get_id(a) for a in listing(c, "/activities", {"agencyId": ag})}, "activity listed"
        groups = as_list(get(c, "/reports/deals", {"agencyId": ag, "groupBy": "stage"}))
        assert not any(g.get("count") for g in groups), f"report still counts the deal: {groups}"
        assert get(c, f"/segments/{s.need('segment')}/count").get("count") == 0, "segment still counts the contact"
        found = {get_id(r) for r in (get(c, "/search", {"agencyId": ag, "q": "gone"}).get("results") or [])}
        assert not found & {x, deal}, "search still finds the contact or its deal"
    s.check("hidden_everywhere", everywhere)

    def meetings():
        att = get(c, f"/activities/{s.need('meeting')}").get("attendeeIds")
        assert att == [s.need("z")], f"meeting attendees {att}, expected only the remaining attendee"
        line = {get_id(a) for a in listing(c, f"/contacts/{s.need('y')}/activities")}
        assert s.need("meeting") in line, "the meeting of another contact disappeared"
    s.check("meetings_of_others_stay", meetings)

    def trash_list():
        entries = listing(c, "/trash", {"agencyId": ag})
        assert [get_id(e) for e in entries] == [s.need("x")], f"trash {entries}"
        e = entries[0]
        assert (e.get("firstName"), e.get("lastName"), e.get("deals"), e.get("activities")) == ("Xena", "Gone", 1, 1), \
            f"trash entry {e}"
        assert e.get("deletedAt"), "no deletedAt"
    s.check("trash_lists_deleted_contacts", trash_list)

    def new_lead():
        r = c.post("/webhooks/formflow", json={"event": "lead.created", "id": f"trash-{uid()}", "agency": ag,
                                               "fields": {"name": "Xena New", "email": s.need("mail")}})
        lead = ok(r, "lead")
        assert lead.get("contactId") not in (None, s.need("x")), "lead linked to the deleted contact"
        st["new_contact"] = lead.get("contactId")
    s.check("lead_for_deleted_email_creates_contact", new_lead)

    def restore():
        x = s.need("x")
        got = ok(c.post(f"/trash/{x}/restore"), "restore")
        assert get_id(got) == x and got.get("tags") == ["gone"], f"restored {str(got)[:200]}"
        assert get(c, f"/deals/{s.need('deal')}").get("contactId") == x, "deal not restored"
        assert get(c, f"/activities/{s.need('call')}").get("contactId") == x, "activity not restored"
        assert get(c, f"/leads/{s.need('lead')}").get("contactId") == x, "lead not restored"
        att = get(c, f"/activities/{s.need('meeting')}").get("attendeeIds")
        assert att == [x, s.need("z")], f"meeting attendees after restore {att}, expected {[x, s.need('z')]}"
        assert get(c, f"/contacts/{x}").get("openPipeline") == 700, "openPipeline after restore"
        assert get(c, f"/segments/{s.need('segment')}/count").get("count") == 1, "segment after restore"
        assert not listing(c, "/trash", {"agencyId": ag}), "restored contact still in the trash"
    s.check("restore_brings_everything_back", restore)

    def purge():
        x = s.need("x")
        status(c.delete(f"/contacts/{x}"), 204, "DELETE again")
        status(c.delete(f"/trash/{x}"), 204, "purge")
        assert not listing(c, "/trash", {"agencyId": ag}), "purged contact still in the trash"
        r = c.post(f"/trash/{x}/restore")
        assert r.status_code == 404, f"restore after purge: HTTP {r.status_code}"
        status(c.get(f"/deals/{s.need('deal')}"), 404, "deal after purge")
        assert get(c, f"/activities/{s.need('meeting')}").get("attendeeIds") == [s.need("z")], "attendees after purge"
    s.check("purge_deletes_for_good", purge)
    return s.results

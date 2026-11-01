"""Checks introduced by the `activity_timeline` phase: typed activities, timelines and merging."""
from __future__ import annotations

import httpx

from harness.suite import Suite, created, get, get_id, listing, mk, ok, rejected, status
from scenarios.crm.checks.helpers import fresh_agency, person, ts

T = "2027-02-0{}T{:02d}:00:00Z"


def v7(c: httpx.Client, seed: dict | None) -> dict:
    s = Suite("v7")
    st = s.state
    ag = fresh_agency(c, "studio-events")

    def fixtures():
        ids = {k: mk(c, "/contacts", person(ag, first, last), "contact")
               for k, (first, last) in {"ana": ("Ana", "Reis"), "bo": ("Bo", "Lind"), "cy": ("Cy", "Moreau"),
                                         "dee": ("Dee", "Quinn")}.items()}
        acts = {
            "call": {"type": "call", "contactId": ids["ana"], "at": T.format(3, 9), "durationMinutes": 12,
                     "outcome": "connected"},
            "missed": {"type": "call", "contactId": ids["ana"], "at": T.format(4, 9), "durationMinutes": 0,
                       "outcome": "no_answer"},
            "mail": {"type": "email", "contactId": ids["ana"], "at": T.format(5, 9), "direction": "in",
                     "subject": "Venue options"},
            "meet": {"type": "meeting", "contactId": ids["bo"], "at": T.format(6, 10), "endsAt": T.format(6, 11),
                     "attendeeIds": [ids["ana"], ids["cy"]], "location": "Lisbon office"},
            "note": {"type": "note", "contactId": ids["cy"], "at": T.format(2, 8), "text": "Prefers mornings"},
        }
        made = {}
        for k, body in acts.items():
            got = created(c.post("/activities", json={"agencyId": ag, **body}), f"create {k}")
            assert got.get("type") == body["type"], f"{k}: type {got.get('type')!r}"
            made[k] = get_id(got)
        st.update(ids=ids, acts=made)
    s.check("create_each_type", fixtures)

    def reads_back():
        acts, ids = s.need("acts"), s.need("ids")
        got = get(c, f"/activities/{acts['meet']}")
        assert got.get("attendeeIds") == [ids["ana"], ids["cy"]], f"attendeeIds {got.get('attendeeIds')!r}"
        assert got.get("location") == "Lisbon office" and ts(got.get("endsAt")) == ts(T.format(6, 11)), str(got)[:300]
        call = get(c, f"/activities/{acts['call']}")
        assert call.get("durationMinutes") == 12 and call.get("outcome") == "connected", str(call)[:300]
    s.check("activity_reads_back", reads_back)

    def rejects():
        ids = s.need("ids")
        other = mk(c, "/contacts", person(fresh_agency(c, "studio-other"), "Eve", "Other"), "contact")
        base = {"agencyId": ag, "contactId": ids["dee"], "at": T.format(7, 9)}
        for what, body in {
            "call without outcome": {"type": "call", "durationMinutes": 3},
            "call with a bad outcome": {"type": "call", "durationMinutes": 3, "outcome": "busy"},
            "negative duration": {"type": "call", "durationMinutes": -1, "outcome": "connected"},
            "email without subject": {"type": "email", "direction": "out"},
            "a field of another type": {"type": "note", "text": "hi", "subject": "x"},
            "unknown type": {"type": "fax", "text": "hi"},
            "meeting ending before it starts": {"type": "meeting", "endsAt": T.format(7, 8)},
            "attendee of another agency": {"type": "meeting", "endsAt": T.format(7, 10), "attendeeIds": [other]},
            "contact of another agency": {"type": "note", "text": "hi", "contactId": other},
            "the contact as its own attendee": {"type": "meeting", "endsAt": T.format(7, 10), "attendeeIds": [ids["dee"]]},
            "missing at": {"type": "note", "text": "hi", "at": None},
        }.items():
            rejected(c.post("/activities", json={k: v for k, v in {**base, **body}.items() if v is not None}), what)
        assert listing(c, f"/contacts/{ids['dee']}/activities") == [], "a rejected activity was stored"
    s.check("rejects_invalid_activities", rejects)

    def timeline(key: str) -> list[str]:
        return [get_id(a) for a in listing(c, f"/contacts/{s.need('ids')[key]}/activities")]

    def timelines():
        acts = s.need("acts")
        assert timeline("ana") == [acts[k] for k in ("meet", "mail", "missed", "call")], \
            f"ana's timeline {timeline('ana')}"
        assert timeline("cy") == [acts["meet"], acts["note"]], f"cy's timeline {timeline('cy')}"
        assert timeline("bo") == [acts["meet"]] and timeline("dee") == [], "bo's or dee's timeline"
    s.check("timeline_newest_first_with_meetings_attended", timelines)

    def last_activity(want: dict[str, str | None]) -> None:
        ids = s.need("ids")
        listed = {get_id(x): x for x in listing(c, "/contacts", {"agencyId": ag})}
        for k, at in want.items():
            for where, contact in (("GET", get(c, f"/contacts/{ids[k]}")), ("list", listed.get(ids[k], {}))):
                got = contact.get("lastActivityAt")
                assert (got is None if at is None else got is not None and ts(got) == ts(at)), \
                    f"{k} ({where}): lastActivityAt {got!r}, expected {at}"

    s.check("last_activity_at", lambda: last_activity({"ana": T.format(6, 10), "cy": T.format(6, 10),
                                                        "bo": T.format(6, 10), "dee": None}))

    def patches():
        acts = s.need("acts")
        got = ok(c.patch(f"/activities/{acts['missed']}", json={"outcome": "voicemail", "durationMinutes": 1}), "PATCH call")
        assert got.get("outcome") == "voicemail" and got.get("durationMinutes") == 1, str(got)[:300]
        got = ok(c.patch(f"/activities/{acts['meet']}", json={"location": None}), "PATCH meeting location")
        assert got.get("location") is None, f"location {got.get('location')!r}"
        assert get(c, f"/activities/{acts['meet']}").get("location") is None, "location not cleared"
        rejected(c.patch(f"/activities/{acts['call']}", json={"type": "note"}), "changing type")
        rejected(c.patch(f"/activities/{acts['call']}", json={"contactId": s.need("ids")["bo"]}), "changing contactId")
        rejected(c.patch(f"/activities/{acts['call']}", json={"subject": "x"}), "a field of another type")
        rejected(c.patch(f"/activities/{acts['meet']}", json={"endsAt": T.format(6, 9)}), "endsAt before at")
        assert get(c, f"/activities/{acts['call']}").get("type") == "call", "rejected PATCH changed the activity"
    s.check("patch_activity", patches)

    def last_follows():
        ids, acts = s.need("ids"), s.need("acts")
        late = mk(c, "/activities", {"agencyId": ag, "type": "note", "contactId": ids["dee"], "at": T.format(8, 9),
                                     "text": "Called back"}, "note")
        last_activity({"dee": T.format(8, 9)})
        status(c.delete(f"/activities/{late}"), 204, "DELETE activity")
        status(c.get(f"/activities/{late}"), 404, "GET deleted activity")
        ok(c.patch(f"/activities/{acts['meet']}", json={"at": T.format(1, 10), "endsAt": T.format(1, 11)}), "move meeting")
        last_activity({"dee": None, "ana": T.format(5, 9), "bo": T.format(1, 10)})
        assert timeline("ana")[-1] == acts["meet"], "moved meeting not last on ana's timeline"
    s.check("last_activity_follows_changes", last_follows)

    def search(params: dict, keys: list[str]) -> None:
        acts = s.need("acts")
        got = [get_id(a) for a in listing(c, "/activities", {"agencyId": ag, **params})]
        assert got == [acts[k] for k in keys], f"{params}: got {got}, expected {[acts[k] for k in keys]}"

    def filters():
        ids = s.need("ids")
        search({"type": "call"}, ["missed", "call"])
        search({"outcome": "connected"}, ["call"])
        search({"direction": "in"}, ["mail"])
        search({"minDurationMinutes": 5}, ["call"])
        search({"contactId": ids["cy"]}, ["note", "meet"])
        search({"from": T.format(3, 9), "to": T.format(5, 9)}, ["mail", "missed", "call"])
        search({"type": "call", "from": T.format(4, 0)}, ["missed"])
        assert listing(c, "/activities", {"agencyId": ag}, limit=2) == listing(c, "/activities", {"agencyId": ag})[:2], \
            "limit"
        assert listing(c, "/activities", {"agencyId": fresh_agency(c, "studio-empty")}) == [], "agency isolation"
    s.check("filter_activities", filters)

    def merge_moves():
        ids = s.need("ids")
        dup = mk(c, "/contacts", person(ag, "Ana", "Reis"), "duplicate")
        own = mk(c, "/activities", {"agencyId": ag, "type": "note", "contactId": dup, "at": T.format(9, 9),
                                    "text": "From the duplicate"}, "note")
        shared = mk(c, "/activities", {"agencyId": ag, "type": "meeting", "contactId": ids["bo"], "at": T.format(9, 10),
                                       "endsAt": T.format(9, 11), "attendeeIds": [dup, ids["ana"]]}, "meeting")
        hosted = mk(c, "/activities", {"agencyId": ag, "type": "meeting", "contactId": ids["ana"],
                                       "at": T.format(9, 12), "endsAt": T.format(9, 13), "attendeeIds": [dup]}, "meeting")
        ok(c.post(f"/contacts/{ids['ana']}/merge", json={"duplicateId": dup}), "merge")
        line = timeline("ana")
        assert {own, shared, hosted} <= set(line) and len(line) == len(set(line)), f"ana's timeline after merge {line}"
        assert get(c, f"/activities/{own}").get("contactId") == ids["ana"], "note not moved to the kept contact"
        att = get(c, f"/activities/{shared}").get("attendeeIds")
        assert att == [ids["ana"]], f"shared meeting attendees {att}, expected only the kept contact once"
        att = get(c, f"/activities/{hosted}").get("attendeeIds") or []
        assert att == [], f"kept contact's own meeting lists attendees {att}"
    s.check("merge_moves_activities", merge_moves)
    return s.results

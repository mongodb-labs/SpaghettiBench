"""Checks introduced by the `inbound_leads` phase (3: inbound leads)."""
from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

import httpx

from harness.suite import Suite, created, expect, get, get_id, id_set, listing, mk, rejected, same_set
from scenarios.crm.checks.helpers import addresses, email_list, fresh_agency, person, phone_list, seed_by_key, uid
from scenarios.crm.common import email


def formflow(ag: Any, ext: Any, name: str, email_: str | None = None, phone_: str | None = None,
             company: str | None = None, event: str = "lead.created", **extra: Any) -> dict:
    fields: dict[str, Any] = {"name": name}
    for k, v in (("email", email_), ("phone", phone_), ("company", company)):
        if v is not None:
            fields[k] = v
    body = {"event": event, "id": ext, "agency": ag, "submitted": "2026-10-03T10:00:00Z", "fields": fields, **extra}
    return {k: v for k, v in body.items() if v is not None}


def leadpipe1(ag: Any, ext: Any, first: str, last: str, emails: list, phones: list, org: str | None = None,
              kind: str = "new_lead", **extra: Any) -> dict:
    body = {"type": kind, "lead_id": ext, "account": ag, "created_at": 1791021600,
            "person": {"first_name": first, "last_name": last, "emails": emails, "phones": phones}, **extra}
    if org:
        body["organization"] = {"name": org}
    return {k: v for k, v in body.items() if v is not None}


def leadpipe2(ag: Any, ext: Any, first: str, last: str, emails: list[tuple[str, bool]], phones: list[str],
              org: str | None = None, event: str = "lead.new", **extra: Any) -> dict:
    lead = {"id": ext, "account_id": ag, "created": "2026-10-05T08:30:00Z",
            "contact": {"name": {"first": first, "last": last},
                        "email_addresses": [{"value": v, "primary": p} for v, p in emails],
                        "phone_numbers": [{"value": v} for v in phones]}, **extra}
    if org:
        lead["organization"] = {"name": org}
    return {"version": 2, "event": event, "lead": {k: v for k, v in lead.items() if v is not None}}


def lead_int() -> int:
    return uuid.uuid4().int % 10**12


def v3(c: httpx.Client, seed: dict | None) -> dict:
    s = Suite("v3")
    st = s.state
    ag, other = fresh_agency(c, "costa-brava-homes"), fresh_agency(c, "alpine-chalets")

    def deliver(provider: str, body: dict, key: str) -> dict:
        lead = created(c.post(f"/webhooks/{provider}", json=body), f"{provider} lead")
        st[key], st[f"{key}_body"] = get_id(lead), body
        return lead

    def lead_of(key: str) -> dict:
        return get(c, f"/leads/{s.need(key)}")

    def fixtures():
        st["sol"] = mk(c, "/companies", {"agencyId": ag, "name": "Sol Properties"}, "company")
        st["eva"] = mk(c, "/contacts", person(ag, "Eva", "Lind", emails=[{"address": "eva@lind.se", "label": "work"}],
                                              phones=[{"number": "+46 70 111 22 33", "label": "mobile"}]), "contact")
    s.check("create_fixtures", fixtures)

    def ff1():
        s.need("sol")
        deliver("formflow", formflow(ag, f"ff_{uid()}", "María José García", "maria@solproperties.es", "+34 600 111 222",
                                     " sol properties ", tracking={"utm_source": "google", "utm_campaign": "spring-2027"}),
                "ff1")
    s.check("formflow_creates_lead", ff1)

    def ff1_read_back():
        got, body = lead_of("ff1"), s.need("ff1_body")
        assert (got.get("agencyId"), got.get("provider"), got.get("externalId")) == (ag, "formflow", body["id"]), \
            f"agencyId/provider/externalId {got.get('agencyId')!r}/{got.get('provider')!r}/{got.get('externalId')!r}"
        assert got.get("payload") == body, "payload not stored exactly as received"
        assert got.get("contactId"), "no contactId"
    s.check("lead_read_back", ff1_read_back)

    def ff1_contact():
        got = get(c, f"/contacts/{lead_of('ff1').get('contactId')}")
        assert (got.get("firstName"), got.get("lastName")) == ("María José", "García"), \
            f"names {got.get('firstName')!r} {got.get('lastName')!r}"
        assert email_list(got) == [("maria@solproperties.es", "other")], f"emails {got.get('emails')}"
        assert phone_list(got) == [("+34600111222", "other")], f"phones {got.get('phones')}"
        assert got.get("companyId") == s.need("sol"), "not linked to the existing company 'Sol Properties'"
    s.check("formflow_creates_contact", ff1_contact)

    def company_not_duplicated():
        names = [co.get("name", "").strip().lower() for co in listing(c, "/companies", {"agencyId": ag})]
        assert names.count("sol properties") == 1, f"{names.count('sol properties')} companies named Sol Properties"
    s.check("existing_company_matched", company_not_duplicated)

    def ff2():
        deliver("formflow", formflow(ag, f"ff_{uid()}", "Pedro", "PEDRO@estudio.es", company="Nuevo Estudio"), "ff2")
        got = get(c, f"/contacts/{lead_of('ff2').get('contactId')}")
        assert (got.get("firstName"), got.get("lastName")) == ("Pedro", "Pedro"), \
            f"names {got.get('firstName')!r} {got.get('lastName')!r}"
        st["pedro"] = got
    s.check("formflow_single_word_name", ff2)

    def new_company():
        got = s.need("pedro")
        matches = [co for co in listing(c, "/companies", {"agencyId": ag}) if co.get("name") == "Nuevo Estudio"]
        assert len(matches) == 1, f"{len(matches)} companies 'Nuevo Estudio'"
        assert got.get("companyId") == get_id(matches[0]) and got.get("companyName") == "Nuevo Estudio", \
            f"contact company {got.get('companyId')!r}/{got.get('companyName')!r}"
    s.check("formflow_creates_company", new_company)

    def lp1():
        deliver("leadpipe", leadpipe1(ag, lead_int(), "Tom", "Becker", ["tom@becker.de", "t.becker@gmail.com"],
                                      ["+49 171 555 0101"], "Becker Bau",
                                      answers=[{"question": "Budget", "answer": "400k-600k"},
                                               {"question": "Timeline", "answer": "Q2 2027"}]), "lp1")
        got, body = lead_of("lp1"), s.need("lp1_body")
        assert (got.get("provider"), got.get("externalId")) == ("leadpipe", str(body["lead_id"])), \
            f"provider/externalId {got.get('provider')!r}/{got.get('externalId')!r}"
        assert got.get("payload") == body, "payload not stored exactly as received"
    s.check("leadpipe_v1_lead", lp1)

    def lp1_contact():
        got = get(c, f"/contacts/{lead_of('lp1').get('contactId')}")
        assert (got.get("firstName"), got.get("lastName")) == ("Tom", "Becker"), "names"
        assert email_list(got) == [("tom@becker.de", "other"), ("t.becker@gmail.com", "other")], f"emails {got.get('emails')}"
        assert phone_list(got) == [("+491715550101", "other")], f"phones {got.get('phones')}"
        assert got.get("companyName") == "Becker Bau", f"company {got.get('companyName')!r}"
        st["becker"] = get_id(got)
    s.check("leadpipe_v1_contact", lp1_contact)

    def lp2():
        deliver("leadpipe", leadpipe2(ag, f"lp2_{uid()}", "Thomas", "Becker",
                                      [("T.Becker@gmail.com", False), ("tom@becker-bau.de", True)], ["+49 30 1234567"],
                                      "Becker Bau", custom_answers={"budget": "600k"}), "lp2")
        got, body = lead_of("lp2"), s.need("lp2_body")
        assert (got.get("provider"), got.get("externalId")) == ("leadpipe", body["lead"]["id"]), \
            f"provider/externalId {got.get('provider')!r}/{got.get('externalId')!r}"
        assert got.get("payload") == body, "payload not stored exactly as received"
    s.check("leadpipe_v2_lead", lp2)

    s.check("leadpipe_v2_matches_existing_contact", lambda: expect(
        lead_of("lp2").get("contactId") == s.need("becker"), lambda: "v2 lead not linked to the contact with that address"))

    def appended():
        got = get(c, f"/contacts/{s.need('becker')}")
        assert email_list(got) == [("tom@becker.de", "other"), ("t.becker@gmail.com", "other"),
                                   ("tom@becker-bau.de", "other")], f"emails {got.get('emails')}"
        assert phone_list(got) == [("+491715550101", "other"), ("+49301234567", "other")], f"phones {got.get('phones')}"
        assert (got.get("firstName"), got.get("lastName")) == ("Tom", "Becker"), "the lead changed the contact's names"
        names = [co.get("name") for co in listing(c, "/companies", {"agencyId": ag})]
        assert names.count("Becker Bau") == 1, f"{names.count('Becker Bau')} companies 'Becker Bau'"
    s.check("lead_appends_new_addresses", appended)

    def ff3():
        deliver("formflow", formflow(ag, f"ff_{uid()}", "Eva Lindqvist", "EVA@LIND.SE", "+46 70 111 22 33"), "ff3")
        assert lead_of("ff3").get("contactId") == s.need("eva"), "not linked to the contact with that address"
        got = get(c, f"/contacts/{s.need('eva')}")
        assert (got.get("firstName"), got.get("lastName")) == ("Eva", "Lind"), "names changed"
        assert email_list(got) == [("eva@lind.se", "work")] and phone_list(got) == [("+46701112233", "mobile")], \
            f"lists changed: {got.get('emails')} {got.get('phones')}"
    s.check("lead_matches_case_insensitively", ff3)

    def no_email():
        a = deliver("leadpipe", leadpipe1(ag, lead_int(), "Anna", "Schmidt", [], ["+49 151 000 0001"]), "anna1")
        b = deliver("leadpipe", leadpipe1(ag, lead_int(), "Anna", "Schmidt", [], ["+49 151 000 0002"]), "anna2")
        ca, cb = lead_of("anna1").get("contactId"), lead_of("anna2").get("contactId")
        assert ca and cb and ca != cb, "leads without an address must each create a contact"
        assert get_id(a) and get_id(b)
    s.check("lead_without_email_creates_contact", no_email)

    def redelivery():
        before = len(listing(c, "/contacts", {"agencyId": ag}))
        r = c.post("/webhooks/formflow", json=s.need("ff1_body"))
        assert r.status_code == 200, f"redelivery: HTTP {r.status_code} (expected 200)"
        assert get_id(r.json()) == s.need("ff1"), "redelivery returned a different lead"
        assert len(listing(c, "/contacts", {"agencyId": ag})) == before, "redelivery created a contact"
        same = listing(c, "/leads", {"agencyId": ag, "payload.id": s.need("ff1_body")["id"]})
        assert len(same) == 1, f"{len(same)} leads with that id"
    s.check("redelivery_returns_existing_lead", redelivery)

    def concurrent():
        body = formflow(ag, f"ff_{uid()}", "Zoe Park", "zoe@park.kr")
        with ThreadPoolExecutor(8) as ex:
            rs = list(ex.map(lambda _: c.post("/webhooks/formflow", json=body), range(8)))
        assert all(r.status_code in (200, 201) for r in rs), f"statuses {sorted(r.status_code for r in rs)}"
        ids = {get_id(r.json()) for r in rs}
        assert len(ids) == 1, f"{len(ids)} different leads returned"
        st["ff4"] = ids.pop()
        assert len(listing(c, "/leads", {"agencyId": ag, "payload.id": body["id"]})) == 1, "duplicate leads stored"
        assert len(listing(c, "/contacts", {"agencyId": ag, "email": "zoe@park.kr"})) == 1, "duplicate contacts created"
    s.check("concurrent_redelivery_single_lead", concurrent)

    def ignored():
        ext_ff, ext_lp1, ext_lp2 = f"ff_{uid()}", lead_int(), f"lp2_{uid()}"
        for provider, body in (("formflow", formflow(ag, ext_ff, "Ivy Ignored", "ivy@ignored.org", event="form.viewed")),
                               ("leadpipe", leadpipe1(ag, ext_lp1, "Ivy", "Ignored", ["ivy@ignored.org"], [], kind="lead_updated")),
                               ("leadpipe", leadpipe2(ag, ext_lp2, "Ivy", "Ignored", [("ivy@ignored.org", True)], [],
                                                      event="lead.deleted"))):
            r = c.post(f"/webhooks/{provider}", json=body)
            assert r.status_code in (200, 202, 204), f"{provider} other event: HTTP {r.status_code}"
        assert not listing(c, "/contacts", {"agencyId": ag, "email": "ivy@ignored.org"}), "an ignored event created a contact"
        assert not listing(c, "/leads", {"agencyId": ag, "payload.id": ext_ff}), "an ignored event was stored"
        assert not listing(c, "/leads", {"agencyId": ag, "payload.lead_id": str(ext_lp1)}), "an ignored event was stored"
    s.check("ignores_other_events", ignored)

    def malformed():
        rejected(c.post("/webhooks/formflow", json=formflow(ag, None, "Rex Rejected", "rex@rejected.org")), "no id")
        rejected(c.post("/webhooks/leadpipe", json=leadpipe1(None, lead_int(), "Rex", "Rejected", ["rex@rejected.org"], [])),
                 "no account")
        rejected(c.post("/webhooks/leadpipe", json=leadpipe2(ag, None, "Rex", "Rejected", [("rex@rejected.org", True)], [])),
                 "no v2 id")
        assert not listing(c, "/contacts", {"agencyId": ag, "email": "rex@rejected.org"}), "a rejected lead created a contact"
    s.check("rejects_lead_without_id_or_agency", malformed)

    def by_provider():
        same_set(listing(c, "/leads", {"agencyId": ag, "provider": "leadpipe"}),
                 {s.need(k) for k in ("lp1", "lp2", "anna1", "anna2")}, "provider=leadpipe")
        same_set(listing(c, "/leads", {"agencyId": ag, "provider": "formflow"}),
                 {s.need(k) for k in ("ff1", "ff2", "ff3", "ff4")}, "provider=formflow")
    s.check("list_leads_by_provider", by_provider)
    s.check("list_leads_by_contact", lambda: same_set(
        listing(c, "/leads", {"agencyId": ag, "contactId": s.need("becker")}), {s.need("lp1"), s.need("lp2")}, "contactId"))

    def pfilter(params: Callable[[], dict], keys: list[str]) -> Callable[[], None]:
        return lambda: same_set(listing(c, "/leads", {"agencyId": ag, **params()}), {s.need(k) for k in keys}, str(params()))
    s.check("payload_filter_nested", pfilter(lambda: {"payload.tracking.utm_campaign": "spring-2027"}, ["ff1"]))
    s.check("payload_filter_array", pfilter(lambda: {"payload.answers.question": "Timeline"}, ["lp1"]))
    s.check("payload_filter_number", pfilter(lambda: {"payload.lead_id": str(s.need("lp1_body")["lead_id"])}, ["lp1"]))
    s.check("payload_filter_v2_path", pfilter(lambda: {"payload.lead.contact.name.last": "Becker"}, ["lp2"]))
    s.check("payload_filter_array_of_objects", pfilter(
        lambda: {"payload.lead.contact.email_addresses.value": "tom@becker-bau.de"}, ["lp2"]))
    s.check("payload_filter_boolean", pfilter(lambda: {"payload.lead.contact.email_addresses.primary": "false"}, ["lp2"]))
    s.check("payload_filter_with_provider", pfilter(
        lambda: {"provider": "formflow", "payload.fields.company": "Nuevo Estudio"}, ["ff2"]))
    s.check("payload_filter_missing_path", pfilter(lambda: {"payload.fields.nothing": "x"}, []))

    def per_agency():
        theirs = deliver("formflow", formflow(other, f"ff_{uid()}", "Otto Other", "otto@other.ch"), "theirs")
        assert get_id(theirs) not in id_set(listing(c, "/leads", {"agencyId": ag})), "another agency's lead listed"
        same_set(listing(c, "/leads", {"agencyId": other}), {get_id(theirs)}, "other agency's leads")
        assert not listing(c, "/contacts", {"agencyId": ag, "email": "otto@other.ch"}), "contact created in the wrong agency"
    s.check("leads_per_agency", per_agency)

    if seed:
        def seeded_lead():
            target = seed_by_key(seed, "lead_target")
            addr = target["payload"]["email"]
            lead = created(c.post("/webhooks/formflow", json=formflow("harbor-realty", f"ff_{uid()}", "J. Lindqvist",
                                                                      addr.upper())), "lead")
            assert get(c, f"/leads/{get_id(lead)}").get("contactId") == target["id"], "not linked to the existing contact"
            got = get(c, f"/contacts/{target['id']}")
            assert (got.get("firstName"), got.get("lastName")) == ("Jonas", "Lindqvist"), "names changed"
            assert addresses(got) == {email(addr)}, f"addresses changed: {got.get('emails')}"
        s.check("seeded_lead_links_existing_contact", seeded_lead)

    return s.results

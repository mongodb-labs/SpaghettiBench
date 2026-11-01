"""Checks introduced by the `multiple_emails` phase (1: several emails and phones)."""
from __future__ import annotations

from typing import Any

import httpx

from harness.suite import Suite, first_mismatches, get, listing, mk, ok, rejected, same_set, superset_matching
from scenarios.crm.checks.helpers import email_list, fresh_agency, person, phone_list, seed_contacts
from scenarios.crm.common import email, phone


def v1(c: httpx.Client, seed: dict | None, exclude: frozenset = frozenset()) -> dict:
    s = Suite("v1")
    st = s.state
    ag = fresh_agency(c, "porto-lofts")

    def create():
        st["ana"] = mk(c, "/contacts", person(
            ag, "Ana", "Silva",
            emails=[{"address": "ana@work.pt", "label": "work"}, {"address": "Ana.Silva@gmail.com", "label": "home"}],
            phones=[{"number": "+351 910 000 001", "label": "mobile"}, {"number": "+351 210 000 002", "label": "work"}]),
            "contact with lists")
        st["bo"] = mk(c, "/contacts", person(ag, "Bo", "Ek", email="bo@ek.se", phone="+46 70 123 4567"), "old shape")
        st["cy"] = mk(c, "/contacts", person(ag, "Cy", "Young"), "contact without email or phone")
    s.check("create_contacts", create)

    def lists_read_back():
        got = get(c, f"/contacts/{s.need('ana')}")
        assert email_list(got) == [("ana@work.pt", "work"), ("ana.silva@gmail.com", "home")], f"emails {got.get('emails')}"
        assert phone_list(got) == [("+351910000001", "mobile"), ("+351210000002", "work")], f"phones {got.get('phones')}"
        assert email(got.get("email")) == "ana@work.pt", f"email {got.get('email')!r} is not the primary address"
        assert phone(got.get("phone")) == "+351910000001", f"phone {got.get('phone')!r} is not the primary number"
    s.check("lists_read_back", lists_read_back)

    def old_shape():
        got = get(c, f"/contacts/{s.need('bo')}")
        assert email_list(got) == [("bo@ek.se", "work")], f"emails {got.get('emails')}"
        assert phone_list(got) == [("+46701234567", "work")], f"phones {got.get('phones')}"
    s.check("old_shape_creates_one_item_lists", old_shape)

    def empty_lists():
        got = get(c, f"/contacts/{s.need('cy')}")
        assert got.get("emails") == [] and got.get("phones") == [], f"emails {got.get('emails')!r}, phones {got.get('phones')!r}"
        assert got.get("email") is None and got.get("phone") is None, "email/phone set without entries"
    s.check("empty_lists_when_none", empty_lists)

    invalid = {
        "unknown_label": {"emails": [{"address": "rex@example.com", "label": "private"}]},
        "invalid_address_in_list": {"emails": [{"address": "rex.example.com", "label": "work"}]},
        "emails_not_a_list": {"emails": "rex@example.com"},
        "email_and_emails": {"email": "rex@example.com", "emails": [{"address": "rex@example.com", "label": "work"}]},
        "phone_and_phones": {"phone": "+1 555 0101", "phones": [{"number": "+1 555 0101", "label": "work"}]},
    }
    for name, extra in invalid.items():
        s.check(f"rejects_{name}", lambda extra=extra, name=name: rejected(
            c.post("/contacts", json=person(ag, "Rex", "Rejected", **extra)), name))

    def not_stored():
        same_set(listing(c, "/contacts", {"agencyId": ag}), {s.need("ana"), s.need("bo"), s.need("cy")}, "after rejections")
    s.check("invalid_not_stored", not_stored)

    s.check("filter_email_any_position", lambda: same_set(
        listing(c, "/contacts", {"agencyId": ag, "email": "ANA.SILVA@GMAIL.COM"}), {s.need("ana")}, "second address"))
    s.check("filter_phone_any_position", lambda: same_set(
        listing(c, "/contacts", {"agencyId": ag, "phone": "+351 210 000 002"}), {s.need("ana")}, "second number"))

    def patch_lists():
        bo = s.need("bo")
        ok(c.patch(f"/contacts/{bo}", json={"emails": [{"address": "bo@new.se", "label": "other"}]}), "PATCH emails")
        got = get(c, f"/contacts/{bo}")
        assert email_list(got) == [("bo@new.se", "other")], f"emails {got.get('emails')}"
        assert email(got.get("email")) == "bo@new.se", f"email {got.get('email')!r}"
        assert phone_list(got) == [("+46701234567", "work")], "phones changed by an emails PATCH"
        same_set(listing(c, "/contacts", {"agencyId": ag, "email": "bo@ek.se"}), set(), "old address still found")
    s.check("patch_lists_replace", patch_lists)

    def patch_old_email():
        ana = s.need("ana")
        ok(c.patch(f"/contacts/{ana}", json={"email": "ana@new-work.pt"}), "PATCH email")
        got = get(c, f"/contacts/{ana}")
        assert email_list(got) == [("ana@new-work.pt", "work"), ("ana.silva@gmail.com", "home")], f"emails {got.get('emails')}"
    s.check("patch_old_email_sets_primary", patch_old_email)

    def patch_old_phone():
        cy = s.need("cy")
        ok(c.patch(f"/contacts/{cy}", json={"phone": "+1 555 0100"}), "PATCH phone")
        assert phone_list(get(c, f"/contacts/{cy}")) == [("+15550100", "work")], "phone not added as a work entry"
    s.check("patch_old_phone_adds_entry", patch_old_phone)

    def patch_old_email_null():
        ana = s.need("ana")
        ok(c.patch(f"/contacts/{ana}", json={"email": None}), "PATCH email null")
        got = get(c, f"/contacts/{ana}")
        assert email_list(got) == [("ana.silva@gmail.com", "home")], f"emails {got.get('emails')}"
        assert email(got.get("email")) == "ana.silva@gmail.com", f"email {got.get('email')!r}"
    s.check("patch_old_email_null_removes_primary", patch_old_email_null)

    if seed:
        def seeded_converted():
            def problem(x: dict, got: dict) -> Any:
                p = x["payload"]
                want_e = [(email(p["email"]), "work")] if p.get("email") else []
                want_p = [(phone(p["phone"]), "work")] if p.get("phone") else []
                return (email_list(got), phone_list(got)) != (want_e, want_p) and \
                    f"emails {got.get('emails')} phones {got.get('phones')}"
            first_mismatches(seed_contacts(seed, exclude), lambda x: get(c, f"/contacts/{x['id']}"), problem)
        s.check("seeded_contacts_converted", seeded_converted)

        def seeded_filter_phone():
            for x in [x for x in seed_contacts(seed, exclude) if x["payload"].get("phone")][:6]:
                number = x["payload"]["phone"]
                items = listing(c, "/contacts", {"agencyId": x["agency"], "phone": number})
                superset_matching(items, {x["id"]}, lambda i: phone(number) in {n for n, _ in phone_list(i)},
                                  f"phone={number}")
        s.check("seeded_filter_phone", seeded_filter_phone)

    return s.results

"""Checks introduced by the `contact_merge` phase (5: merging contacts)."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx

from harness.suite import Suite, as_list, created, expect, get, get_id, id_set, listing, mk, ok, rejected, same_set
from scenarios.crm.checks.helpers import (DUP_PAIRS, custom_equal, email_list, fresh_agency, person, phone_list,
                                          seed_by_key, uid)
from scenarios.crm.checks.inbound_leads import formflow
from scenarios.crm.common import email, fields_match, phone


def merges_of(c: httpx.Client, cid: str) -> list:
    return as_list(get(c, f"/contacts/{cid}/merges"))


def v5(c: httpx.Client, seed: dict | None) -> dict:
    s = Suite("v5")
    st = s.state
    ag, other = fresh_agency(c, "nordic-homes"), fresh_agency(c, "baltic-homes")

    def contact(first: str, last: str, **kw: Any) -> str:
        return mk(c, "/contacts", person(ag, first, last, **kw), f"contact {first}")

    def deal(cid: str, title: str) -> str:
        return mk(c, "/deals", {"agencyId": ag, "contactId": cid, "title": title, "value": 1000, "stage": "lead"}, "deal")

    def fixtures():
        for f in ({"entity": "contact", "key": "budget", "label": "Budget", "type": "number"},
                  {"entity": "contact", "key": "source", "label": "Source", "type": "text"}):
            ok(c.post(f"/agencies/{ag}/fields", json=f), "define")
        st["x"] = mk(c, "/companies", {"agencyId": ag, "name": "Berg & Co"}, "company")
        st["k"] = contact("Kim", "Berg", emails=[{"address": "kim@berg.se", "label": "work"}],
                          phones=[{"number": "+46 8 100 100", "label": "mobile"}], custom={"budget": 100})
        st["d_emails"] = [{"address": "KIM@berg.se", "label": "home"}, {"address": "kim.b@gmail.com", "label": "home"}]
        st["d"] = contact("Kimberly", "Berg", emails=st["d_emails"], title="CEO", companyId=st["x"],
                          phones=[{"number": "+46 8 100 100", "label": "work"}, {"number": "+46 70 200 200", "label": "mobile"}],
                          custom={"budget": 999, "source": "fair"})
        st["kd1"], st["dd1"], st["dd2"] = deal(st["k"], "Kept deal"), deal(st["d"], "Dup deal 1"), deal(st["d"], "Dup deal 2")
        lead = created(c.post("/webhooks/formflow", json=formflow(ag, f"ff_{uid()}", "Kimberly Berg", "kim.b@gmail.com")), "lead")
        st["lead"] = get_id(lead)
        assert get(c, f"/leads/{st['lead']}").get("contactId") == st["d"], "lead not linked to the duplicate before the merge"
        st["foreign"] = mk(c, "/contacts", person(other, "Otto", "Other"), "other agency's contact")
    s.check("create_fixtures", fixtures)

    def merge():
        k, d = s.need("k"), s.need("d")
        got = ok(c.post(f"/contacts/{k}/merge", json={"duplicateId": d}), "merge")
        assert get_id(got) == k, f"merge returned {get_id(got)!r}, not the kept contact"
        st["merged"] = True
    s.check("merge_returns_kept_contact", merge)

    def kept() -> dict:
        s.need("merged")
        return get(c, f"/contacts/{s.need('k')}")

    def fields():
        got = kept()
        want = ("Kim", "Berg", "CEO", s.need("x"), "Berg & Co")
        have = tuple(got.get(k) for k in ("firstName", "lastName", "title", "companyId", "companyName"))
        assert have == want, f"names/title/company {have} != {want}"
    s.check("merge_keeps_names_and_fills_gaps", fields)
    s.check("merge_combines_emails", lambda: expect(
        email_list(kept()) == [("kim@berg.se", "work"), ("kim.b@gmail.com", "home")], lambda: f"emails {kept().get('emails')}"))
    s.check("merge_combines_phones", lambda: expect(
        phone_list(kept()) == [("+468100100", "mobile"), ("+4670200200", "mobile")], lambda: f"phones {kept().get('phones')}"))
    s.check("merge_combines_custom", lambda: expect(
        custom_equal({"budget": 100, "source": "fair"}, kept().get("custom")), lambda: f"custom {kept().get('custom')}"))

    def moves_deals():
        s.need("merged")
        k = s.need("k")
        for key in ("dd1", "dd2"):
            got = get(c, f"/deals/{s.need(key)}")
            have = (got.get("contactId"), got.get("contactName"), got.get("companyId"))
            assert have == (k, "Kim Berg", s.need("x")), f"deal {s.need(key)}: contactId/contactName/companyId {have}"
        same_set(listing(c, "/deals", {"agencyId": ag, "contactId": k}), {s.need("kd1"), s.need("dd1"), s.need("dd2")},
                 "deals of the kept contact")
    s.check("merge_moves_deals", moves_deals)

    def moves_leads():
        s.need("merged")
        assert get(c, f"/leads/{s.need('lead')}").get("contactId") == s.need("k"), "lead still on the duplicate"
        assert s.need("lead") in id_set(listing(c, "/leads", {"agencyId": ag, "contactId": s.need("k")})), "lead not listed"
    s.check("merge_moves_leads", moves_leads)

    def resolves():
        s.need("merged")
        assert get_id(get(c, f"/contacts/{s.need('d')}")) == s.need("k"), "the duplicate's id doesn't return the kept contact"
    s.check("duplicate_id_returns_kept_contact", resolves)

    def gone():
        s.need("merged")
        k, d = s.need("k"), s.need("d")
        assert d not in id_set(listing(c, "/contacts", {"agencyId": ag})), "duplicate still listed"
        same_set(listing(c, "/contacts", {"agencyId": ag, "email": "kim.b@gmail.com"}), {k}, "email filter")
        same_set(listing(c, "/contacts", {"agencyId": ag, "lastName": "Berg"}), {k}, "lastName filter")
    s.check("duplicate_gone_from_lists", gone)

    def history():
        s.need("merged")
        entries = [e for e in merges_of(c, s.need("k")) if e.get("duplicateId") == s.need("d")]
        assert len(entries) == 1, f"{len(entries)} merge records for the duplicate"
        snap = entries[0].get("duplicate") or {}
        assert (snap.get("firstName"), snap.get("title")) == ("Kimberly", "CEO"), f"snapshot {str(snap)[:200]}"
        assert email_list(snap) == [(email(e["address"]), e["label"]) for e in s.need("d_emails")], "snapshot emails"
        assert entries[0].get("mergedAt"), "no mergedAt"
    s.check("merge_history", history)

    def lead_after():
        s.need("merged")
        lead = created(c.post("/webhooks/formflow", json=formflow(ag, f"ff_{uid()}", "Kim B", "KIM.B@gmail.com")), "lead")
        assert get(c, f"/leads/{get_id(lead)}").get("contactId") == s.need("k"), "new lead not linked to the kept contact"
    s.check("lead_after_merge_links_kept_contact", lead_after)

    def rejects():
        k, d = s.need("k"), s.need("d")
        rejected(c.post(f"/contacts/{k}/merge", json={"duplicateId": k}), "merge into itself")
        rejected(c.post(f"/contacts/{k}/merge", json={"duplicateId": s.need("foreign")}), "other agency")
        s.need("merged")
        rejected(c.post(f"/contacts/{k}/merge", json={"duplicateId": d}), "duplicate merged twice")
        fresh = contact("Fay", "Fresh")
        rejected(c.post(f"/contacts/{d}/merge", json={"duplicateId": fresh}), "merge into a merged-away contact")
        assert get_id(get(c, f"/contacts/{fresh}")) == fresh, "rejected merge changed the contact"
    s.check("merge_rejects_invalid", rejects)

    def chain():
        a, b, cc = contact("Ann", "Chain"), contact("Bea", "Chain"), contact("Cat", "Chain")
        dc = deal(cc, "Chain deal")
        ok(c.post(f"/contacts/{b}/merge", json={"duplicateId": cc}), "merge C into B")
        ok(c.post(f"/contacts/{a}/merge", json={"duplicateId": b}), "merge B into A")
        assert {e.get("duplicateId") for e in merges_of(c, a)} == {b, cc}, "A's merges must include B's"
        assert get_id(get(c, f"/contacts/{cc}")) == a and get_id(get(c, f"/contacts/{b}")) == a, "merged ids don't return A"
        assert get(c, f"/deals/{dc}").get("contactId") == a, "C's deal not on A"
    s.check("merge_chain", chain)

    def trio(tag: str) -> tuple[list[str], dict[str, list[str]]]:
        people = [contact(f"{tag}{n}", f"Race{uid()[:4]}") for n in range(3)]
        return people, {p: [deal(p, f"{tag} deal {n}") for n in range(2)] for p in people}

    def concurrent_same_duplicate():
        for n in range(5):
            (x, y, z), deals = trio(f"Same{n}")
            with ThreadPoolExecutor(2) as ex:
                rs = list(ex.map(lambda keep: c.post(f"/contacts/{keep}/merge", json={"duplicateId": z}), [x, y]))
            codes = [r.status_code for r in rs]
            wins = [keep for keep, r in zip((x, y), rs) if r.status_code in (200, 201)]
            assert len(wins) == 1 and all(code in (200, 201) or 400 <= code < 500 for code in codes), f"statuses {codes}"
            loser = y if wins[0] == x else x
            for did in deals[z]:
                assert get(c, f"/deals/{did}").get("contactId") == wins[0], "duplicate's deal not on the winner"
            assert [e.get("duplicateId") for e in merges_of(c, wins[0])].count(z) == 1, "merge record missing or doubled"
            assert z not in [e.get("duplicateId") for e in merges_of(c, loser)], "the failed merge left a record"
    s.check("concurrent_merges_of_same_duplicate", concurrent_same_duplicate)

    def concurrent_chain():
        for n in range(5):
            (p, q, r), deals = trio(f"Chain{n}")
            with ThreadPoolExecutor(2) as ex:
                f1 = ex.submit(c.post, f"/contacts/{p}/merge", json={"duplicateId": q})
                f2 = ex.submit(c.post, f"/contacts/{q}/merge", json={"duplicateId": r})
                r1, r2 = f1.result(), f2.result()
            assert r1.status_code in (200, 201), f"P<-Q: HTTP {r1.status_code}"
            assert r2.status_code in (200, 201) or 400 <= r2.status_code < 500, f"Q<-R: HTTP {r2.status_code}"
            for x in (p, q, r):
                live = get_id(get(c, f"/contacts/{x}"))
                assert get_id(get(c, f"/contacts/{live}")) == live, "a merged id resolves to a merged-away contact"
            for did in [d for ds in deals.values() for d in ds]:
                owner = get(c, f"/deals/{did}").get("contactId")
                assert get_id(get(c, f"/contacts/{owner}")) == owner, "a deal is attached to a merged-away contact"
            merged_ok = 1 + (r2.status_code in (200, 201))
            assert len(merges_of(c, p)) == merged_ok, f"{len(merges_of(c, p))} merge records on P, expected {merged_ok}"
    s.check("concurrent_overlapping_merges", concurrent_chain)

    if seed:
        pairs = [(seed_by_key(seed, k), seed_by_key(seed, d)) for k, d in DUP_PAIRS]

        def seeded_merge():
            for keep, dup in pairs:
                if get_id(get(c, f"/contacts/{dup['id']}")) != keep["id"]:
                    ok(c.post(f"/contacts/{keep['id']}/merge", json={"duplicateId": dup["id"]}),
                       f"merge contact {dup['id']} into {keep['id']}")
            st["seeded_merged"] = True
        s.check("seeded_merge", seeded_merge)

        def seeded_state():
            s.need("seeded_merged")
            listed = id_set(listing(c, "/contacts", {"agencyId": "harbor-realty"}))
            for keep, dup in pairs:
                kp, dp = keep["payload"], dup["payload"]
                got = get(c, f"/contacts/{dup['id']}")
                assert get_id(got) == keep["id"], f"contact {dup['id']} doesn't return the kept contact {keep['id']}"
                assert dup["id"] not in listed, f"merged contact {dup['id']} still listed"
                want_e = [(email(kp["email"]), "work")] if kp.get("email") else []
                if dp.get("email") and email(dp["email"]) not in {a for a, _ in want_e}:
                    want_e.append((email(dp["email"]), "work"))
                want_p = [(phone(kp["phone"]), "work")] if kp.get("phone") else []
                if dp.get("phone") and phone(dp["phone"]) not in {n for n, _ in want_p}:
                    want_p.append((phone(dp["phone"]), "work"))
                assert (email_list(got), phone_list(got)) == (want_e, want_p), \
                    f"contact {keep['id']}: emails {got.get('emails')} phones {got.get('phones')}"
                for k in ("title", "companyId"):
                    assert got.get(k) == (kp.get(k) or dp.get(k)), f"contact {keep['id']}: {k} {got.get(k)!r}"
        s.check("seeded_merged_contacts", seeded_state)

        def seeded_deals_moved():
            s.need("seeded_merged")
            for keep, dup in pairs:
                for d in [d for d in seed["deals"] if d["payload"]["contactId"] == dup["id"]]:
                    assert get(c, f"/deals/{d['id']}").get("contactId") == keep["id"], f"deal {d['id']} still on the merged contact"
        s.check("seeded_merge_moves_deals", seeded_deals_moved)

        def seeded_history():
            s.need("seeded_merged")
            for keep, dup in pairs:
                entries = [e for e in merges_of(c, keep["id"]) if e.get("duplicateId") == dup["id"]]
                assert len(entries) == 1, f"contact {keep['id']}: {len(entries)} merge records for {dup['id']}"
                snap = entries[0].get("duplicate") or {}
                assert not fields_match({k: v for k, v in dup["payload"].items() if k != "agencyId"}, snap), \
                    f"merge of {dup['id']}: snapshot {str(snap)[:200]}"
        s.check("seeded_merge_history", seeded_history)

    return s.results

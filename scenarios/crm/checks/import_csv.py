"""Checks introduced by the `import` phase: CSV imports that create or fill in contacts."""
from __future__ import annotations

import time

import httpx

from harness.suite import Suite, get, get_id, listing, mk, ok
from scenarios.crm.checks.helpers import fresh_agency, person, phone_list

CSV = (
    "Email,FIRSTNAME,lastname,Phone,Title,Company,Tags,Notes\n"
    "eva@OLD.example,Eva,Changed,+351 900 000 002,Intern,Atlantic Tours,new;vip,x\n"
    'rui@sun.example,Rui,Sol,,"Head of Sales, Iberia","  atlantic tours ",guide,"say ""hi"""\n'
    "bad-email,Bad,Mail,,,,,\n"
    "lia@sun.example,Lia,,,,,,\n"
    ",Nina,Noemail,,,,,\n"
    'max@sun.example,Max,Lines,,"Line one\nLine two",New Horizons Ltd,,\n'
    "MAX@sun.example,Maximilian,Other,+351 911 111 111,Other,,b2b,\n"
    "rejectme@,X,Y,,,,,\n"
    "eva@old.example,Eva,Old,,,,,\n"
)
REJECTED = [4, 5, 6, 10]


def post_csv(c: httpx.Client, ag: str, text: str, timeout: float = 30) -> dict:
    r = c.post(f"/agencies/{ag}/imports", content=text.encode(), headers={"Content-Type": "text/csv"}, timeout=timeout)
    return ok(r, "import")


def v12(c: httpx.Client, seed: dict | None) -> dict:
    s = Suite("v12")
    st = s.state
    ag = fresh_agency(c, "sheet-imports")

    def run_import():
        existing = mk(c, "/companies", {"agencyId": ag, "name": "Existing Co"}, "company")
        atlantic = mk(c, "/companies", {"agencyId": ag, "name": "Atlantic Tours"}, "company")
        eva = mk(c, "/contacts", person(ag, "Eva", "Old", title="CEO", companyId=existing, tags=["vip"],
                                        emails=[{"address": "eva@old.example", "label": "work"}],
                                        phones=[{"number": "+351 900 000 001", "label": "work"}]), "contact")
        st.update(eva=eva, existing=existing, atlantic=atlantic)
        got = post_csv(c, ag, CSV)
        st["first"] = got
        counts = {k: got.get(k) for k in ("created", "updated", "unchanged")}
        assert counts == {"created": 2, "updated": 2, "unchanged": 1}, f"counts {counts}, expected 2 created, 2 updated, 1 unchanged"
    s.check("import_counts", run_import)

    def rejected_rows():
        rows = sorted(r.get("row") for r in (s.need("first").get("rejected") or []))
        assert rows == REJECTED, f"rejected rows {rows}, expected {REJECTED} (header is line 1)"
    s.check("rejected_rows_reported", rejected_rows)

    def by_email(address: str) -> dict:
        found = listing(c, "/contacts", {"agencyId": ag, "email": address})
        assert len(found) == 1, f"{len(found)} contacts with {address}"
        return found[0]

    def fills_in():
        eva = get(c, f"/contacts/{s.need('eva')}")
        assert (eva.get("firstName"), eva.get("lastName"), eva.get("title")) == ("Eva", "Old", "CEO"), \
            f"existing values overwritten: {eva.get('firstName')} {eva.get('lastName')}, {eva.get('title')!r}"
        phones = [p for p, _ in phone_list(eva)]
        assert phones == ["+351900000001", "+351900000002"], f"phones {phones}"
        companies = [x.get("companyId") for x in eva.get("companies") or []]
        assert companies == [s.need("existing"), s.need("atlantic")], f"companies {companies}"
        assert sorted(eva.get("tags") or []) == ["new", "vip"], f"tags {eva.get('tags')}"
    s.check("existing_contact_filled_in", fills_in)

    def creates():
        rui = by_email("rui@sun.example")
        assert (rui.get("firstName"), rui.get("lastName"), rui.get("title")) == ("Rui", "Sol", "Head of Sales, Iberia"), \
            f"rui: {rui.get('firstName')} {rui.get('lastName')}, {rui.get('title')!r}"
        assert rui.get("companyId") == s.need("atlantic"), "company not matched by name ignoring case and spaces"
        assert rui.get("tags") == ["guide"], f"rui's tags {rui.get('tags')}"
        mx = by_email("max@sun.example")
        assert (mx.get("firstName"), mx.get("lastName")) == ("Max", "Lines"), "the first row with an email should create"
        assert (mx.get("title") or "").replace("\r\n", "\n") == "Line one\nLine two", f"multi-line title {mx.get('title')!r}"
        assert [p for p, _ in phone_list(mx)] == ["+351911111111"] and mx.get("tags") == ["b2b"], \
            "a later row with the same email should fill in the contact"
        horizons = [co for co in listing(c, "/companies", {"agencyId": ag}) if co.get("name") == "New Horizons Ltd"]
        assert len(horizons) == 1 and mx.get("companyId") == get_id(horizons[0]), "new company not created once"
        assert not listing(c, "/contacts", {"agencyId": ag, "lastName": "Mail"}), "a rejected row was imported"
    s.check("new_contacts_and_companies", creates)

    def idempotent():
        before = len(listing(c, "/contacts", {"agencyId": ag}))
        got = post_csv(c, ag, CSV)
        counts = {k: got.get(k) for k in ("created", "updated", "unchanged")}
        assert counts == {"created": 0, "updated": 0, "unchanged": 5}, f"second import: {counts}"
        assert len(listing(c, "/contacts", {"agencyId": ag})) == before, "second import changed the number of contacts"
    s.check("reimport_changes_nothing", idempotent)

    def bulk():
        big = fresh_agency(c, "sheet-bulk")
        rows = ["firstName,lastName,email,company,tags"]
        rows += [f"First{i},Last{i},bulk{i}@bulk.example,Bulk Co {i % 50},bulk" for i in range(3000)]
        start = time.monotonic()
        got = post_csv(c, big, "\n".join(rows) + "\n", timeout=90)
        took = time.monotonic() - start
        assert got.get("created") == 3000, f"created {got.get('created')!r} of 3000"
        assert took <= 60, f"3,000 rows took {took:.0f} s"
        seg = ok(c.post(f"/agencies/{big}/segments", json={"name": "Bulk", "filter": {
            "field": "tags", "op": "has", "value": "bulk"}}), "segment")
        count = get(c, f"/segments/{get_id(seg)}/count").get("count")
        assert count == 3000, f"{count} imported contacts found, expected 3000"
        assert len(listing(c, "/companies", {"agencyId": big})) == 50, "companies not de-duplicated by name"
    s.check("bulk_import_3000_rows", bulk)
    return s.results

"""Deterministic "production" data, created through the app's own API after the initial build.

The same RNG seed is used for every run, so both database arms get identical data. A few records
have fixed keys because later checks use them: three pairs of duplicate contacts (merged in the
contact-merge phase) and a contact that an inbound lead is matched to.
"""
from __future__ import annotations

import datetime as dt
import random
import unicodedata
from typing import Any

import httpx

from harness.suite import get_id

SEED = 20261001

FIRST = ["Anna", "Marco", "Sofia", "Jonas", "Chloé", "Mateo", "Aisha", "Liam", "Yuki", "Oluwaseun",
         "Björn", "Zoë", "Siobhán", "Rajesh", "Élodie", "Tomasz", "Mei", "Diego", "Fatima", "Noah"]
LAST = ["Kowalski", "Rossi", "Müller", "O'Connor", "García", "Nakamura", "Okafor", "Lindqvist", "Dubois",
        "Haddad", "Smith", "Nguyen", "Fernández", "Brennan", "Kaur", "Novák", "Silva", "Andersen",
        "Takahashi", "Mensah"]

AGENCIES: dict[str, dict[str, Any]] = {
    "harbor-realty": {
        "companies": [("Sol Properties", "solproperties.es"), ("Costa Homes", "costahomes.pt"),
                      ("Nordhafen Immobilien GmbH", "nordhafen.de"), ("Blue Anchor Lettings", None),
                      ("O'Neill & Daughters", "oneill-daughters.ie"), ("Kensington Estates", "kensington.co.uk"),
                      ("Maison Lumière", "maisonlumiere.fr"), ("Harbourside Developments", None)],
        "titles": ["Buyer", "Investor", "Head of Acquisitions", "Property Manager", "Landlord", "CFO",
                   "Asset Manager", "Tenant Representative", "Director"],
        "deals": ["Harbour View apartments", "Old Town loft", "Office lease – 3rd floor", "Warehouse portfolio",
                  "Seafront villa", "Student housing block", "Retail unit 12", "Penthouse Nordhafen"],
        "contacts": 60, "n_deals": 55, "values": (50_000, 900_000, 5_000),
    },
    "talentbridge": {
        "companies": [("Acme Robotics", "acme-robotics.com"), ("Northwind Traders", "northwind.com"),
                      ("Brightpath Energy", "brightpath.energy"), ("Zeta Analytics", "zeta.ai"),
                      ("Quantum Loop", None), ("Meridian Health", "meridian-health.org"), ("Globex", "globex.com"),
                      ("Initech", "initech.com"), ("Umbrella Labs", None), ("Stark Logistics", "stark-log.com")],
        # Includes the edge cases of the seniority rule: substrings, "Sr.", upper case, precedence.
        "titles": ["Senior Team Lead", "Sr. Recruiter", "Junior Developer", "Head of Talent", "Principal Engineer",
                   "Office Manager", "SENIOR ACCOUNTANT", "Leadership Coach", "Jr. Analyst", "Teamleiter Vertrieb",
                   "Seniors Care Manager", "Data Engineer", "Lead", "Software Engineer", "Senior Designer",
                   "Product Owner", "junior accountant", "Engineering Manager", "Team-Lead Support", "Sales Rep"],
        "deals": ["Placement – backend engineer", "Retained search CFO", "Contract recruiters (3)",
                  "Graduate intake 2027", "Placement – data analyst", "Executive search COO"],
        "contacts": 70, "n_deals": 45, "values": (5_000, 40_000, 500),
    },
    "fete-events": {
        "companies": [("Hotel Miramar", "miramar.es"), ("Villa Aurora", "villa-aurora.it"),
                      ("Castle Rock Venues", None), ("Gala & Co", "galaandco.com"), ("Lakeside Pavilion", None),
                      ("Studio Nine", "studio9.events")],
        "titles": ["Event Manager", "Wedding Planner", "Bride", "Groom", "HR Director", "Office Assistant",
                   "Marketing Lead"],
        "deals": ["Summer gala", "Wedding – 120 guests", "Product launch", "Christmas party", "Board retreat",
                  "Conference catering"],
        "contacts": 40, "n_deals": 35, "values": (2_000, 80_000, 250),
    },
}

STAGES = ["lead", "qualified", "proposal", "won", "lost"]
STAGE_WEIGHTS = [20, 20, 20, 25, 15]


def fixed_contacts(company: dict[str, str]) -> dict[str, dict[str, Any]]:
    """Harbor Realty contacts that later checks refer to by key (company name -> id in `company`)."""
    return {
        "dup_case_keep": {"firstName": "Anna", "lastName": "Kowalski", "email": "anna.kowalski@solproperties.es",
                          "phone": "+34 611 000 101", "title": "Sales Director", "companyId": company["Sol Properties"]},
        "dup_case_dup": {"firstName": "Anna", "lastName": "Kowalski", "email": "Anna.Kowalski@SolProperties.es",
                         "companyId": company["Sol Properties"]},
        "dup_email_keep": {"firstName": "Marco", "lastName": "Rossi", "email": "marco.rossi@costahomes.pt",
                           "companyId": company["Costa Homes"]},
        "dup_email_dup": {"firstName": "Marco", "lastName": "Rossi", "email": "m.rossi@gmail.com",
                          "phone": "+39 347 000 2020"},
        "dup_phone_keep": {"firstName": "Chloé", "lastName": "Dubois", "email": "chloe.dubois@maisonlumiere.fr"},
        "dup_phone_dup": {"firstName": "Chloé", "lastName": "Dubois", "phone": "+33 6 12 34 56 78",
                          "title": "Acheteuse", "companyId": company["Maison Lumière"]},
        "lead_target": {"firstName": "Jonas", "lastName": "Lindqvist", "email": "jonas.lindqvist@nordhafen.de",
                        "companyId": company["Nordhafen Immobilien GmbH"]},
    }


def ascii_slug(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return "".join(ch for ch in folded.lower() if ch.isalnum())


def gen_contact(rng: random.Random, i: int, ag: str, spec: dict, companies: list[tuple[str, str | None, str]]) -> dict:
    first, last = rng.choice(FIRST), rng.choice(LAST)
    p: dict[str, Any] = {"firstName": first, "lastName": last}
    company = rng.choice(companies) if rng.random() < 0.8 else None
    if company:
        p["companyId"] = company[2]
    if rng.random() < 0.85:
        domain = company[1] if company and company[1] else rng.choice(["gmail.com", "outlook.com", "proton.me"])
        local = f"{ascii_slug(first)}.{ascii_slug(last)}{i}"
        p["email"] = f"{local.capitalize() if rng.random() < 0.15 else local}@{domain}"
    if rng.random() < 0.7:
        p["phone"] = f"+{rng.choice([34, 44, 49, 33, 351, 1])} {rng.randint(100, 999)} {rng.randint(100000, 999999)}"
    if rng.random() < 0.85:
        p["title"] = rng.choice(spec["titles"])
    return p


def gen_deal(rng: random.Random, spec: dict) -> dict:
    lo, hi, step = spec["values"]
    value: float = rng.randrange(lo, hi, step)
    if rng.random() < 0.1:
        value += 0.5
    stage = rng.choices(STAGES, STAGE_WEIGHTS)[0]
    d: dict[str, Any] = {"title": rng.choice(spec["deals"]), "value": value, "stage": stage}
    if stage in ("won", "lost") and rng.random() < 0.9:
        d["closeDate"] = (dt.date(2026, 1, 1) + dt.timedelta(days=rng.randint(0, 270))).isoformat()
    elif stage not in ("won", "lost") and rng.random() < 0.5:
        d["closeDate"] = (dt.date(2026, 10, 1) + dt.timedelta(days=rng.randint(0, 180))).isoformat()
    return d


def run(base_url: str) -> dict[str, Any]:
    rng = random.Random(SEED)
    out: dict[str, Any] = {"companies": [], "contacts": [], "deals": [], "failures": []}
    with httpx.Client(base_url=base_url, timeout=30) as c:

        def create(kind: str, key: str, ag: str, payload: dict) -> str | None:
            body = {"agencyId": ag, **payload}
            try:
                r = c.post(f"/{kind}", json=body)
                rid = get_id(r.json()) if r.status_code in (200, 201) else None
            except (httpx.HTTPError, ValueError) as e:
                r, rid = None, None
                out["failures"].append({"kind": kind, "key": key, "error": str(e)[:300]})
            if rid is None:
                if r is not None:
                    out["failures"].append({"kind": kind, "key": key, "status": r.status_code, "body": r.text[:300]})
                return None
            out[kind].append({"key": key, "agency": ag, "id": rid, "payload": body})
            return rid

        for ag, spec in AGENCIES.items():
            companies = []
            for n, (name, domain) in enumerate(spec["companies"]):
                payload = {"name": name, **({"domain": domain} if domain else {})}
                cid = create("companies", f"{ag}:co{n}", ag, payload)
                if cid:
                    companies.append((name, domain, cid))
            contact_ids = []
            if ag == "harbor-realty":
                by_name = {name: cid for name, _, cid in companies}
                for key, payload in fixed_contacts(by_name).items():
                    cid = create("contacts", key, ag, payload)
                    if cid:
                        # Every fixed contact gets deals; duplicates get two so the merge has something to move.
                        for _ in range(2 if key.endswith("_dup") else 1):
                            create("deals", f"{key}:deal{_}", ag, {"contactId": cid, **gen_deal(rng, spec)})
            for i in range(spec["contacts"]):
                cid = create("contacts", f"{ag}:c{i}", ag, gen_contact(rng, i, ag, spec, companies))
                if cid:
                    contact_ids.append(cid)
            for i in range(spec["n_deals"]):
                if contact_ids:
                    create("deals", f"{ag}:d{i}", ag, {"contactId": rng.choice(contact_ids), **gen_deal(rng, spec)})
    return out

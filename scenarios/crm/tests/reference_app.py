"""In-memory reference implementation of the CRM spec (all phases), used to test the checks.

It stores everything in the final shape from the start; the only data change that needs a step is
Talentbridge's seniority backfill, exposed as the test-only endpoint `/__backfill_seniority` (new deals
get their follow-up and currency when created, which is what the later backfills produce).

Deliberately wrong modes, to show the checks catch them:
- `stale_names`: contact and company names are copied into records when written and never updated
  (the classic denormalised-copy bug).
- `case_sensitive_emails`: email addresses are matched exactly.
- `search_any_word`: search finds records matching any word of the query (MongoDB's `$text` default).
- `frozen_rates`: a deal's value in euros is computed when the deal is written and never again.
"""
from __future__ import annotations

import copy
import csv
import datetime as dt
import hashlib
import hmac
import io
import json
import math
import re
import secrets
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qsl, urlparse

import httpx

from scenarios.crm.common import fold, seniority

STAGES = {"lead", "qualified", "proposal", "won", "lost"}
OPEN_STAGES = {"lead", "qualified", "proposal"}
LABELS = {"work", "home", "mobile", "other"}
TYPES = {"text", "number", "boolean", "date", "select"}
KEY = re.compile(r"^[A-Za-z0-9_]+$")
REPORT_KEYS = {"stage", "closeMonth", "companyId"}
SENIORITY = {"entity": "contact", "key": "seniority", "label": "Seniority", "type": "select",
             "options": ["junior", "mid", "senior", "lead"]}
FOLLOW_UP_DAYS = {"lead": 2, "qualified": 5, "proposal": 3}
ACTIVITY_FIELDS = {"call": {"durationMinutes", "outcome"}, "email": {"direction", "subject"},
                   "meeting": {"endsAt", "attendeeIds", "location"}, "note": {"text"}}
ACTIVITY_REQUIRED = {"call": {"durationMinutes", "outcome"}, "email": {"direction", "subject"},
                     "meeting": {"endsAt"}, "note": {"text"}}
OUTCOMES = {"connected", "no_answer", "voicemail"}
SEGMENT_TEXT = {"firstName", "lastName", "title", "email"}
COMPARE = {"eq": lambda a, b: a == b, "gt": lambda a, b: a > b, "gte": lambda a, b: a >= b,
           "lt": lambda a, b: a < b, "lte": lambda a, b: a <= b}
EVENTS = {"contact.created", "contact.updated", "contact.deleted", "deal.created", "deal.updated",
          "deal.stage_changed"}
IMPORT_COLUMNS = {"firstname", "lastname", "email", "phone", "title", "company", "tags"}


class Invalid(Exception):
    code = 400


class NotFound(Exception):
    code = 404


class Conflict(Exception):
    code = 409


def valid_date(v: Any) -> bool:
    if not isinstance(v, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        return False
    try:
        dt.date.fromisoformat(v)
        return True
    except ValueError:
        return False


def valid_email(v: Any) -> bool:
    return isinstance(v, str) and v.count("@") == 1 and all(v.split("@"))


def today() -> dt.date:
    return dt.datetime.now(dt.timezone.utc).date()


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def timestamp(v: Any) -> dt.datetime:
    if not isinstance(v, str) or "T" not in v:
        raise Invalid()
    try:
        t = dt.datetime.fromisoformat(v)
    except ValueError:
        raise Invalid() from None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def valid_location(v: Any) -> bool:
    return v is None or (isinstance(v, dict) and is_num(v.get("lat")) and is_num(v.get("lng"))
                         and -90 <= v["lat"] <= 90 and -180 <= v["lng"] <= 180)


def km_between(a: dict, b: dict) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a["lat"], a["lng"], b["lat"], b["lng"]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(h))


def inside(point: dict, area: list[list[float]]) -> bool:
    x, y, hit = point["lng"], point["lat"], False
    for (x1, y1), (x2, y2) in zip(area, area[1:] + area[:1]):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            hit = not hit
    return hit


def stems(text: Any) -> set[str]:
    words = re.findall(r"\w+", fold(text)) if isinstance(text, str) else []
    return {w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w for w in words}


def is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def digits(number: str) -> str:
    return re.sub(r"[^\d+]", "", number)


def valid_value(f: dict, v: Any) -> bool:
    t = f["type"]
    return {"text": isinstance(v, str), "number": is_num(v), "boolean": isinstance(v, bool),
            "date": valid_date(v), "select": v in (f.get("options") or [])}[t]


def value_matches(f: dict, stored: Any, raw: str) -> bool:
    if stored is None:
        return False
    if f["type"] == "number":
        try:
            return is_num(stored) and float(stored) == float(raw)
        except ValueError:
            return False
    if f["type"] == "boolean":
        return raw in ("true", "false") and stored == (raw == "true")
    return stored == raw


def values_at(node: Any, parts: list[str]) -> list:
    if isinstance(node, list):
        return [v for item in node for v in values_at(item, parts)]
    if not parts:
        return [node]
    if isinstance(node, dict) and parts[0] in node:
        return values_at(node[parts[0]], parts[1:])
    return []


def payload_matches(payload: Any, path: str, raw: str) -> bool:
    for v in values_at(payload, path.split(".")):
        if (isinstance(v, str) and v == raw) or (isinstance(v, (bool, int, float)) and json.dumps(v) == raw):
            return True
    return False


class State:
    def __init__(self, stale_names: bool, case_sensitive_emails: bool, search_any_word: bool, frozen_rates: bool):
        self.companies: dict[str, dict] = {}
        self.contacts: dict[str, dict] = {}
        self.deals: dict[str, dict] = {}
        self.fields: dict[str, list[dict]] = {}
        self.leads: dict[str, dict] = {}
        self.lead_keys: dict[tuple[str, str], str] = {}
        self.merges: dict[str, list[dict]] = {}
        self.activities: dict[str, dict] = {}
        self.segments: dict[str, dict] = {}
        self.rates: dict[str, dict[str, dict[str, float]]] = {}
        self.territories: dict[str, dict] = {}
        self.subscriptions: dict[str, dict] = {}
        self.outbox: list[dict] = []
        self.lock = threading.RLock()
        self.stale = stale_names
        self.ci = not case_sensitive_emails
        self.any_word = search_any_word
        self.frozen = frozen_rates

    # ------------------------------------------------------------ helpers

    def same_email(self, a: str, b: str) -> bool:
        return a.lower() == b.lower() if self.ci else a == b

    def defs(self, ag: str, entity: str) -> dict[str, dict]:
        return {f["key"]: f for f in self.fields.get(ag, []) if f["entity"] == entity}

    def resolve(self, cid: str) -> dict:
        ct = self.contacts.get(cid)
        while ct and ct.get("mergedInto"):
            ct = self.contacts.get(ct["mergedInto"])
        if not ct or ct.get("deletedAt"):
            raise NotFound()
        return ct

    def live(self, ag: str) -> list[dict]:
        return [c for c in self.contacts.values()
                if c["agencyId"] == ag and not c.get("mergedInto") and not c.get("deletedAt")]

    def visible(self, record: dict) -> bool:
        """A deal, activity or lead whose contact isn't in the trash."""
        return not self.contacts[record["contactId"]].get("deletedAt")

    def company_by_name(self, ag: str, name: str) -> dict:
        name = name.strip()
        return next((co for co in self.companies.values() if co["agencyId"] == ag
                     and co["name"].strip().lower() == name.lower()), None) or self.new_company(ag, name)

    def company_entry(self, ag: str, entry: Any) -> dict:
        if not isinstance(entry, dict):
            raise Invalid()
        cid, role = entry.get("companyId"), entry.get("role")
        co = self.companies.get(cid) if isinstance(cid, str) else None
        if not co or co["agencyId"] != ag or (role is not None and not isinstance(role, str)):
            raise Invalid()
        return {"companyId": cid, "role": role, "_name": co["name"]}

    # ------------------------------------------------------------ views

    def contact_view(self, ct: dict) -> dict:
        comps = [{"companyId": x["companyId"], "role": x["role"],
                  "companyName": x["_name"] if self.stale else self.companies[x["companyId"]]["name"]}
                 for x in ct["companies"]]
        first = comps[0] if comps else {}
        open_deals = [d for d in self.deals.values() if d["contactId"] == ct["id"] and d["stage"] in OPEN_STAGES]
        return {"id": ct["id"], "agencyId": ct["agencyId"], "firstName": ct["firstName"], "lastName": ct["lastName"],
                "title": ct.get("title"), "email": ct["emails"][0]["address"] if ct["emails"] else None,
                "phone": ct["phones"][0]["number"] if ct["phones"] else None,
                "emails": copy.deepcopy(ct["emails"]), "phones": copy.deepcopy(ct["phones"]),
                "companyId": first.get("companyId"), "companyName": first.get("companyName"),
                "companies": comps, "custom": dict(ct["custom"]), "tags": list(ct["tags"]),
                "location": ct["location"], "lastActivityAt": next(iter(self.timeline(ct["id"])), {}).get("at"),
                "openPipeline": round(sum(self.value_eur(d) for d in open_deals), 2),
                "nextFollowUpOn": min((d["followUpOn"] for d in open_deals if d["followUpOn"]), default=None)}

    def deal_company(self, d: dict) -> tuple[str | None, str | None]:
        if self.stale:
            return d["_companyId"], d["_companyName"]
        ct = self.contacts[d["contactId"]]
        if not ct["companies"]:
            return None, None
        cid = ct["companies"][0]["companyId"]
        return cid, self.companies[cid]["name"]

    def deal_view(self, d: dict) -> dict:
        ct = self.contacts[d["contactId"]]
        company_id, company_name = self.deal_company(d)
        return {"id": d["id"], "agencyId": d["agencyId"], "contactId": d["contactId"],
                "contactName": d["_contactName"] if self.stale else f"{ct['firstName']} {ct['lastName']}",
                "companyId": company_id, "companyName": company_name, "title": d["title"], "value": d["value"],
                "stage": d["stage"], "closeDate": d.get("closeDate"), "custom": dict(d["custom"]),
                "currency": d["currency"], "valueEur": self.value_eur(d), "followUpOn": d["followUpOn"],
                "followUpOverdue": d["stage"] in OPEN_STAGES and bool(d["followUpOn"])
                and d["followUpOn"] < today().isoformat()}

    def value_eur(self, d: dict) -> float:
        if self.frozen and "_valueEur" in d:
            return d["_valueEur"]
        if d["currency"] == "EUR":
            return d["value"]
        dated = sorted((day, r[d["currency"]]) for day, r in self.rates.get(d["agencyId"], {}).items() if d["currency"] in r)
        if not dated:
            return d["value"]
        on = d.get("closeDate") or today().isoformat()
        rate = ([r for day, r in dated if day <= on] or [dated[0][1]])[-1]
        return round(d["value"] / rate, 2)

    def snapshot_deal(self, d: dict) -> None:
        ct = self.contacts[d["contactId"]]
        d["_contactName"] = f"{ct['firstName']} {ct['lastName']}"
        d["_companyId"] = ct["companies"][0]["companyId"] if ct["companies"] else None
        d["_companyName"] = ct["companies"][0]["_name"] if ct["companies"] else None

    # ------------------------------------------------------------ writes

    def apply_custom(self, ag: str, entity: str, target: dict, custom: Any, creating: bool) -> None:
        if not isinstance(custom, dict):
            raise Invalid()
        defs = self.defs(ag, entity)
        for k, v in custom.items():
            if k not in defs or (v is not None and not valid_value(defs[k], v)):
                raise Invalid()
        for k, v in custom.items():
            if v is None:
                target.pop(k, None)
            else:
                target[k] = v

    def apply_contact(self, ag: str, ct: dict, b: dict, creating: bool) -> dict:
        new = copy.deepcopy(ct)
        for k in ("firstName", "lastName"):
            if k in b or creating:
                if not isinstance(b.get(k), str) or not b[k]:
                    raise Invalid()
                new[k] = b[k]
        if "title" in b:
            if b["title"] is not None and not isinstance(b["title"], str):
                raise Invalid()
            new["title"] = b["title"]
        for single, many, field in (("email", "emails", "address"), ("phone", "phones", "number")):
            valid = valid_email if field == "address" else (lambda v: isinstance(v, str) and bool(v.strip()))
            if single in b and many in b:
                raise Invalid()
            if many in b:
                lst = b[many]
                if not isinstance(lst, list) or not all(
                        isinstance(e, dict) and valid(e.get(field)) and e.get("label") in LABELS for e in lst):
                    raise Invalid()
                new[many] = [{field: e[field], "label": e["label"]} for e in lst]
            elif single in b:
                v = b[single]
                if v is None:
                    new[many] = new[many][1:]
                elif not valid(v):
                    raise Invalid()
                elif new[many]:
                    new[many][0] = {**new[many][0], field: v}
                else:
                    new[many] = [{field: v, "label": "work"}]
        if "companyId" in b and "companies" in b:
            raise Invalid()
        if "companies" in b:
            if not isinstance(b["companies"], list):
                raise Invalid()
            entries = [self.company_entry(ag, e) for e in b["companies"]]
            if len({e["companyId"] for e in entries}) != len(entries):
                raise Invalid()
            new["companies"] = entries
        elif "companyId" in b:
            new["companies"] = [] if b["companyId"] is None else [self.company_entry(ag, {"companyId": b["companyId"]})]
        if "custom" in b:
            self.apply_custom(ag, "contact", new["custom"], b["custom"], creating)
        if "tags" in b:
            tags = b["tags"]
            if not isinstance(tags, list) or not all(isinstance(t, str) and 0 < len(t) <= 40 for t in tags):
                raise Invalid()
            new["tags"] = list(dict.fromkeys(t.lower() for t in tags))
        if "location" in b:
            if not valid_location(b["location"]):
                raise Invalid()
            new["location"] = b["location"] and {"lat": b["location"]["lat"], "lng": b["location"]["lng"]}
        return new

    def apply_deal(self, ag: str, d: dict, b: dict, creating: bool) -> dict:
        new = copy.deepcopy(d)
        if creating:
            cid = b.get("contactId")
            if not isinstance(cid, str) or cid not in self.contacts:
                raise Invalid()
            try:
                ct = self.resolve(cid)
            except NotFound:
                raise Invalid() from None
            if ct["agencyId"] != ag:
                raise Invalid()
            new["contactId"] = ct["id"]
        if "title" in b or creating:
            if not isinstance(b.get("title"), str) or not b["title"]:
                raise Invalid()
            new["title"] = b["title"]
        if "value" in b or creating:
            if not is_num(b.get("value")) or b["value"] < 0:
                raise Invalid()
            new["value"] = b["value"]
        if "stage" in b or creating:
            if b.get("stage") not in STAGES:
                raise Invalid()
            new["stage"] = b["stage"]
        if "closeDate" in b:
            if b["closeDate"] is not None and not valid_date(b["closeDate"]):
                raise Invalid()
            new["closeDate"] = b["closeDate"]
        if "custom" in b:
            self.apply_custom(ag, "deal", new["custom"], b["custom"], creating)
        if "currency" in b:
            if b["currency"] != "EUR" and b["currency"] not in {c for r in self.rates.get(ag, {}).values() for c in r}:
                raise Invalid()
            new["currency"] = b["currency"]
        if "followUpOn" in b:
            if b["followUpOn"] is not None and not valid_date(b["followUpOn"]):
                raise Invalid()
            new["followUpOn"] = b["followUpOn"]
        elif new["stage"] != d.get("stage"):
            days = FOLLOW_UP_DAYS.get(new["stage"])
            new["followUpOn"] = (today() + dt.timedelta(days=days)).isoformat() if days else None
        if self.frozen:
            new.pop("_valueEur", None)
            new["_valueEur"] = self.value_eur(new)
        return new

    def new_contact(self, ag: str, b: dict) -> dict:
        ct = self.apply_contact(ag, {"id": uuid.uuid4().hex, "agencyId": ag, "emails": [], "phones": [],
                                     "companies": [], "custom": {}, "title": None, "tags": [], "location": None},
                                b, creating=True)
        self.contacts[ct["id"]] = ct
        self.emit("contact.created", ct)
        return ct

    def new_company(self, ag: str, name: str, domain: Any = None, location: Any = None) -> dict:
        co = {"id": uuid.uuid4().hex, "agencyId": ag, "name": name, "domain": domain, "location": location}
        self.companies[co["id"]] = co
        return co

    # ------------------------------------------------------------ filters

    def contact_filter(self, q: dict) -> list[dict]:
        ag = q.get("agencyId")
        defs = self.defs(ag, "contact")
        custom = {k[7:]: v for k, v in q.items() if k.startswith("custom.")}
        if any(k not in defs for k in custom):
            raise Invalid()
        out = []
        for ct in self.live(ag):
            if "email" in q and not any(self.same_email(e["address"], q["email"]) for e in ct["emails"]):
                continue
            if "phone" in q and not any(digits(p["number"]) == digits(q["phone"]) for p in ct["phones"]):
                continue
            if "lastName" in q and fold(ct["lastName"]) != fold(q["lastName"]):
                continue
            if "companyId" in q and q["companyId"] not in {x["companyId"] for x in ct["companies"]}:
                continue
            if "tag" in q and q["tag"].lower() not in ct["tags"]:
                continue
            if not all(value_matches(defs[k], ct["custom"].get(k), v) for k, v in custom.items()):
                continue
            out.append(ct)
        return out

    def deal_filter(self, q: dict) -> list[dict]:
        ag = q.get("agencyId")
        defs = self.defs(ag, "deal")
        custom = {k[7:]: v for k, v in q.items() if k.startswith("custom.")}
        if any(k not in defs for k in custom):
            raise Invalid()
        if "followUpBefore" in q and not valid_date(q["followUpBefore"]):
            raise Invalid()
        if q.get("overdue", "true") not in ("true", "false"):
            raise Invalid()
        out = []
        for d in self.deals.values():
            if d["agencyId"] != ag or not self.visible(d):
                continue
            if "stage" in q and d["stage"] != q["stage"]:
                continue
            if "contactId" in q and d["contactId"] != q["contactId"]:
                continue
            if "companyId" in q and self.deal_company(d)[0] != q["companyId"]:
                continue
            if "currency" in q and d["currency"] != q["currency"]:
                continue
            if "followUpBefore" in q and not (d["followUpOn"] and d["followUpOn"] < q["followUpBefore"]):
                continue
            if "overdue" in q and self.deal_view(d)["followUpOverdue"] != (q["overdue"] == "true"):
                continue
            if not all(value_matches(defs[k], d["custom"].get(k), v) for k, v in custom.items()):
                continue
            out.append(d)
        return out

    # ------------------------------------------------------------ leads

    def parse_lead(self, provider: str, b: dict) -> dict | None:
        if provider == "formflow":
            if b.get("event") != "lead.created":
                return None
            f = b.get("fields") or {}
            words = (f.get("name") or "").split()
            first, last = (" ".join(words[:-1]) or words[-1], words[-1]) if words else ("", "")
            ext, ag = b.get("id"), b.get("agency")
            emails, phones, company = [f["email"]] if f.get("email") else [], [f["phone"]] if f.get("phone") else [], f.get("company")
        elif b.get("version") == 2:
            if b.get("event") != "lead.new":
                return None
            lead = b.get("lead") or {}
            ct = lead.get("contact") or {}
            addrs = ct.get("email_addresses") or []
            emails = [a["value"] for a in addrs if a.get("primary")] + [a["value"] for a in addrs if not a.get("primary")]
            phones = [p["value"] for p in ct.get("phone_numbers") or []]
            first, last = (ct.get("name") or {}).get("first"), (ct.get("name") or {}).get("last")
            ext, ag, company = lead.get("id"), lead.get("account_id"), (lead.get("organization") or {}).get("name")
        else:
            if b.get("type") != "new_lead":
                return None
            p = b.get("person") or {}
            emails, phones = p.get("emails") or [], p.get("phones") or []
            first, last = p.get("first_name"), p.get("last_name")
            ext, ag, company = b.get("lead_id"), b.get("account"), (b.get("organization") or {}).get("name")
        if ext is None or not ag:
            raise Invalid()
        return {"ext": str(ext), "agencyId": ag, "emails": emails, "phones": phones, "first": first, "last": last,
                "company": company}

    def webhook(self, provider: str, b: dict) -> tuple[int, Any]:
        lead = self.parse_lead(provider, b)
        if lead is None:
            return 204, None
        key = (provider, lead["ext"])
        if key in self.lead_keys:
            return 200, self.leads[self.lead_keys[key]]
        ag = lead["agencyId"]
        contact = next((ct for addr in lead["emails"] for ct in self.live(ag)
                        if any(self.same_email(e["address"], addr) for e in ct["emails"])), None)
        if contact:
            for addr in lead["emails"]:
                if not any(self.same_email(e["address"], addr) for e in contact["emails"]):
                    contact["emails"].append({"address": addr, "label": "other"})
            for number in lead["phones"]:
                if not any(digits(p["number"]) == digits(number) for p in contact["phones"]):
                    contact["phones"].append({"number": number, "label": "other"})
            self.emit("contact.updated", contact)
        else:
            company = None
            if lead["company"] and lead["company"].strip():
                company = self.company_by_name(ag, lead["company"])
            body: dict[str, Any] = {"firstName": lead["first"] or "?", "lastName": lead["last"] or "?",
                                    "emails": [{"address": a, "label": "other"} for a in lead["emails"]],
                                    "phones": [{"number": n, "label": "other"} for n in lead["phones"]]}
            if company:
                body["companyId"] = company["id"]
            contact = self.new_contact(ag, body)
        view = {"id": uuid.uuid4().hex, "agencyId": ag, "provider": provider, "externalId": lead["ext"],
                "contactId": contact["id"], "payload": b}
        self.leads[view["id"]] = view
        self.lead_keys[key] = view["id"]
        return 201, view

    # ------------------------------------------------------------ merge and reports

    def merge(self, keep_id: str, b: dict) -> dict:
        keep, dup = self.contacts.get(keep_id), self.contacts.get(b.get("duplicateId"))
        if not keep or not dup or keep.get("deletedAt") or dup.get("deletedAt"):
            raise NotFound()
        if keep.get("mergedInto") or dup.get("mergedInto"):
            raise Conflict()
        if keep is dup or keep["agencyId"] != dup["agencyId"]:
            raise Invalid()
        snapshot = self.contact_view(dup)
        keep["title"] = keep.get("title") or dup.get("title")
        for e in dup["emails"]:
            if not any(self.same_email(e["address"], x["address"]) for x in keep["emails"]):
                keep["emails"].append(dict(e))
        for p in dup["phones"]:
            if not any(digits(p["number"]) == digits(x["number"]) for x in keep["phones"]):
                keep["phones"].append(dict(p))
        for x in dup["companies"]:
            if x["companyId"] not in {y["companyId"] for y in keep["companies"]}:
                keep["companies"].append(dict(x))
        for k, v in dup["custom"].items():
            keep["custom"].setdefault(k, v)
        keep["tags"] += [t for t in dup["tags"] if t not in keep["tags"]]
        for d in self.deals.values():
            if d["contactId"] == dup["id"]:
                d["contactId"] = keep["id"]
        for lead in self.leads.values():
            if lead["contactId"] == dup["id"]:
                lead["contactId"] = keep["id"]
        for a in self.activities.values():
            if a["contactId"] == dup["id"]:
                a["contactId"] = keep["id"]
            if "attendeeIds" in a:
                ids = [keep["id"] if x == dup["id"] else x for x in a["attendeeIds"]]
                a["attendeeIds"] = [x for x in dict.fromkeys(ids) if x != a["contactId"]]
        dup["mergedInto"] = keep["id"]
        self.merges.setdefault(keep["id"], []).append({
            "duplicateId": dup["id"], "mergedAt": dt.datetime.now(dt.timezone.utc).isoformat(), "duplicate": snapshot})
        self.merges[keep["id"]].extend(self.merges.pop(dup["id"], []))
        self.emit("contact.updated", keep)
        return self.contact_view(keep)

    def report(self, q: dict) -> dict:
        ag, group_by = q.get("agencyId"), [k for k in (q.get("groupBy") or "").split(",") if k]
        if not ag or not group_by:
            raise Invalid()
        deal_defs, contact_defs = self.defs(ag, "deal"), self.defs(ag, "contact")
        for k in group_by:
            if not (k in REPORT_KEYS or (k.startswith("custom.") and k[7:] in deal_defs)
                    or (k.startswith("contact.custom.") and k[15:] in contact_defs)):
                raise Invalid()
        groups: dict[tuple, dict] = {}
        for d in self.deal_filter(q):
            ct = self.contacts[d["contactId"]]
            values = {"stage": d["stage"], "closeMonth": (d.get("closeDate") or "")[:7] or None,
                      "companyId": self.deal_company(d)[0]}
            key = tuple(values[k] if k in values else
                        (d["custom"].get(k[7:]) if k.startswith("custom.") else ct["custom"].get(k[15:]))
                        for k in group_by)
            g = groups.setdefault(key, {"count": 0, "value": 0, "won": 0, "closed": 0})
            g["count"] += 1
            g["value"] += self.value_eur(d)
            if d["stage"] in ("won", "lost"):
                g["closed"] += 1
                g["won"] += d["stage"] == "won"
        return {"groups": [{**dict(zip(group_by, key)), "count": g["count"], "value": round(g["value"], 2),
                            "winRate": g["won"] / g["closed"] if g["closed"] else None} for key, g in groups.items()]}

    def backfill_seniority(self) -> None:
        if "seniority" not in self.defs("talentbridge", "contact"):
            self.fields.setdefault("talentbridge", []).append(dict(SENIORITY))
        for ct in self.live("talentbridge"):
            value = seniority(ct.get("title"))
            if value:
                ct["custom"]["seniority"] = value

    # ------------------------------------------------------------ activities

    def apply_activity(self, ag: str, a: dict, b: dict, creating: bool) -> dict:
        new = copy.deepcopy(a)
        allowed = ACTIVITY_FIELDS[new["type"]] | {"at"} | ({"agencyId", "type", "contactId"} if creating else set())
        if set(b) - allowed:
            raise Invalid()
        if creating and (ACTIVITY_REQUIRED[new["type"]] | {"at"}) - {k for k, v in b.items() if v is not None}:
            raise Invalid()
        for k, v in b.items():
            if k in ("agencyId", "type", "contactId"):
                continue
            if k in ("at", "endsAt"):
                timestamp(v)
            elif k == "durationMinutes" and not (isinstance(v, int) and not isinstance(v, bool) and v >= 0):
                raise Invalid()
            elif k == "outcome" and v not in OUTCOMES:
                raise Invalid()
            elif k == "direction" and v not in ("in", "out"):
                raise Invalid()
            elif k in ("subject", "text") and not (isinstance(v, str) and v.strip()):
                raise Invalid()
            elif k == "location" and v is not None and not isinstance(v, str):
                raise Invalid()
            elif k == "attendeeIds":
                live = {ct["id"] for ct in self.live(ag)}
                if (not isinstance(v, list) or len(set(map(str, v))) != len(v) or new["contactId"] in v
                        or not all(isinstance(x, str) and x in live for x in v)):
                    raise Invalid()
            if k == "location" and v is None:
                new.pop(k, None)
            else:
                new[k] = v
        if new["type"] == "meeting" and timestamp(new["endsAt"]) <= timestamp(new["at"]):
            raise Invalid()
        return new

    def new_activity(self, b: dict) -> dict:
        ag, typ, cid = b.get("agencyId"), b.get("type"), b.get("contactId")
        if typ not in ACTIVITY_FIELDS or not isinstance(cid, str) or cid not in {ct["id"] for ct in self.live(ag)}:
            raise Invalid()
        a = self.apply_activity(ag, {"id": uuid.uuid4().hex, "agencyId": ag, "type": typ, "contactId": cid}, b, True)
        if typ == "meeting":
            a.setdefault("attendeeIds", [])
        self.activities[a["id"]] = a
        return a

    def activity(self, aid: str) -> dict:
        a = self.activities.get(aid)
        if not a or not self.visible(a):
            raise NotFound()
        return a

    def activity_view(self, a: dict) -> dict:
        view = copy.deepcopy(a)
        if "attendeeIds" in view:
            view["attendeeIds"] = [x for x in view["attendeeIds"] if not self.contacts[x].get("deletedAt")]
        return view

    def timeline(self, cid: str) -> list[dict]:
        out = [self.activity_view(a) for a in self.activities.values() if self.visible(a)
               and (a["contactId"] == cid or cid in a.get("attendeeIds", []))]
        return sorted(out, key=lambda a: timestamp(a["at"]), reverse=True)

    def activity_filter(self, q: dict) -> list[dict]:
        ag = q.get("agencyId")
        lo = timestamp(q["from"]) if "from" in q else None
        hi = timestamp(q["to"]) if "to" in q else None
        out = []
        for a in self.activities.values():
            if a["agencyId"] != ag or not self.visible(a):
                continue
            if "contactId" in q and q["contactId"] != a["contactId"] and q["contactId"] not in a.get("attendeeIds", []):
                continue
            if any(k in q and str(a.get(k)) != q[k] for k in ("type", "outcome", "direction")):
                continue
            if "minDurationMinutes" in q and not a.get("durationMinutes", -1) >= float(q["minDurationMinutes"]):
                continue
            t = timestamp(a["at"])
            if (lo and t < lo) or (hi and t > hi):
                continue
            out.append(self.activity_view(a))
        return sorted(out, key=lambda a: timestamp(a["at"]), reverse=True)

    # ------------------------------------------------------------ segments

    def check_condition(self, ag: str, c: Any) -> None:
        if isinstance(c, dict) and len(c) == 1 and next(iter(c)) in ("all", "any"):
            items = next(iter(c.values()))
            if not isinstance(items, list) or not items:
                raise Invalid()
            for x in items:
                self.check_condition(ag, x)
            return
        if isinstance(c, dict) and set(c) == {"not"}:
            return self.check_condition(ag, c["not"])
        if not isinstance(c, dict) or set(c) != {"field", "op", "value"} or not isinstance(c["field"], str):
            raise Invalid()
        f, op, v = c["field"], c["op"], c["value"]
        if f == "tags":
            good = op == "has" and isinstance(v, str) and bool(v)
        elif f in SEGMENT_TEXT:
            good = op in ("eq", "contains") and isinstance(v, str)
        elif f == "companyId":
            good = op == "eq" and isinstance(v, str)
        elif f == "openPipeline":
            good = op in COMPARE and is_num(v)
        elif f == "lastActivityAt":
            good = op in ("before", "after") and bool(timestamp(v))
        elif f.startswith("custom.") and f[7:] in self.defs(ag, "contact"):
            d = self.defs(ag, "contact")[f[7:]]
            good = (isinstance(v, bool) if op == "exists" else
                    op in COMPARE and (op == "eq" or d["type"] in ("number", "date")) and valid_value(d, v))
        else:
            good = False
        if not good:
            raise Invalid()

    def matches(self, c: dict, view: dict) -> bool:
        if "all" in c:
            return all(self.matches(x, view) for x in c["all"])
        if "any" in c:
            return any(self.matches(x, view) for x in c["any"])
        if "not" in c:
            return not self.matches(c["not"], view)
        f, op, v = c["field"], c["op"], c["value"]
        if f == "tags":
            return v.lower() in view["tags"]
        if f in SEGMENT_TEXT:
            values = [e["address"] for e in view["emails"]] if f == "email" else [view.get(f)]
            return any(isinstance(x, str) and (x.lower() == v.lower() if op == "eq" else v.lower() in x.lower())
                       for x in values)
        if f == "companyId":
            return v in {x["companyId"] for x in view["companies"]}
        if f == "lastActivityAt":
            at = view["lastActivityAt"]
            return at is not None and (timestamp(at) < timestamp(v) if op == "before" else timestamp(at) > timestamp(v))
        got = view["openPipeline"] if f == "openPipeline" else view["custom"].get(f[7:])
        if op == "exists":
            return (got is not None) == v
        return got is not None and COMPARE[op](got, v)

    def segment(self, sid: str) -> dict:
        if sid not in self.segments:
            raise NotFound()
        return self.segments[sid]

    def segment_contacts(self, sid: str) -> list[dict]:
        seg = self.segment(sid)
        views = [self.contact_view(ct) for ct in self.live(seg["agencyId"])]
        return sorted((v for v in views if self.matches(seg["filter"], v)), key=lambda v: (v["lastName"], v["firstName"]))

    # ------------------------------------------------------------ search

    def search(self, q: dict) -> list[dict]:
        ag, text = q.get("agencyId"), (q.get("q") or "").strip()
        if not text:
            raise Invalid()
        limit = min(int(q.get("limit", 20)), 100)
        if valid_email(text) and " " not in text:
            return [{"kind": "contact", "id": ct["id"], "title": f"{ct['firstName']} {ct['lastName']}"}
                    for ct in self.live(ag) if any(self.same_email(e["address"], text) for e in ct["emails"])][:limit]
        want = stems(text)
        found = (any if self.any_word else all)
        records = []
        for ct in self.live(ag):
            view = self.contact_view(ct)
            name = f"{ct['firstName']} {ct['lastName']}"
            notes = [a.get("subject") or a.get("text") for a in self.activities.values()
                     if a["contactId"] == ct["id"] and a["type"] in ("email", "note")]
            other = [view["title"], *(e["address"] for e in ct["emails"]), *(x["companyName"] for x in view["companies"]), *notes]
            records.append(("contact", ct["id"], name, other))
        records += [("company", co["id"], co["name"], [co.get("domain")]) for co in self.companies.values()
                    if co["agencyId"] == ag]
        records += [("deal", d["id"], d["title"], [self.deal_view(d)["contactName"]]) for d in self.deals.values()
                    if d["agencyId"] == ag and self.visible(d)]
        hits = []
        for kind, rid, title, other in records:
            own = stems(title)
            every = own.union(*(stems(x) for x in other))
            if found(w in every for w in want):
                hits.append((0 if all(w in own for w in want) else 1, {"kind": kind, "id": rid, "title": title}))
        return [h for _, h in sorted(hits, key=lambda h: h[0])][:limit]

    # ------------------------------------------------------------ locations

    def point(self, ct: dict) -> dict | None:
        if ct["location"]:
            return ct["location"]
        return self.companies[ct["companies"][0]["companyId"]]["location"] if ct["companies"] else None

    def territory_contacts(self, tid: str) -> list[dict]:
        t = self.territories.get(tid)
        if not t:
            raise NotFound()
        return [ct for ct in self.live(t["agencyId"]) if (p := self.point(ct)) and inside(p, t["area"])]

    # ------------------------------------------------------------ import

    def import_csv(self, ag: str, text: str) -> dict:
        reader = csv.reader(io.StringIO(text, newline=""))
        header = next(reader, None)
        if not header:
            raise Invalid()
        cols = {h.strip().lower(): i for i, h in enumerate(header) if h.strip().lower() in IMPORT_COLUMNS}
        out: dict[str, Any] = {"created": 0, "updated": 0, "unchanged": 0, "rejected": []}
        key = (lambda m: m.lower()) if self.ci else (lambda m: m)
        by_email = {key(e["address"]): ct for ct in self.live(ag) for e in reversed(ct["emails"])}
        line = reader.line_num
        for row in reader:
            start, line = line + 1, reader.line_num
            if not any(cell.strip() for cell in row):
                continue

            def cell(name: str) -> str:
                return row[cols[name]].strip() if name in cols and cols[name] < len(row) else ""

            first, last, mail = cell("firstname"), cell("lastname"), cell("email")
            if not first or not last or not valid_email(mail):
                out["rejected"].append({"row": start, "error": "missing name" if not first or not last else "invalid email"})
                continue
            title, number, company = cell("title"), cell("phone"), cell("company")
            tags = [t.strip().lower() for t in cell("tags").split(";") if t.strip()]
            ct = by_email.get(key(mail))
            if ct is None:
                body: dict[str, Any] = {"firstName": first, "lastName": last, "emails": [{"address": mail, "label": "work"}],
                                        "tags": tags, **({"title": title} if title else {})}
                if number:
                    body["phones"] = [{"number": number, "label": "work"}]
                if company:
                    body["companyId"] = self.company_by_name(ag, company)["id"]
                by_email[key(mail)] = self.new_contact(ag, body)
                out["created"] += 1
                continue
            before = copy.deepcopy(ct)
            if title and not ct.get("title"):
                ct["title"] = title
            if number and not any(digits(p["number"]) == digits(number) for p in ct["phones"]):
                ct["phones"].append({"number": number, "label": "work"})
            if company:
                co = self.company_by_name(ag, company)
                if co["id"] not in {x["companyId"] for x in ct["companies"]}:
                    ct["companies"].append({"companyId": co["id"], "role": None, "_name": co["name"]})
            ct["tags"] += [t for t in dict.fromkeys(tags) if t not in ct["tags"]]
            changed = ct != before
            out["updated" if changed else "unchanged"] += 1
            if changed:
                self.emit("contact.updated", ct)
        return out

    # ------------------------------------------------------------ trash

    def trash_entry(self, ct: dict) -> dict:
        return {"id": ct["id"], "firstName": ct["firstName"], "lastName": ct["lastName"], "deletedAt": ct["deletedAt"],
                "deals": sum(d["contactId"] == ct["id"] for d in self.deals.values()),
                "activities": sum(a["contactId"] == ct["id"] for a in self.activities.values())}

    def trashed(self, cid: str) -> dict:
        ct = self.contacts.get(cid)
        if not ct or not ct.get("deletedAt"):
            raise NotFound()
        return ct

    def purge(self, ct: dict) -> None:
        cid = ct["id"]
        del self.contacts[cid]
        self.deals = {k: d for k, d in self.deals.items() if d["contactId"] != cid}
        self.activities = {k: a for k, a in self.activities.items() if a["contactId"] != cid}
        for a in self.activities.values():
            if cid in a.get("attendeeIds", []):
                a["attendeeIds"].remove(cid)
        gone = {k for k, lead in self.leads.items() if lead["contactId"] == cid}
        self.leads = {k: lead for k, lead in self.leads.items() if k not in gone}
        self.lead_keys = {k: v for k, v in self.lead_keys.items() if v not in gone}

    # ------------------------------------------------------------ webhooks

    def emit(self, kind: str, record: dict, extra: dict | None = None, view: dict | None = None) -> None:
        contact_id = record["contactId"] if kind.startswith("deal.") else record["id"]
        subs = [s for s in self.subscriptions.values() if s["agencyId"] == record["agencyId"] and kind in s["events"]]
        if not subs:
            return
        data = view or (self.deal_view(record) if kind.startswith("deal.") else self.contact_view(record))
        for sub in subs:
            body = json.dumps({"id": uuid.uuid4().hex, "type": kind, "occurredAt": now_iso(),
                               "agencyId": record["agencyId"], "data": data, **(extra or {})}).encode()
            self.outbox.append({"sub": sub["id"], "key": contact_id, "body": body, "next": 0.0, "tries": 0,
                                "state": "waiting"})

    def due(self) -> list[dict]:
        """The first undelivered event of every subscription and contact, if it is time to try it."""
        heads: dict[tuple, dict] = {}
        for e in self.outbox:
            if e["state"] != "done" and e["sub"] in self.subscriptions:
                heads.setdefault((e["sub"], e["key"]), e)
        self.outbox = [e for e in self.outbox if e["state"] != "done" and e["sub"] in self.subscriptions]
        ready = [e for e in heads.values() if e["state"] == "waiting" and e["next"] <= time.monotonic()]
        for e in ready:
            e["state"] = "sending"
        return ready

    def deliver(self, e: dict) -> None:
        sub = self.subscriptions.get(e["sub"])
        good = False
        if sub:
            signature = hmac.new(sub["secret"].encode(), e["body"], hashlib.sha256).hexdigest()
            try:
                r = httpx.post(sub["url"], content=e["body"], timeout=10,
                               headers={"Content-Type": "application/json", "X-Signature": signature})
                good = 200 <= r.status_code < 300
            except httpx.HTTPError:
                pass
        with self.lock:
            e["tries"] += 1
            e["state"] = "done" if good else "waiting"
            e["next"] = time.monotonic() + min(2 ** (e["tries"] - 1), 600)

    def deliveries(self) -> None:
        while True:
            with self.lock:
                ready = self.due()
            for e in ready:
                threading.Thread(target=self.deliver, args=(e,), daemon=True).start()
            time.sleep(0.05)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:
        pass

    @property
    def st(self) -> State:
        return self.server.state  # type: ignore[attr-defined]

    def send(self, code: int, body: Any = None) -> None:
        data = b"" if code == 204 else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def handle_any(self, method: str) -> None:
        url = urlparse(self.path)
        parts, q = url.path.strip("/").split("/"), dict(parse_qsl(url.query, keep_blank_values=True))
        try:
            raw = self.rfile.read(int(self.headers.get("Content-Length", 0))) if method in ("POST", "PATCH", "PUT") else b""
            if method == "POST" and parts[0] == "agencies" and parts[-1] == "imports":
                body: Any = {"csv": raw.decode("utf-8-sig")}
            else:
                body = json.loads(raw or b"{}")
            if method in ("POST", "PATCH", "PUT") and not isinstance(body, dict):
                raise Invalid()
            with self.st.lock:
                code, out = self.route(method, parts, q, body)
        except (Invalid, NotFound, Conflict) as e:
            code, out = e.code, {"error": type(e).__name__}
        except json.JSONDecodeError:
            code, out = 400, {"error": "bad JSON"}
        except (ValueError, KeyError):
            code, out = 400, {"error": "bad request"}
        self.send(code, out)

    def do_PUT(self) -> None:
        self.handle_any("PUT")

    def do_GET(self) -> None:
        self.handle_any("GET")

    def do_POST(self) -> None:
        self.handle_any("POST")

    def do_PATCH(self) -> None:
        self.handle_any("PATCH")

    def do_DELETE(self) -> None:
        self.handle_any("DELETE")

    def route(self, method: str, parts: list[str], q: dict, b: dict) -> tuple[int, Any]:
        st = self.st
        limit = min(int(q.get("limit", 50)), 500)
        match method, parts:
            case "POST", ["__backfill_seniority"]:
                st.backfill_seniority()
                return 200, {}
            case "POST", ["companies"]:
                if (not isinstance(b.get("name"), str) or not b["name"] or not isinstance(b.get("agencyId"), str)
                        or not valid_location(b.get("location"))):
                    raise Invalid()
                return 201, st.new_company(b["agencyId"], b["name"], b.get("domain"), b.get("location"))
            case "GET", ["companies"]:
                return 200, [co for co in st.companies.values() if co["agencyId"] == q.get("agencyId")][:limit]
            case "GET", ["companies", cid]:
                if cid not in st.companies:
                    raise NotFound()
                return 200, st.companies[cid]
            case "PATCH", ["companies", cid]:
                co = st.companies.get(cid)
                if not co:
                    raise NotFound()
                if "name" in b and (not isinstance(b["name"], str) or not b["name"]):
                    raise Invalid()
                if not valid_location(b.get("location")):
                    raise Invalid()
                co.update({k: b[k] for k in ("name", "domain", "location") if k in b})
                return 200, co
            case "GET", ["companies", cid, "contacts"]:
                out = []
                for ct in st.live(st.companies[cid]["agencyId"]) if cid in st.companies else []:
                    role = next((x for x in ct["companies"] if x["companyId"] == cid), None)
                    if role:
                        out.append({**st.contact_view(ct), "role": role["role"]})
                return 200, out[:limit]
            case "POST", ["contacts"]:
                if not isinstance(b.get("agencyId"), str):
                    raise Invalid()
                return 201, st.contact_view(st.new_contact(b["agencyId"], b))
            case "GET", ["contacts"]:
                found = st.contact_filter(q)
                if "territory" in q:
                    inside_ids = {ct["id"] for ct in st.territory_contacts(q["territory"])}
                    found = [ct for ct in found if ct["id"] in inside_ids]
                views = [st.contact_view(ct) for ct in found]
                if q.get("sort") == "openPipeline":
                    views.sort(key=lambda v: (-v["openPipeline"], v["lastName"], v["firstName"]))
                if "near" in q or "radiusKm" in q:
                    lat, lng = map(float, q["near"].split(","))
                    radius, centre = float(q["radiusKm"]), {"lat": lat, "lng": lng}
                    if not valid_location(centre) or radius <= 0:
                        raise Invalid()
                    near = []
                    for v in views:
                        p = st.point(st.contacts[v["id"]])
                        if p and (km := km_between(centre, p)) <= radius:
                            near.append({**v, "distanceKm": round(km, 1), "_km": km})
                    views = [{k: x for k, x in v.items() if k != "_km"} for v in sorted(near, key=lambda v: v["_km"])]
                return 200, views[:limit]
            case "GET", ["contacts", cid]:
                return 200, st.contact_view(st.resolve(cid))
            case "DELETE", ["contacts", cid]:
                ct = st.resolve(cid)
                view = st.contact_view(ct)
                ct["deletedAt"] = now_iso()
                st.emit("contact.deleted", ct, view=view)
                return 204, None
            case "PATCH", ["contacts", cid]:
                ct = st.resolve(cid)
                new = st.apply_contact(ct["agencyId"], ct, b, creating=False)
                st.contacts[new["id"]] = new
                st.emit("contact.updated", new)
                return 200, st.contact_view(new)
            case "POST", ["contacts", cid, "merge"]:
                return 200, st.merge(cid, b)
            case "GET", ["contacts", cid, "merges"]:
                return 200, st.merges.get(st.resolve(cid)["id"], [])
            case "POST", ["contacts", cid, "companies"]:
                ct = st.resolve(cid)
                entry = st.company_entry(ct["agencyId"], b)
                if entry["companyId"] in {x["companyId"] for x in ct["companies"]}:
                    raise Conflict()
                ct["companies"].append(entry)
                st.emit("contact.updated", ct)
                return 200, st.contact_view(ct)
            case "DELETE", ["contacts", cid, "companies", company_id]:
                ct = st.resolve(cid)
                if company_id not in {x["companyId"] for x in ct["companies"]}:
                    raise NotFound()
                ct["companies"] = [x for x in ct["companies"] if x["companyId"] != company_id]
                st.emit("contact.updated", ct)
                return 200, st.contact_view(ct)
            case "GET", ["contacts", cid, "activities"]:
                return 200, st.timeline(st.resolve(cid)["id"])[:limit]
            case "POST", ["deals"]:
                ag = b.get("agencyId")
                d = st.apply_deal(ag, {"id": uuid.uuid4().hex, "agencyId": ag, "custom": {}, "closeDate": None,
                                       "currency": "EUR", "followUpOn": None}, b, creating=True)
                st.snapshot_deal(d)
                st.deals[d["id"]] = d
                st.emit("deal.created", d)
                return 201, st.deal_view(d)
            case "GET", ["deals"]:
                return 200, [st.deal_view(d) for d in st.deal_filter(q)][:limit]
            case "GET", ["deals", did]:
                if did not in st.deals or not st.visible(st.deals[did]):
                    raise NotFound()
                return 200, st.deal_view(st.deals[did])
            case "PATCH", ["deals", did]:
                if did not in st.deals or not st.visible(st.deals[did]):
                    raise NotFound()
                old = st.deals[did]
                d = st.apply_deal(old["agencyId"], old, b, creating=False)
                st.deals[did] = d
                st.emit("deal.updated", d)
                if d["stage"] != old["stage"]:
                    st.emit("deal.stage_changed", d, {"previousStage": old["stage"]})
                return 200, st.deal_view(d)
            case "POST", ["agencies", ag, "fields"]:
                f = {k: b.get(k) for k in ("entity", "key", "label", "type", "options")}
                if (f["entity"] not in ("contact", "deal") or not isinstance(f["key"], str) or not KEY.match(f["key"])
                        or f["type"] not in TYPES or (f["type"] == "select" and not (
                            isinstance(f["options"], list) and f["options"] and all(isinstance(o, str) for o in f["options"])))):
                    raise Invalid()
                if f["key"] in st.defs(ag, f["entity"]):
                    raise Conflict()
                st.fields.setdefault(ag, []).append(f)
                return 201, f
            case "GET", ["agencies", ag, "fields"]:
                return 200, [f for f in st.fields.get(ag, []) if q.get("entity") in (None, f["entity"])]
            case "POST", ["webhooks", provider] if provider in ("formflow", "leadpipe"):
                return st.webhook(provider, b)
            case "GET", ["leads"]:
                out = []
                for lead in st.leads.values():
                    if lead["agencyId"] != q.get("agencyId") or not st.visible(lead):
                        continue
                    if any(lead.get(k) != q[k] for k in ("provider", "contactId") if k in q):
                        continue
                    if not all(payload_matches(lead["payload"], k[8:], v) for k, v in q.items() if k.startswith("payload.")):
                        continue
                    out.append(lead)
                return 200, out[:limit]
            case "GET", ["leads", lid]:
                if lid not in st.leads or not st.visible(st.leads[lid]):
                    raise NotFound()
                return 200, st.leads[lid]
            case "GET", ["reports", "deals"]:
                return 200, st.report(q)
            case "POST", ["activities"]:
                return 201, st.activity_view(st.new_activity(b))
            case "GET", ["activities"]:
                return 200, st.activity_filter(q)[:limit]
            case "GET", ["activities", aid]:
                return 200, st.activity_view(st.activity(aid))
            case "PATCH", ["activities", aid]:
                a = st.activity(aid)
                st.activities[aid] = st.apply_activity(a["agencyId"], a, b, creating=False)
                return 200, st.activity_view(st.activities[aid])
            case "DELETE", ["activities", aid]:
                st.activity(aid)
                del st.activities[aid]
                return 204, None
            case "POST", ["agencies", ag, "segments"]:
                if not isinstance(b.get("name"), str) or not b["name"]:
                    raise Invalid()
                st.check_condition(ag, b.get("filter"))
                seg = {"id": uuid.uuid4().hex, "agencyId": ag, "name": b["name"], "filter": b["filter"]}
                st.segments[seg["id"]] = seg
                return 201, seg
            case "GET", ["agencies", ag, "segments"]:
                return 200, [s for s in st.segments.values() if s["agencyId"] == ag]
            case "GET", ["segments", sid]:
                return 200, st.segment(sid)
            case "PATCH", ["segments", sid]:
                seg = st.segment(sid)
                if "name" in b and (not isinstance(b["name"], str) or not b["name"]):
                    raise Invalid()
                if "filter" in b:
                    st.check_condition(seg["agencyId"], b["filter"])
                seg.update({k: b[k] for k in ("name", "filter") if k in b})
                return 200, seg
            case "DELETE", ["segments", sid]:
                st.segment(sid)
                del st.segments[sid]
                return 204, None
            case "GET", ["segments", sid, "contacts"]:
                return 200, st.segment_contacts(sid)[:limit]
            case "GET", ["segments", sid, "count"]:
                return 200, {"count": len(st.segment_contacts(sid))}
            case "GET", ["search"]:
                return 200, {"results": st.search(q)}
            case "PUT", ["agencies", ag, "rates", day]:
                if not valid_date(day) or not all(re.fullmatch(r"[A-Z]{3}", k) and is_num(v) and v > 0
                                                  for k, v in b.items()):
                    raise Invalid()
                st.rates.setdefault(ag, {})[day] = dict(b)
                return 200, st.rates[ag]
            case "GET", ["agencies", ag, "rates"]:
                return 200, st.rates.get(ag, {})
            case "POST", ["agencies", ag, "territories"]:
                area = b.get("area")
                if (not isinstance(b.get("name"), str) or not isinstance(area, list) or not all(
                        isinstance(p, list) and len(p) == 2 and valid_location({"lng": p[0], "lat": p[1]}) for p in area)):
                    raise Invalid()
                corners = area[:-1] if len(area) > 1 and area[0] == area[-1] else area
                if len(corners) < 3:
                    raise Invalid()
                t = {"id": uuid.uuid4().hex, "agencyId": ag, "name": b["name"], "area": corners}
                st.territories[t["id"]] = t
                return 201, t
            case "GET", ["agencies", ag, "territories"]:
                return 200, [t for t in st.territories.values() if t["agencyId"] == ag]
            case "DELETE", ["territories", tid]:
                if tid not in st.territories:
                    raise NotFound()
                del st.territories[tid]
                return 204, None
            case "GET", ["territories", tid, "summary"]:
                views = [st.contact_view(ct) for ct in st.territory_contacts(tid)]
                return 200, {"contacts": len(views), "openPipeline": round(sum(v["openPipeline"] for v in views), 2)}
            case "POST", ["agencies", ag, "imports"]:
                return 200, st.import_csv(ag, b["csv"])
            case "GET", ["trash"]:
                gone = [ct for ct in st.contacts.values() if ct["agencyId"] == q.get("agencyId") and ct.get("deletedAt")]
                return 200, [st.trash_entry(ct) for ct in sorted(gone, key=lambda ct: ct["deletedAt"], reverse=True)]
            case "POST", ["trash", cid, "restore"]:
                ct = st.trashed(cid)
                del ct["deletedAt"]
                return 200, st.contact_view(ct)
            case "DELETE", ["trash", cid]:
                st.purge(st.trashed(cid))
                return 204, None
            case "POST", ["agencies", ag, "subscriptions"]:
                url, events = b.get("url"), b.get("events")
                if (not isinstance(url, str) or urlparse(url).scheme not in ("http", "https") or not urlparse(url).netloc
                        or not isinstance(events, list) or not events or not set(events) <= EVENTS):
                    raise Invalid()
                sub = {"id": uuid.uuid4().hex, "agencyId": ag, "url": url, "events": events,
                       "secret": secrets.token_hex(16)}
                st.subscriptions[sub["id"]] = sub
                return 201, sub
            case "GET", ["agencies", ag, "subscriptions"]:
                return 200, [s for s in st.subscriptions.values() if s["agencyId"] == ag]
            case "DELETE", ["subscriptions", sid]:
                if sid not in st.subscriptions:
                    raise NotFound()
                del st.subscriptions[sid]
                return 204, None
        raise NotFound()


def serve(stale_names: bool = False, case_sensitive_emails: bool = False, search_any_word: bool = False,
          frozen_rates: bool = False) -> tuple[ThreadingHTTPServer, str]:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    srv.state = State(stale_names, case_sensitive_emails, search_any_word, frozen_rates)  # type: ignore[attr-defined]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    threading.Thread(target=srv.state.deliveries, daemon=True).start()  # type: ignore[attr-defined]
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"

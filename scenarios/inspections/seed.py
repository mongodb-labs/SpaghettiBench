"""Deterministic "production" data, created through the app's own API after the initial build.

The same RNG seed is used for every run, so both database arms get identical data.
"""
from __future__ import annotations

import datetime as dt
import random
from typing import Any, Callable

import httpx

from harness.suite import get_id
from scenarios.inspections.common import FIRE_FIELDS

SEED = 20260930

WORKERS = [
    "j.ramirez", "a.kowalski", "m.nguyen", "s.okafor", "l.müller",
    "p.o'connor", "t.sato", "r.haddad", "e.lindqvist", "k.mensah",
]
SITES = ["Depot 4", "Depot 7", "Head office", "Riverside Plant", "Nordhafen Lager",
         "Unit 12 – Parkway Industrial Estate"]
LOCATIONS = ["Lobby", "Loading bay", "Stairwell A", "Kitchen", "Server room",
             "Warehouse – aisle 12", "Büro 3. OG", "Boiler room"]
KITCHEN_NOTES = [
    "All fine.",
    "Crème brûlée station – OK",
    'Chef O\'Brien said "fixed tomorrow"',
    "Seal on walk-in door torn; reported to facilities.",
    "温度计需要校准",
    "Deep clean overdue. " * 60,
]


def _date(rng: random.Random, start: dt.date = dt.date(2025, 1, 1), days: int = 1460) -> str:
    return (start + dt.timedelta(days=rng.randint(0, days))).isoformat()


def _maybe(rng: random.Random, p: float = 0.85) -> bool:
    return rng.random() < p


def gen_acme_fire(rng: random.Random) -> dict[str, Any]:
    a: dict[str, Any] = {"site_name": rng.choice(SITES)}
    ext = {
        "extinguisher_location": rng.choice(LOCATIONS),
        "extinguisher_expiry": _date(rng),
        "pressure_ok": rng.random() < 0.75,
    }
    r = rng.random()
    if r >= 0.30:
        a.update(ext)
    elif r >= 0.15:
        a.update({k: ext[k] for k in rng.sample(sorted(ext), rng.randint(1, 2))})
    # else: none of the extinguisher fields, which the conversion must turn into an empty group
    if _maybe(rng):
        a["condition"] = rng.choice(["good", "fair", "poor"])
    if _maybe(rng):
        a["score"] = rng.choice([rng.randint(0, 10), rng.randint(0, 10), 7.5, 0, 10])
    return a


def gen_acme_lighting(rng: random.Random) -> dict[str, Any]:
    ok = rng.random() < 0.8
    a: dict[str, Any] = {
        "building": rng.choice(["Block A", "Block B", "Annex", "Warehouse 2"]),
        "test_date": _date(rng, dt.date(2026, 6, 1), 120),
        "all_working": ok,
    }
    if _maybe(rng):
        a["luminaires_tested"] = rng.randint(4, 120)
    if not ok:
        a["failed_units"] = rng.choice(["EL-14", "EL-03, EL-07", "Stairwell 2 exit sign"])
    if _maybe(rng):
        a["duration"] = rng.choice(["1h", "3h"])
    return a


def gen_kitchen(rng: random.Random) -> dict[str, Any]:
    a: dict[str, Any] = {
        "kitchen_area": rng.choice(["prep", "cold store", "dish wash", "service"]),
        "handwash_ok": rng.random() < 0.8,
        "audit_date": _date(rng, dt.date(2026, 6, 1), 120),
    }
    if _maybe(rng):
        a["fridge_temp"] = rng.choice([3, 4.5, -18, -21.5, 7, 20, -30])
    if _maybe(rng, 0.7):
        a["pest_signs"] = rng.random() < 0.25
    if _maybe(rng, 0.5):
        a["notes"] = rng.choice(KITCHEN_NOTES)
    return a


def gen_hvac(rng: random.Random) -> dict[str, Any]:
    a: dict[str, Any] = {"unit_id": f"AHU-{rng.randint(1, 24):02d}", "filter_changed": rng.random() < 0.6}
    if _maybe(rng):
        a["static_pressure"] = round(rng.uniform(0, 5), 2)
    if _maybe(rng):
        a["refrigerant"] = rng.choice(["R-410A", "R-32", "R-454B"])
    if _maybe(rng, 0.7):
        a["next_service"] = _date(rng, dt.date(2026, 10, 1), 365)
    if _maybe(rng, 0.4):
        a["technician_notes"] = rng.choice([
            "Belt tension adjusted", "Condensate drain cleared",
            "Noisy bearing – replace at next visit", "Access via roof hatch (key at reception)",
        ])
    return a


def gen_forklift(rng: random.Random) -> dict[str, Any]:
    cond = rng.choice(["good", "good", "good", "worn", "damaged"])
    a: dict[str, Any] = {
        "forklift_id": f"FL-{rng.randint(100, 140)}",
        "brakes_ok": rng.random() < 0.9,
        "horn_ok": rng.random() < 0.95,
        "forks_condition": cond,
    }
    if _maybe(rng):
        a["hour_meter"] = rng.randint(0, 20000)
    if cond != "good":
        a["damage_notes"] = rng.choice(["Left fork tip bent", "Heel wear > 10%", "Crack near weld – tagged out"])
    return a


FORMS: dict[str, tuple[dict[str, Any], int, Callable[[random.Random], dict[str, Any]]]] = {
    "acme_fire": ({"companyId": "acme", "name": "Fire Safety Check", "fields": FIRE_FIELDS}, 80, gen_acme_fire),
    "acme_lighting": ({"companyId": "acme", "name": "Emergency Lighting Test", "fields": [
        {"key": "building", "label": "Building", "type": "text", "required": True},
        {"key": "test_date", "label": "Test date", "type": "date", "required": True},
        {"key": "luminaires_tested", "label": "Luminaires tested", "type": "number", "min": 0},
        {"key": "all_working", "label": "All working", "type": "boolean", "required": True},
        {"key": "failed_units", "label": "Failed units", "type": "text"},
        {"key": "duration", "label": "Test duration", "type": "select", "options": ["1h", "3h"]},
    ]}, 30, gen_acme_lighting),
    "northwind_kitchen": ({"companyId": "northwind-foods", "name": "Kitchen Hygiene Audit", "fields": [
        {"key": "kitchen_area", "label": "Area", "type": "select",
         "options": ["prep", "cold store", "dish wash", "service"], "required": True},
        {"key": "fridge_temp", "label": "Fridge temperature (°C)", "type": "number", "min": -30, "max": 20},
        {"key": "handwash_ok", "label": "Handwash station OK", "type": "boolean", "required": True},
        {"key": "pest_signs", "label": "Signs of pests", "type": "boolean"},
        {"key": "notes", "label": "Notes", "type": "text"},
        {"key": "audit_date", "label": "Audit date", "type": "date", "required": True},
    ]}, 60, gen_kitchen),
    "brightpath_hvac": ({"companyId": "brightpath-facilities", "name": "HVAC Maintenance", "fields": [
        {"key": "unit_id", "label": "Unit", "type": "text", "required": True},
        {"key": "filter_changed", "label": "Filter changed", "type": "boolean", "required": True},
        {"key": "static_pressure", "label": "Static pressure (inWC)", "type": "number", "min": 0, "max": 5},
        {"key": "refrigerant", "label": "Refrigerant", "type": "select", "options": ["R-410A", "R-32", "R-454B"]},
        {"key": "next_service", "label": "Next service", "type": "date"},
        {"key": "technician_notes", "label": "Technician notes", "type": "text"},
    ]}, 50, gen_hvac),
    "meridian_forklift": ({"companyId": "meridian-logistics", "name": "Forklift Pre-Use Check", "fields": [
        {"key": "forklift_id", "label": "Forklift", "type": "text", "required": True},
        {"key": "hour_meter", "label": "Hour meter", "type": "number", "min": 0},
        {"key": "brakes_ok", "label": "Brakes OK", "type": "boolean", "required": True},
        {"key": "horn_ok", "label": "Horn OK", "type": "boolean", "required": True},
        {"key": "forks_condition", "label": "Forks", "type": "select", "options": ["good", "worn", "damaged"]},
        {"key": "damage_notes", "label": "Damage notes", "type": "text"},
    ]}, 60, gen_forklift),
}


def _created_id(r: httpx.Response) -> str | None:
    if r.status_code not in (200, 201):
        return None
    try:
        return get_id(r.json())
    except ValueError:
        return None


def run(base_url: str) -> dict[str, Any]:
    rng = random.Random(SEED)
    start = dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc)
    # Generate everything before touching the API so the data never depends on the app's behaviour.
    plan = []
    for key, (_, count, gen) in FORMS.items():
        for _ in range(count):
            at = start + dt.timedelta(seconds=rng.randint(0, 120 * 86400))
            plan.append((key, {
                "submittedBy": rng.choice(WORKERS),
                "submittedAt": at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "answers": gen(rng),
            }))
    rng.shuffle(plan)

    data: dict[str, Any] = {"forms": [], "submissions": [], "failures": []}
    form_ids: dict[str, str] = {}
    with httpx.Client(base_url=base_url, timeout=30) as c:
        for key, (payload, _, _) in FORMS.items():
            r = c.post("/forms", json=payload)
            fid = _created_id(r)
            if fid:
                form_ids[key] = fid
                data["forms"].append({"key": key, "id": fid, "payload": payload})
            else:
                data["failures"].append({"form": key, "status": r.status_code, "body": r.text[:300]})
        for key, payload in plan:
            if key not in form_ids:
                data["failures"].append({"form": key, "status": None, "body": "form was not created"})
                continue
            r = c.post(f"/forms/{form_ids[key]}/submissions", json=payload)
            sid = _created_id(r)
            if sid:
                data["submissions"].append({"form": key, "id": sid, "payload": payload})
            else:
                data["failures"].append({"form": key, "status": r.status_code, "body": r.text[:300],
                                         "payload": payload})
    return data


# ---------------------------------------------------------------- later production data

LATE_SEED = 20270120
TIDEWATER_FORM = {"companyId": "tidewater-properties", "name": "Building Safety Walk", "fields": [
    {"key": "site_name", "label": "Site", "type": "text", "required": True},
    {"key": "floors", "label": "Floors", "type": "group", "maxItems": 20, "fields": [
        {"key": "floor_name", "label": "Floor", "type": "text", "required": True},
        {"key": "rooms", "label": "Rooms", "type": "group", "maxItems": 30, "fields": [
            {"key": "room_name", "label": "Room", "type": "text", "required": True},
            {"key": "items", "label": "Items", "type": "group", "maxItems": 50, "fields": [
                {"key": "kind", "label": "Kind", "type": "select",
                 "options": ["extinguisher", "alarm", "exit_sign", "sprinkler"], "required": True},
                {"key": "status", "label": "Status", "type": "select", "options": ["pass", "fail"], "required": True},
                {"key": "notes", "label": "Notes", "type": "text"},
                {"key": "serial", "label": "Serial number", "type": "text"},
            ]},
        ]},
    ]},
]}
TIDEWATER_SITES = ["Harbour View Apartments", "Quayside Offices", "Marina Point", "Old Customs House", "Dock 9 Lofts"]
FLOORS = ["Basement", "Ground", "First", "Second", "Roof"]
ROOMS = ["Lobby", "Plant room", "Corridor", "Office 1", "Office 2", "Kitchen", "Stairwell", "Bike store"]
ITEM_NOTES = ["Bracket loose", "Sticker faded", "Blocked by furniture", "Tested OK", "Needs service – ticket #881"]


def gen_walk(rng: random.Random) -> dict[str, Any]:
    floors = []
    for floor in sorted(rng.sample(FLOORS, rng.randint(1, 3)), key=FLOORS.index):
        rooms = []
        for room in rng.sample(ROOMS, rng.randint(1, 4)):
            items = []
            for _ in range(rng.randint(1, 5)):
                item: dict[str, Any] = {"kind": rng.choice(["extinguisher", "alarm", "exit_sign", "sprinkler"]),
                                        "status": "fail" if rng.random() < 0.2 else "pass"}
                if rng.random() < 0.4:
                    item["notes"] = rng.choice(ITEM_NOTES)
                if rng.random() < 0.5:
                    item["serial"] = f"SN-{rng.randint(10000, 99999)}"
                items.append(item)
            rooms.append({"room_name": room, "items": items})
        floors.append({"floor_name": floor, "rooms": rooms})
    return {"site_name": rng.choice(TIDEWATER_SITES), "floors": floors}


def run_late(base_url: str) -> dict[str, Any]:
    """Tidewater Properties starts using nested inspections: added once the app supports them."""
    rng = random.Random(LATE_SEED)
    start = dt.datetime(2026, 11, 1, tzinfo=dt.timezone.utc)
    plan = [{"submittedBy": rng.choice(WORKERS),
             "submittedAt": (start + dt.timedelta(seconds=rng.randint(0, 75 * 86400))).strftime("%Y-%m-%dT%H:%M:%SZ"),
             "answers": gen_walk(rng)} for _ in range(40)]
    data: dict[str, Any] = {"forms": [], "submissions": [], "failures": []}
    with httpx.Client(base_url=base_url, timeout=30) as c:
        fid = _created_id(r := c.post("/forms", json=TIDEWATER_FORM))
        if not fid:
            data["failures"].append({"form": "tidewater_walk", "status": r.status_code, "body": r.text[:300]})
            return data
        data["forms"].append({"key": "tidewater_walk", "id": fid, "payload": TIDEWATER_FORM})
        for n, payload in enumerate(plan):
            r = c.post(f"/forms/{fid}/submissions", json=payload)
            sid = _created_id(r)
            if sid:
                data["submissions"].append({"key": f"tw{n}", "form": "tidewater_walk", "id": sid, "payload": payload})
            else:
                data["failures"].append({"form": "tidewater_walk", "status": r.status_code, "body": r.text[:300]})
    return data


def run_late_findings(base_url: str, late: dict[str, Any]) -> dict[str, Any]:
    """Managers record a few findings by hand on items that passed, without due dates."""
    data: dict[str, Any] = {"findings": [], "failures": []}
    severities = ["low", "medium", "high", "critical", "low"]
    with httpx.Client(base_url=base_url, timeout=30) as c:
        for sub, severity in zip(late["submissions"][1:6], severities):
            r = c.get(f"/submissions/{sub['id']}")
            answers = r.json().get("answers") if r.status_code == 200 else None
            item = next((i for f in (answers or {}).get("floors") or [] for room in f.get("rooms") or []
                         for i in room.get("items") or [] if i.get("status") == "pass"), None)
            if not item:
                data["failures"].append({"submission": sub["id"], "status": r.status_code, "body": "no passed item"})
                continue
            r = c.post(f"/submissions/{sub['id']}/items/{item['id']}/findings",
                       json={"title": "Check mounting bracket", "severity": severity})
            fid = _created_id(r)
            if fid:
                data["findings"].append({"submission": sub["id"], "item": item["id"], "id": fid, "severity": severity})
            else:
                data["failures"].append({"submission": sub["id"], "status": r.status_code, "body": r.text[:300]})
    return data

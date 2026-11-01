"""In-memory reference implementation of the shop spec (all phases), used to test the checks.

It stores everything in the final shape from the start (variants, order numbers, line ids, discounts,
versions and stock movements); the only data change that needs a step is Northloom's material backfill,
exposed as the test-only endpoint `/__backfill_material`.

Deliberately wrong modes, to show the checks catch them:
- `live_prices`: order lines show the product's current sku, name and price instead of those at the
  time of the order (the classic bug of joining orders to products).
- `racy_stock`: an order checks stock and discount code, then takes them in a separate step without
  holding the lock in between (check-then-act without a transaction).
- `lazy_expiry`: expired carts keep holding stock until a cleanup removes them, up to a minute later
  (relying on a TTL index for correctness).
- `unversioned_edits`: order edits ignore `version`, so concurrent edits overwrite each other.
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import math
import re
import threading
import time
import uuid
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qsl, unquote, urlparse

from scenarios.shop.common import cents, fold, is_num, material, parse_time, round_cents, stems

KEY = re.compile(r"^[A-Za-z0-9_]+$")
CODE = re.compile(r"^[A-Za-z0-9_-]+$")
ATTR_TYPES = {"text", "number", "boolean", "select"}
FIELD_TYPES = {"text", "select", "boolean"}
MATERIAL = {"key": "material", "label": "Material", "type": "select", "options": ["cotton", "linen", "wool", "other"]}
REPORT_KEYS = {"day", "month", "productId", "variantId", "category", "discountCode"}
LANG = re.compile(r"^[a-z]{2}$")


class Invalid(Exception):
    code = 400


class Forbidden(Exception):
    code = 403


class NotFound(Exception):
    code = 404


class Conflict(Exception):
    code = 409


def valid_email(v: Any) -> bool:
    return isinstance(v, str) and v.count("@") == 1 and all(v.split("@"))


def valid_date(v: Any) -> bool:
    if not isinstance(v, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        return False
    try:
        dt.date.fromisoformat(v)
        return True
    except ValueError:
        return False


def is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def text(v: Any) -> bool:
    return isinstance(v, str) and v != ""


def options_list(v: Any) -> bool:
    return isinstance(v, list) and bool(v) and all(text(x) for x in v) and len(set(v)) == len(v)


def money(c: int) -> float:
    return c / 100


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def valid_location(v: Any) -> bool:
    return (isinstance(v, dict) and is_num(v.get("lat")) and is_num(v.get("lng"))
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


def polygon(area: Any) -> list[list[float]]:
    if not isinstance(area, list) or not all(isinstance(p, list) and len(p) == 2
                                             and valid_location({"lng": p[0], "lat": p[1]}) for p in area):
        raise Invalid()
    corners = area[:-1] if len(area) > 1 and area[0] == area[-1] else area
    if len(corners) < 3:
        raise Invalid()
    return corners


def schedule_after(start: str, weeks: int, day: str, inclusive: bool) -> str:
    """The first date of the schedule (start, start + weeks, ...) after `day` (or on it, if inclusive)."""
    s, d, step = dt.date.fromisoformat(start), dt.date.fromisoformat(day), weeks * 7
    if s > d or (inclusive and s == d):
        return s.isoformat()
    n = (d - s).days // step + (0 if inclusive and (d - s).days % step == 0 else 1)
    return (s + dt.timedelta(days=n * step)).isoformat()


def valid_attr(d: dict, v: Any) -> bool:
    return {"text": isinstance(v, str), "number": is_num(v), "boolean": isinstance(v, bool),
            "select": v in (d.get("options") or [])}[d["type"]]


def raw_value(d: dict, raw: str) -> Any:
    if d["type"] == "number":
        try:
            return float(raw)
        except ValueError:
            raise Invalid()
    if d["type"] == "boolean":
        if raw not in ("true", "false"):
            raise Invalid()
        return raw == "true"
    return raw


def same_value(stored: Any, want: Any) -> bool:
    if isinstance(want, bool) or isinstance(stored, bool):
        return stored is want
    if isinstance(want, float):
        return is_num(stored) and float(stored) == want
    return stored == want


class State:
    def __init__(self, live_prices: bool, racy_stock: bool, lazy_expiry: bool, unversioned_edits: bool):
        self.products: dict[str, dict] = {}
        self.variant_product: dict[str, str] = {}
        self.orders: dict[str, dict] = {}
        self.returns: dict[str, list[dict]] = {}
        self.attr_defs: dict[tuple[str, str], list[dict]] = {}
        self.discounts: dict[str, dict[str, dict]] = {}
        self.numbers: dict[str, int] = {}
        self.reviews: dict[str, dict] = {}
        self.languages: dict[str, dict] = {}
        self.settings: dict[str, dict] = {}
        self.carts: dict[str, dict] = {}
        self.zones: dict[str, dict] = {}
        self.pickups: dict[str, dict] = {}
        self.subscriptions: dict[str, dict] = {}
        self.movements: list[dict] = []
        self.seq = 0
        self.lock = threading.RLock()
        self.live = live_prices
        self.racy = racy_stock
        self.lazy = lazy_expiry
        self.unversioned = unversioned_edits

    def next_seq(self) -> int:
        self.seq += 1
        return self.seq

    # ------------------------------------------------------------ stock

    def move(self, vid: str, change: int, reason: str, order_id: str | None = None, note: str | None = None) -> dict | None:
        """Record a stock change that has just been made."""
        if not change:
            return None
        m = {"id": uuid.uuid4().hex, "variantId": vid, "productId": self.variant_product[vid], "change": change,
             "stockAfter": self.variant(vid)["stock"], "reason": reason, "orderId": order_id, "at": now()}
        if note is not None:
            m["note"] = note
        self.movements.append(m)
        return m

    def holding(self, cart: dict) -> bool:
        if cart["status"] != "active":
            return False
        grace = dt.timedelta(seconds=60) if self.lazy else dt.timedelta()
        return utc() < cart["expiresAt"] + grace

    def held(self, vid: str, except_cart: str | None = None) -> int:
        return sum(c["items"].get(vid, 0) for c in self.carts.values() if c["id"] != except_cart and self.holding(c))

    def available(self, vid: str, except_cart: str | None = None) -> int:
        return self.variant(vid)["stock"] - self.held(vid, except_cart)

    # ------------------------------------------------------------ products

    def variant(self, vid: str) -> dict:
        p = self.products[self.variant_product[vid]]
        return next(v for v in p["variants"] if v["id"] == vid)

    def sku_taken(self, shop: str, sku: str) -> bool:
        return any(v["sku"] == sku for p in self.products.values() if p["shopId"] == shop for v in p["variants"])

    def defs(self, shop: str, category: str) -> dict[str, dict]:
        return {d["key"]: d for d in self.attr_defs.get((shop, category), [])}

    def check_variant(self, options: list[str], v: Any) -> dict:
        if not isinstance(v, dict) or not text(v.get("sku")) or not isinstance(v.get("options", {}), dict):
            raise Invalid()
        opts = v.get("options") or {}
        if set(opts) != set(options) or not all(text(x) for x in opts.values()):
            raise Invalid()
        price, stock = cents(v.get("price")), v.get("stock")
        if price is None or not is_int(stock) or stock < 0:
            raise Invalid()
        return {"id": uuid.uuid4().hex, "sku": v["sku"], "options": dict(opts), "price": price, "stock": stock}

    def check_personalization(self, fields: Any) -> list[dict]:
        if not isinstance(fields, list):
            raise Invalid()
        out = []
        for f in fields:
            if (not isinstance(f, dict) or not isinstance(f.get("key"), str) or not KEY.match(f["key"])
                    or not text(f.get("label")) or f.get("type") not in FIELD_TYPES
                    or not isinstance(f.get("required", False), bool)):
                raise Invalid()
            field = {"key": f["key"], "label": f["label"], "type": f["type"], "required": f.get("required", False)}
            if f["type"] == "select":
                if not options_list(f.get("options")):
                    raise Invalid()
                field["options"] = list(f["options"])
            if f["type"] == "text" and f.get("maxLength") is not None:
                if not is_int(f["maxLength"]) or f["maxLength"] < 1:
                    raise Invalid()
                field["maxLength"] = f["maxLength"]
            out.append(field)
        if len({f["key"] for f in out}) != len(out):
            raise Invalid()
        return out

    def apply_attributes(self, p: dict, values: Any) -> None:
        if not isinstance(values, dict):
            raise Invalid()
        defs = self.defs(p["shopId"], p["category"])
        for k, v in values.items():
            if k not in defs or (v is not None and not valid_attr(defs[k], v)):
                raise Invalid()
        for k, v in values.items():
            if v is None:
                p["attributes"].pop(k, None)
            else:
                p["attributes"][k] = v

    def new_product(self, b: dict) -> dict:
        shop = b.get("shopId")
        if not text(shop) or not text(b.get("name")) or not text(b.get("category")):
            raise Invalid()
        if "variants" in b:
            if any(k in b for k in ("sku", "price", "stock")):
                raise Invalid()
            options = b.get("options") or []
            if not isinstance(options, list) or not all(text(o) for o in options) or len(set(options)) != len(options):
                raise Invalid()
            if not isinstance(b["variants"], list) or not b["variants"]:
                raise Invalid()
            variants = [self.check_variant(options, v) for v in b["variants"]]
            if len({json.dumps(v["options"], sort_keys=True) for v in variants}) != len(variants):
                raise Invalid()
        else:
            if b.get("options"):
                raise Invalid()
            options = []
            variants = [self.check_variant([], {"sku": b.get("sku"), "price": b.get("price"), "stock": b.get("stock")})]
        p = {"id": uuid.uuid4().hex, "shopId": shop, "name": b["name"], "category": b["category"],
             "options": list(options), "variants": variants, "attributes": {}, "personalization": [],
             "description": None, "translations": {}, "active": True, "source": None}
        if b.get("attributes") is not None:
            self.apply_attributes(p, b["attributes"])
        if b.get("personalization") is not None:
            p["personalization"] = self.check_personalization(b["personalization"])
        self.apply_texts(p, b)
        skus = [v["sku"] for v in variants]
        if len(set(skus)) != len(skus) or any(self.sku_taken(shop, s) for s in skus):
            raise Conflict()
        self.products[p["id"]] = p
        for v in variants:
            self.variant_product[v["id"]] = p["id"]
            self.move(v["id"], v["stock"], "initial")
        return p

    def apply_texts(self, p: dict, b: dict) -> None:
        """description, translations and active, shared by create and PATCH."""
        if "description" in b:
            if b["description"] is not None and not (isinstance(b["description"], str) and len(b["description"]) <= 5000):
                raise Invalid()
            p["description"] = b["description"]
        if "active" in b:
            if not isinstance(b["active"], bool):
                raise Invalid()
            p["active"] = b["active"]
        if b.get("translations") is not None:
            tr = b["translations"]
            if not isinstance(tr, dict) or not set(tr) <= set(self.langs(p["shopId"])["others"]):
                raise Invalid()
            for lang, fields in tr.items():
                if fields is None:
                    p["translations"].pop(lang, None)
                    continue
                if not isinstance(fields, dict) or not set(fields) <= {"name", "description", "options"}:
                    raise Invalid()
                if fields.get("name") is not None and not text(fields["name"]):
                    raise Invalid()
                if fields.get("description") is not None and not isinstance(fields["description"], str):
                    raise Invalid()
                if fields.get("options") is not None and not (isinstance(fields["options"], dict)
                                                            and all(text(v) for v in fields["options"].values())):
                    raise Invalid()
                mine = p["translations"].setdefault(lang, {})
                for k, v in fields.items():
                    if v is None:
                        mine.pop(k, None)
                    else:
                        mine[k] = copy.deepcopy(v)

    def langs(self, shop: str) -> dict:
        return self.languages.get(shop, {"default": "en", "others": []})

    def check_lang(self, shop: str, lang: Any) -> str | None:
        if lang is None:
            return None
        langs = self.langs(shop)
        if lang != langs["default"] and lang not in langs["others"]:
            raise Invalid()
        return None if lang == langs["default"] else lang

    def texts(self, p: dict, lang: str | None) -> tuple[str, str | None, dict]:
        """Name, description and option-value map in a language, falling back to the default."""
        tr = p["translations"].get(lang, {}) if lang else {}
        return tr.get("name") or p["name"], tr.get("description") or p["description"], tr.get("options") or {}

    def patch_product(self, p: dict, b: dict) -> dict:
        if any(k in b for k in ("sku", "options", "variants", "shopId")):
            raise Invalid()
        new = copy.deepcopy(p)
        for k in ("name", "category"):
            if k in b:
                if not text(b[k]):
                    raise Invalid()
                new[k] = b[k]
        old_stock = new["variants"][0]["stock"]
        if "price" in b or "stock" in b:
            if len(new["variants"]) != 1:
                raise Invalid()
            v = new["variants"][0]
            if "price" in b:
                if cents(b["price"]) is None:
                    raise Invalid()
                v["price"] = cents(b["price"])
            if "stock" in b:
                if not is_int(b["stock"]) or b["stock"] < 0:
                    raise Invalid()
                v["stock"] = b["stock"]
        if new["category"] != p["category"]:
            old, target = self.defs(p["shopId"], p["category"]), self.defs(p["shopId"], new["category"])
            new["attributes"] = {k: v for k, v in new["attributes"].items()
                                 if k in target and old.get(k, {}).get("type") == target[k]["type"]}
        if "attributes" in b:
            self.apply_attributes(new, b["attributes"])
        if "personalization" in b:
            new["personalization"] = self.check_personalization(b["personalization"])
        self.apply_texts(new, b)
        self.products[p["id"]] = new
        self.move(new["variants"][0]["id"], new["variants"][0]["stock"] - old_stock, "adjustment")
        return new

    def add_variant(self, p: dict, b: dict) -> dict:
        v = self.check_variant(p["options"], b)
        if any(x["options"] == v["options"] for x in p["variants"]):
            raise Invalid()
        if self.sku_taken(p["shopId"], v["sku"]):
            raise Conflict()
        p["variants"].append(v)
        self.variant_product[v["id"]] = p["id"]
        self.move(v["id"], v["stock"], "initial")
        return p

    def patch_variant(self, p: dict, vid: str, b: dict) -> dict:
        v = next((x for x in p["variants"] if x["id"] == vid), None)
        if not v:
            raise NotFound()
        price, stock = cents(b.get("price")), b.get("stock")
        if ("price" in b and price is None) or ("stock" in b and (not is_int(stock) or stock < 0)):
            raise Invalid()
        if "price" in b:
            v["price"] = price
        if "stock" in b:
            change, v["stock"] = stock - v["stock"], stock
            self.move(vid, change, "adjustment")
        return p

    def units_sold(self, pid: str) -> int:
        return sum(l["quantity"] - l["returnedQuantity"] for o in self.orders.values() if o["status"] != "cancelled"
                   for l in o["lines"] if l["productId"] == pid)

    def rating(self, pid: str) -> dict:
        ratings = [r["rating"] for r in self.reviews.values() if r["productId"] == pid and r["status"] == "published"]
        if not ratings:
            return {"average": None, "count": 0}
        avg = (Decimal(sum(ratings)) / len(ratings)).quantize(Decimal("0.1"), rounding="ROUND_HALF_UP")
        return {"average": float(avg), "count": len(ratings)}

    def product_view(self, p: dict, lang: str | None = None) -> dict:
        first = p["variants"][0]
        name, description, values = self.texts(p, lang)
        variants = [{**v, "options": {k: values.get(x, x) for k, x in v["options"].items()}, "price": money(v["price"]),
                     "available": self.available(v["id"])} for v in p["variants"]]
        return {"id": p["id"], "shopId": p["shopId"], "sku": first["sku"], "name": name,
                "category": p["category"], "price": money(first["price"]),
                "stock": sum(v["stock"] for v in p["variants"]), "available": sum(v["available"] for v in variants),
                "options": list(p["options"]), "variants": variants,
                "attributes": dict(p["attributes"]), "personalization": copy.deepcopy(p["personalization"]),
                "unitsSold": self.units_sold(p["id"]), "rating": self.rating(p["id"]), "description": description,
                "translations": copy.deepcopy(p["translations"]), "active": p["active"], "source": p["source"],
                **({"lang": lang} if lang else {})}

    def product_filter(self, q: dict) -> list[dict]:
        shop, category = q.get("shopId"), q.get("category")
        attr: list[tuple[str, str, Any]] = []
        defs = self.defs(shop, category) if category else {}
        for k, raw in q.items():
            if not k.startswith("attr."):
                continue
            key, bound = k[5:], "eq"
            if key.endswith(".min") or key.endswith(".max"):
                key, bound = key[:-4], key[-3:]
            if not category or key not in defs or (bound != "eq" and defs[key]["type"] != "number"):
                raise Invalid()
            attr.append((key, bound, raw_value(defs[key], raw)))
        options = {k[7:]: v for k, v in q.items() if k.startswith("option.")}
        min_rating = float(q["minRating"]) if "minRating" in q else None
        if q.get("active", "true") not in ("true", "false"):
            raise Invalid()
        if "missingTranslation" in q:
            self.check_lang(shop, q["missingTranslation"])
        out = []
        for p in self.products.values():
            if p["shopId"] != shop or (category and p["category"] != category):
                continue
            if "sku" in q and not any(v["sku"] == q["sku"] for v in p["variants"]):
                continue
            if q.get("inStock") == "true" and not any(self.available(v["id"]) > 0 for v in p["variants"]):
                continue
            if "active" in q and p["active"] != (q["active"] == "true"):
                continue
            if "missingTranslation" in q and p["translations"].get(q["missingTranslation"], {}).get("name"):
                continue
            if min_rating is not None and (self.rating(p["id"])["average"] or 0) < min_rating:
                continue
            if not all(any(v["options"].get(n) == val for v in p["variants"]) for n, val in options.items()):
                continue
            ok = True
            for key, bound, want in attr:
                stored = p["attributes"].get(key)
                if bound == "eq":
                    ok = ok and same_value(stored, want)
                else:
                    ok = ok and is_num(stored) and (stored >= want if bound == "min" else stored <= want)
            if ok:
                out.append(p)
        if q.get("sort") == "bestselling":
            sold = {p["id"]: self.units_sold(p["id"]) for p in out}
            out.sort(key=lambda p: (-sold[p["id"]], p["name"]))
        if q.get("sort") == "rating":
            r = {p["id"]: self.rating(p["id"]) for p in out}
            out.sort(key=lambda p: (r[p["id"]]["average"] is None, -(r[p["id"]]["average"] or 0),
                                    -r[p["id"]]["count"], p["name"]))
        return out

    def facets(self, q: dict) -> dict:
        if not q.get("category"):
            raise Invalid()
        matching = self.product_filter(q)
        out: dict[str, dict[str, int]] = {}
        for d in self.attr_defs.get((q.get("shopId"), q["category"]), []):
            if d["type"] not in ("select", "boolean"):
                continue
            counts: dict[str, int] = {}
            for p in matching:
                v = p["attributes"].get(d["key"])
                if v is not None:
                    label = json.dumps(v) if isinstance(v, bool) else str(v)
                    counts[label] = counts.get(label, 0) + 1
            out[d["key"]] = counts
        return {"total": len(matching), "attributes": out}

    def add_attr_def(self, shop: str, category: str, b: dict) -> dict:
        if (not isinstance(b.get("key"), str) or not KEY.match(b["key"]) or not text(b.get("label"))
                or b.get("type") not in ATTR_TYPES or (b["type"] == "select" and not options_list(b.get("options")))):
            raise Invalid()
        defs = self.attr_defs.setdefault((shop, category), [])
        if any(d["key"] == b["key"] for d in defs):
            raise Conflict()
        d = {"key": b["key"], "label": b["label"], "type": b["type"]}
        if b["type"] == "select":
            d["options"] = list(b["options"])
        defs.append(d)
        return d

    def backfill_material(self) -> None:
        defs = self.attr_defs.setdefault(("northloom", "tops"), [])
        if not any(d["key"] == "material" for d in defs):
            defs.append(copy.deepcopy(MATERIAL))
        for p in self.products.values():
            if p["shopId"] == "northloom" and p["category"] == "tops":
                p["attributes"]["material"] = material(p["name"])

    # ------------------------------------------------------------ discounts

    def uses(self, shop: str, code: str) -> int:
        return sum(1 for o in self.orders.values() if o["shopId"] == shop and o["status"] != "cancelled"
                   and o["discount"] and o["discount"]["code"] == code)

    def add_discount(self, shop: str, b: dict) -> dict:
        code, kind, value = b.get("code"), b.get("type"), b.get("value")
        if not isinstance(code, str) or not CODE.match(code) or kind not in ("percent", "fixed"):
            raise Invalid()
        if kind == "percent" and not (is_num(value) and 0 < value <= 100):
            raise Invalid()
        if kind == "fixed" and not (cents(value) or 0) > 0:
            raise Invalid()
        if b.get("minSubtotal") is not None and cents(b["minSubtotal"]) is None:
            raise Invalid()
        if b.get("maxUses") is not None and not (is_int(b["maxUses"]) and b["maxUses"] >= 1):
            raise Invalid()
        if b.get("oncePerCustomer") is not None and not isinstance(b["oncePerCustomer"], bool):
            raise Invalid()
        for k in ("startsAt", "endsAt"):
            if b.get(k) is not None and parse_time(b[k]) is None:
                raise Invalid()
        codes = self.discounts.setdefault(shop, {})
        if code.upper() in codes:
            raise Conflict()
        d = {"code": code, "type": kind, "value": value, "minSubtotal": b.get("minSubtotal"),
             "maxUses": b.get("maxUses"), "oncePerCustomer": bool(b.get("oncePerCustomer")),
             "startsAt": b.get("startsAt"), "endsAt": b.get("endsAt"), "active": True}
        codes[code.upper()] = d
        return d

    def discount_view(self, shop: str, d: dict) -> dict:
        return {**d, "uses": self.uses(shop, d["code"])}

    def check_code(self, shop: str, email: str, raw: Any, subtotal: int) -> tuple[dict, int]:
        d = self.discounts.get(shop, {}).get(raw.upper()) if isinstance(raw, str) else None
        t = dt.datetime.now(dt.timezone.utc)
        if (not d or not d["active"] or (d["startsAt"] and t < parse_time(d["startsAt"]))  # type: ignore[operator]
                or (d["endsAt"] and t >= parse_time(d["endsAt"]))  # type: ignore[operator]
                or (d["minSubtotal"] is not None and subtotal < cents(d["minSubtotal"]))  # type: ignore[operator]
                or (d["maxUses"] is not None and self.uses(shop, d["code"]) >= d["maxUses"])):
            raise Invalid()
        if d["oncePerCustomer"] and any(
                o["shopId"] == shop and o["status"] != "cancelled" and o["discount"]
                and o["discount"]["code"] == d["code"] and o["customerEmail"].lower() == email.lower()
                for o in self.orders.values()):
            raise Invalid()
        if d["type"] == "percent":
            amount = round_cents(Decimal(subtotal) * Decimal(str(d["value"])) / 100)
        else:
            amount = min(cents(d["value"]), subtotal)  # type: ignore[type-var]
        return d, amount

    # ------------------------------------------------------------ orders

    def check_values(self, p: dict, values: Any) -> dict:
        if values is None:
            values = {}
        if not isinstance(values, dict):
            raise Invalid()
        values = {k: v for k, v in values.items() if v is not None}
        fields = {f["key"]: f for f in p["personalization"]}
        for k, v in values.items():
            f = fields.get(k)
            if (not f or (f["type"] == "text" and not (isinstance(v, str) and len(v) <= f.get("maxLength", len(v))))
                    or (f["type"] == "select" and v not in f["options"])
                    or (f["type"] == "boolean" and not isinstance(v, bool))):
                raise Invalid()
        for f in fields.values():
            if f["required"] and (f["key"] not in values or values[f["key"]] == ""):
                raise Invalid()
        return values

    def plan_order(self, b: dict, cart: dict | None = None) -> dict:
        shop, email, items = b.get("shopId"), b.get("customerEmail"), b.get("items")
        if not text(shop) or not valid_email(email) or not isinstance(items, list) or not items:
            raise Invalid()
        lang = self.check_lang(shop, b.get("lang"))
        lines = self.plan_lines(shop, items)
        subtotal = sum(v["price"] * q for _, v, q, _ in lines)
        code, amount = (None, 0)
        if b.get("discountCode") is not None:
            code, amount = self.check_code(shop, email, b["discountCode"], subtotal)
        delivery, fee = self.plan_delivery(shop, b.get("delivery"), subtotal - amount)
        need: dict[str, int] = {}
        for _, v, q, _ in lines:
            need[v["id"]] = need.get(v["id"], 0) + q
        if any(self.available(vid, cart["id"] if cart else None) < q for vid, q in need.items()):
            raise Conflict()
        return {"shop": shop, "email": email, "lines": lines, "subtotal": subtotal, "code": code, "amount": amount,
                "need": need, "lang": lang, "delivery": delivery, "fee": fee, "subscriptionId": b.get("_subscription")}

    def plan_lines(self, shop: str, items: list) -> list[tuple]:
        lines, seen = [], set()
        for it in items:
            if not isinstance(it, dict) or not is_int(it.get("quantity")) or it["quantity"] < 1:
                raise Invalid()
            vid, pid = it.get("variantId"), it.get("productId")
            if vid is not None:
                if not isinstance(vid, str) or vid not in self.variant_product:
                    raise Invalid()
                p = self.products[self.variant_product[vid]]
                if pid is not None and pid != p["id"]:
                    raise Invalid()
            elif isinstance(pid, str) and pid in self.products:
                p = self.products[pid]
                if len(p["variants"]) != 1:
                    raise Invalid()
                vid = p["variants"][0]["id"]
            else:
                raise Invalid()
            if p["shopId"] != shop or not p["active"]:
                raise Invalid()
            values = self.check_values(p, it.get("personalization"))
            key = (vid, json.dumps(values, sort_keys=True))
            if key in seen:
                raise Invalid()
            seen.add(key)
            lines.append((p, self.variant(vid), it["quantity"], values))
        return lines

    def plan_delivery(self, shop: str, d: Any, paid: int) -> tuple[dict | None, int]:
        if d is None:
            return None, 0
        if isinstance(d, dict) and d.get("type") == "shipping" and valid_location(d.get("location")):
            point = {"lat": d["location"]["lat"], "lng": d["location"]["lng"]}
            zones = [z for z in self.zones.values() if z["shopId"] == shop and inside(point, z["area"])]
            if not zones:
                raise Invalid()
            z = min(zones, key=lambda z: z["fee"])
            return {"type": "shipping", "location": point, "zoneId": z["id"]}, self.zone_fee(z, paid)
        if isinstance(d, dict) and d.get("type") == "pickup":
            pp = self.pickups.get(d.get("pickupPointId"))  # type: ignore[arg-type]
            if not pp or pp["shopId"] != shop:
                raise Invalid()
            return {"type": "pickup", "pickupPointId": pp["id"]}, 0
        raise Invalid()

    @staticmethod
    def zone_fee(z: dict, paid: int) -> int:
        return 0 if z["freeFrom"] is not None and paid >= z["freeFrom"] else z["fee"]

    @staticmethod
    def shares(amount: int, totals: list[int]) -> list[int]:
        out: list[int] = []
        subtotal = sum(totals)
        for i, total in enumerate(totals):
            out.append(amount - sum(out) if i == len(totals) - 1 else
                       round_cents(Decimal(amount) * total / subtotal) if subtotal else 0)
        return out

    def new_line(self, p: dict, v: dict, q: int, values: dict, lang: str | None) -> dict:
        name, _, translated = self.texts(p, lang)
        return {"id": uuid.uuid4().hex, "productId": p["id"], "variantId": v["id"], "sku": v["sku"], "name": name,
                "options": {k: translated.get(x, x) for k, x in v["options"].items()}, "unitPrice": v["price"],
                "quantity": q, "lineTotal": v["price"] * q, "personalization": values, "discount": 0,
                "returnedQuantity": 0, "refunded": 0}

    def commit_order(self, plan: dict) -> dict:
        oid = uuid.uuid4().hex
        for vid, q in plan["need"].items():
            self.variant(vid)["stock"] -= q
            self.move(vid, -q, "order", oid)
        shop, subtotal, amount = plan["shop"], plan["subtotal"], plan["amount"]
        self.numbers[shop] = self.numbers.get(shop, 1000) + 1
        lines = [self.new_line(p, v, q, values, plan["lang"]) for p, v, q, values in plan["lines"]]
        for line, share in zip(lines, self.shares(amount, [l["lineTotal"] for l in lines])):
            line["discount"] = share
        o = {"id": oid, "shopId": shop, "number": self.numbers[shop], "customerEmail": plan["email"],
             "status": "placed", "createdAt": now(), "lines": lines, "subtotal": subtotal,
             "discount": {"code": plan["code"]["code"], "amount": amount} if plan["code"] else None, "refunded": 0,
             "version": 1, "revisions": [], "delivery": plan["delivery"], "shippingFee": plan["fee"],
             "subscriptionId": plan["subscriptionId"], "lang": plan["lang"]}
        self.orders[o["id"]] = o
        return self.order_view(o)

    def place_order(self, b: dict) -> dict:
        if not self.racy:
            with self.lock:
                return self.commit_order(self.plan_order(b))
        with self.lock:
            plan = self.plan_order(b)
        time.sleep(0.1)
        with self.lock:
            return self.commit_order(plan)

    def order_view(self, o: dict) -> dict:
        items = []
        for l in o["lines"]:
            sku, name, price = l["sku"], l["name"], l["unitPrice"]
            p = self.products.get(l["productId"])
            if self.live and p:
                v = next((x for x in p["variants"] if x["id"] == l["variantId"]), None)
                sku, name, price = (v["sku"], p["name"], v["price"]) if v else (sku, name, price)
            items.append({"id": l["id"], "productId": l["productId"], "variantId": l["variantId"], "sku": sku,
                          "name": name, "options": dict(l["options"]), "unitPrice": money(price),
                          "quantity": l["quantity"], "lineTotal": money(l["lineTotal"]),
                          "personalization": dict(l["personalization"]), "discount": money(l["discount"]),
                          "returnedQuantity": l["returnedQuantity"]})
        amount = o["discount"]["amount"] if o["discount"] else 0
        return {"id": o["id"], "shopId": o["shopId"], "number": o["number"], "customerEmail": o["customerEmail"],
                "status": o["status"], "createdAt": o["createdAt"], "items": items, "subtotal": money(o["subtotal"]),
                "discount": {"code": o["discount"]["code"], "amount": money(amount)} if o["discount"] else None,
                "total": money(o["subtotal"] - amount + o["shippingFee"]), "refunded": money(o["refunded"]),
                "version": o["version"], "delivery": copy.deepcopy(o["delivery"]), "shippingFee": money(o["shippingFee"]),
                "subscriptionId": o["subscriptionId"]}

    def changing(self, o: dict) -> None:
        """Keep the order as it is now as a revision, before a change."""
        o["revisions"].append(self.order_view(o))
        o["version"] += 1

    def order_filter(self, q: dict) -> list[dict]:
        pers = {k[16:]: v for k, v in q.items() if k.startswith("personalization.")}
        out = []
        for o in self.orders.values():
            if o["shopId"] != q.get("shopId"):
                continue
            if "customerEmail" in q and o["customerEmail"].lower() != q["customerEmail"].lower():
                continue
            if "status" in q and o["status"] != q["status"]:
                continue
            if "productId" in q and not any(l["productId"] == q["productId"] for l in o["lines"]):
                continue
            if "variantId" in q and not any(l["variantId"] == q["variantId"] for l in o["lines"]):
                continue
            if "number" in q and str(o["number"]) != q["number"]:
                continue
            if "discountCode" in q and not (o["discount"] and o["discount"]["code"].upper() == q["discountCode"].upper()):
                continue
            if "pickupPointId" in q and (o["delivery"] or {}).get("pickupPointId") != q["pickupPointId"]:
                continue
            if "subscriptionId" in q and o["subscriptionId"] != q["subscriptionId"]:
                continue
            if not all(any(k in l["personalization"] and (json.dumps(l["personalization"][k])
                                                          if isinstance(l["personalization"][k], bool)
                                                          else l["personalization"][k]) == raw for l in o["lines"])
                       for k, raw in pers.items()):
                continue
            out.append(o)
        return out

    def cancel(self, o: dict) -> dict:
        if o["status"] != "placed":
            raise Conflict()
        self.changing(o)
        for l in o["lines"]:
            back = l["quantity"] - l["returnedQuantity"]
            self.variant(l["variantId"])["stock"] += back
            self.move(l["variantId"], back, "cancel", o["id"])
        o["status"] = "cancelled"
        return self.order_view(o)

    def add_return(self, o: dict, b: dict) -> dict:
        items, reason = b.get("items"), b.get("reason")
        if not isinstance(items, list) or not items or (reason is not None and not isinstance(reason, str)):
            raise Invalid()
        by_id = {l["id"]: l for l in o["lines"]}
        plan, seen = [], set()
        for it in items:
            if (not isinstance(it, dict) or it.get("lineId") not in by_id or it["lineId"] in seen
                    or not is_int(it.get("quantity")) or it["quantity"] < 1):
                raise Invalid()
            seen.add(it["lineId"])
            line = by_id[it["lineId"]]
            if line["personalization"]:
                raise Invalid()
            plan.append((line, it["quantity"]))
        if o["status"] == "cancelled" or any(l["returnedQuantity"] + q > l["quantity"] for l, q in plan):
            raise Conflict()
        self.changing(o)
        out = []
        for line, q in plan:
            paid = line["lineTotal"] - line["discount"]
            if line["returnedQuantity"] + q == line["quantity"]:
                refund = paid - line["refunded"]
            else:
                refund = round_cents(Decimal(paid) * q / line["quantity"])
            line["returnedQuantity"] += q
            line["refunded"] += refund
            self.variant(line["variantId"])["stock"] += q
            self.move(line["variantId"], q, "return", o["id"])
            out.append({"lineId": line["id"], "quantity": q, "refund": refund})
        total = sum(x["refund"] for x in out)
        o["refunded"] += total
        o["status"] = "returned" if all(l["returnedQuantity"] == l["quantity"] for l in o["lines"]) \
            else "partially_returned"
        r = {"id": uuid.uuid4().hex, "orderId": o["id"], "createdAt": now(), "reason": reason, "items": out,
             "refund": total}
        self.returns.setdefault(o["id"], []).append(r)
        return self.return_view(r)

    @staticmethod
    def return_view(r: dict) -> dict:
        return {**r, "items": [{**x, "refund": money(x["refund"])} for x in r["items"]], "refund": money(r["refund"])}

    # ------------------------------------------------------------ reviews

    def add_review(self, p: dict, b: dict) -> dict:
        email, rating, title, body = b.get("customerEmail"), b.get("rating"), b.get("title"), b.get("text")
        if (not valid_email(email) or not is_int(rating) or not 1 <= rating <= 5
                or not (isinstance(body, str) and body.strip() and len(body) <= 2000)
                or (title is not None and not (isinstance(title, str) and len(title) <= 100))):
            raise Invalid()
        if not any(o["shopId"] == p["shopId"] and o["status"] != "cancelled" and o["customerEmail"].lower() == email.lower()
                   and any(l["productId"] == p["id"] for l in o["lines"]) for o in self.orders.values()):
            raise Forbidden()
        if any(r["productId"] == p["id"] and r["customerEmail"].lower() == email.lower() for r in self.reviews.values()):
            raise Conflict()
        r = {"id": uuid.uuid4().hex, "productId": p["id"], "customerEmail": email, "rating": rating, "title": title,
             "text": body, "createdAt": now(), "status": "published", "voters": [], "seq": self.next_seq()}
        self.reviews[r["id"]] = r
        return r

    @staticmethod
    def review_view(r: dict) -> dict:
        return {**{k: v for k, v in r.items() if k not in ("voters", "seq")}, "helpful": len(r["voters"])}

    def patch_review(self, r: dict, b: dict) -> dict:
        if "rating" in b and not (is_int(b["rating"]) and 1 <= b["rating"] <= 5):
            raise Invalid()
        if "text" in b and not (isinstance(b["text"], str) and b["text"].strip() and len(b["text"]) <= 2000):
            raise Invalid()
        if "title" in b and b["title"] is not None and not (isinstance(b["title"], str) and len(b["title"]) <= 100):
            raise Invalid()
        if "status" in b and b["status"] not in ("published", "hidden"):
            raise Invalid()
        r.update({k: b[k] for k in ("rating", "title", "text", "status") if k in b})
        return r

    def product_reviews(self, pid: str, q: dict) -> list[dict]:
        out = [r for r in self.reviews.values() if r["productId"] == pid and r["status"] == "published"
               and ("rating" not in q or str(r["rating"]) == q["rating"])]
        out.sort(key=lambda r: -r["seq"])
        if q.get("sort") == "helpful":
            out.sort(key=lambda r: -len(r["voters"]))
        return [self.review_view(r) for r in out][:min(int(q.get("limit", 20)), 100)]

    # ------------------------------------------------------------ search

    def search(self, q: dict) -> list[dict]:
        shop, query = q.get("shopId"), (q.get("q") or "").strip()
        if not query:
            raise Invalid()
        lang = self.check_lang(shop, q.get("lang"))
        words, raw = stems(query), re.findall(r"\w+", fold(query))
        hits = []
        for p in self.product_filter({k: v for k, v in q.items() if k in ("shopId", "category", "inStock", "minRating")}):
            if not p["active"]:
                continue
            name, description, _ = self.texts(p, lang)
            texts = [name, description, p["category"], *(v["sku"] for v in p["variants"]),
                     *(v for v in p["attributes"].values() if isinstance(v, str))]

            def matches(pool: list[Any]) -> bool:
                found = {w for t in pool for w in stems(t)}
                prefixes = [w for t in pool if isinstance(t, str) for w in re.findall(r"\w+", fold(t))]
                return all(w in found or (i == len(words) - 1 and any(x.startswith(raw[-1]) for x in prefixes))
                           for i, w in enumerate(words))

            sku_hit = any(v["sku"].lower() == query.lower() for v in p["variants"])
            if sku_hit or matches(texts):
                hits.append((0 if matches([name]) else 1, -self.units_sold(p["id"]), name, p))
        hits.sort(key=lambda h: h[:3])
        return [self.product_view(h[3], lang) for h in hits][:min(int(q.get("limit", 20)), 100)]

    # ------------------------------------------------------------ carts

    def hold_seconds(self, shop: str) -> int:
        return self.settings.get(shop, {}).get("cartHoldSeconds", 900)

    def new_cart(self, b: dict) -> dict:
        if not text(b.get("shopId")) or not valid_email(b.get("customerEmail")):
            raise Invalid()
        c = {"id": uuid.uuid4().hex, "shopId": b["shopId"], "customerEmail": b["customerEmail"], "items": {},
             "status": "active", "expiresAt": utc() + dt.timedelta(seconds=self.hold_seconds(b["shopId"]))}
        self.carts[c["id"]] = c
        return c

    def cart_status(self, c: dict) -> str:
        return "expired" if c["status"] == "active" and utc() >= c["expiresAt"] else c["status"]

    def cart_view(self, c: dict) -> dict:
        items = []
        for vid, q in c["items"].items():
            v = self.variant(vid)
            items.append({"variantId": vid, "productId": self.variant_product[vid], "sku": v["sku"], "quantity": q,
                          "unitPrice": money(v["price"]), "lineTotal": money(v["price"] * q)})
        return {"id": c["id"], "shopId": c["shopId"], "customerEmail": c["customerEmail"], "status": self.cart_status(c),
                "items": items, "total": money(sum(self.variant(vid)["price"] * q for vid, q in c["items"].items())),
                "expiresAt": c["expiresAt"].isoformat(timespec="milliseconds").replace("+00:00", "Z")}

    def set_cart_item(self, c: dict, vid: str, b: dict) -> dict:
        if self.cart_status(c) != "active":
            raise Conflict()
        q = b.get("quantity")
        if not is_int(q) or q < 0 or vid not in self.variant_product:
            raise Invalid()
        p = self.products[self.variant_product[vid]]
        if p["shopId"] != c["shopId"] or not p["active"] or p["personalization"]:
            raise Invalid()
        if q > self.available(vid, c["id"]):
            raise Conflict()
        if q:
            c["items"][vid] = q
        else:
            c["items"].pop(vid, None)
        c["expiresAt"] = utc() + dt.timedelta(seconds=self.hold_seconds(c["shopId"]))
        return c

    def checkout(self, c: dict, b: dict) -> dict:
        if self.cart_status(c) != "active":
            raise Conflict()
        if not c["items"]:
            raise Invalid()
        body = {**{k: v for k, v in b.items() if k not in ("items", "shopId", "customerEmail")}, "shopId": c["shopId"],
                "customerEmail": c["customerEmail"], "items": [{"variantId": vid, "quantity": q} for vid, q in c["items"].items()]}
        order = self.commit_order(self.plan_order(body, cart=c))
        c["status"] = "checked_out"
        return order

    # ------------------------------------------------------------ delivery

    def add_zone(self, shop: str, b: dict) -> dict:
        fee, free = cents(b.get("fee")), cents(b.get("freeFrom")) if b.get("freeFrom") is not None else None
        if not text(b.get("name")) or fee is None or (b.get("freeFrom") is not None and free is None):
            raise Invalid()
        z = {"id": uuid.uuid4().hex, "shopId": shop, "name": b["name"], "area": polygon(b.get("area")), "fee": fee,
             "freeFrom": free}
        self.zones[z["id"]] = z
        return z

    @staticmethod
    def zone_view(z: dict) -> dict:
        return {**z, "fee": money(z["fee"]), "freeFrom": None if z["freeFrom"] is None else money(z["freeFrom"])}

    def pickup_points(self, shop: str, q: dict) -> list[dict]:
        out = [dict(pp) for pp in self.pickups.values() if pp["shopId"] == shop]
        if "near" in q:
            lat, lng = map(float, q["near"].split(","))
            centre = {"lat": lat, "lng": lng}
            if not valid_location(centre):
                raise Invalid()
            for pp in out:
                pp["_km"] = km_between(centre, pp["location"])
                pp["distanceKm"] = round(pp["_km"], 1)
            out.sort(key=lambda pp: pp["_km"])
        return [{k: v for k, v in pp.items() if k != "_km"} for pp in out][:min(int(q.get("limit", 50)), 500)]

    # ------------------------------------------------------------ subscriptions

    def check_subscription(self, shop: str, b: dict) -> None:
        items = b.get("items")
        if not isinstance(items, list) or not items or any(isinstance(i, dict) and "personalization" in i for i in items):
            raise Invalid()
        self.plan_lines(shop, items)
        if "everyWeeks" in b and not (is_int(b["everyWeeks"]) and 1 <= b["everyWeeks"] <= 52):
            raise Invalid()

    def new_subscription(self, b: dict) -> dict:
        shop = b.get("shopId")
        if not text(shop) or not valid_email(b.get("customerEmail")) or not valid_date(b.get("startsOn")) \
                or "everyWeeks" not in b:
            raise Invalid()
        self.check_subscription(shop, b)
        if b.get("delivery") is not None:
            self.plan_delivery(shop, b["delivery"], 0)
        s = {"id": uuid.uuid4().hex, "shopId": shop, "customerEmail": b["customerEmail"], "items": copy.deepcopy(b["items"]),
             "everyWeeks": b["everyWeeks"], "startsOn": b["startsOn"], "nextOn": b["startsOn"], "status": "active",
             "history": [], "delivery": copy.deepcopy(b.get("delivery"))}
        self.subscriptions[s["id"]] = s
        return s

    def patch_subscription(self, s: dict, b: dict) -> dict:
        if s["status"] == "cancelled":
            raise Conflict()
        if "status" in b and b["status"] not in ("active", "paused", "cancelled"):
            raise Invalid()
        if "items" in b or "everyWeeks" in b:
            self.check_subscription(s["shopId"], {"items": b.get("items", s["items"]), **b})
        if b.get("status") == "active" and s["status"] == "paused":
            s["nextOn"] = schedule_after(s["startsOn"], b.get("everyWeeks", s["everyWeeks"]),
                                         utc().date().isoformat(), inclusive=True)
        s.update({k: copy.deepcopy(b[k]) for k in ("status", "items", "everyWeeks") if k in b})
        return s

    def run_subscriptions(self, shop: str, b: dict) -> dict:
        day = b.get("date")
        if not valid_date(day):
            raise Invalid()
        placed, skipped = [], []
        for s in self.subscriptions.values():
            if s["shopId"] != shop or s["status"] != "active" or s["nextOn"] > day:
                continue
            due = s["nextOn"]
            try:
                order = self.commit_order(self.plan_order({"shopId": shop, "customerEmail": s["customerEmail"],
                                                           "items": s["items"], "delivery": s["delivery"],
                                                           "_subscription": s["id"]}))
                s["history"].append({"date": due, "orderId": order["id"]})
                placed.append(order["id"])
            except (Invalid, Conflict) as e:
                s["history"].append({"date": due, "skipped": "not enough stock" if isinstance(e, Conflict) else "invalid"})
                skipped.append(s["id"])
            s["nextOn"] = schedule_after(s["startsOn"], s["everyWeeks"], day, inclusive=False)
        return {"placed": placed, "skipped": skipped}

    # ------------------------------------------------------------ order edits

    def edit_order(self, o: dict, b: dict) -> dict:
        if o["status"] != "placed" or (not self.unversioned and b.get("version") != o["version"]):
            raise Conflict()
        if not isinstance(b.get("items"), list) or not b["items"]:
            raise Invalid()
        old = {(l["variantId"], json.dumps(l["personalization"], sort_keys=True)): l for l in o["lines"]}
        lines = []
        for p, v, q, values in self.plan_lines(o["shopId"], b["items"]):
            kept = old.get((v["id"], json.dumps(values, sort_keys=True)))
            line = copy.deepcopy(kept) if kept else self.new_line(p, v, q, values, o["lang"])
            line.update(quantity=q, lineTotal=line["unitPrice"] * q)
            lines.append(line)
        subtotal, amount = sum(l["lineTotal"] for l in lines), 0
        if o["discount"]:
            d = self.discounts[o["shopId"]][o["discount"]["code"].upper()]
            if d["minSubtotal"] is not None and subtotal < cents(d["minSubtotal"]):  # type: ignore[operator]
                raise Invalid()
            amount = (round_cents(Decimal(subtotal) * Decimal(str(d["value"])) / 100) if d["type"] == "percent"
                      else min(cents(d["value"]), subtotal))  # type: ignore[type-var]
        need: dict[str, int] = {}
        for l in lines:
            need[l["variantId"]] = need.get(l["variantId"], 0) + l["quantity"]
        for l in o["lines"]:
            need[l["variantId"]] = need.get(l["variantId"], 0) - l["quantity"]
        if any(q > self.available(vid) for vid, q in need.items()):
            raise Conflict()
        fee = o["shippingFee"]
        if o["delivery"] and o["delivery"]["type"] == "shipping" and o["delivery"]["zoneId"] in self.zones:
            fee = self.zone_fee(self.zones[o["delivery"]["zoneId"]], subtotal - amount)
        self.changing(o)
        for vid, q in need.items():
            self.variant(vid)["stock"] -= q
            self.move(vid, -q, "edit", o["id"])
        for line, share in zip(lines, self.shares(amount, [l["lineTotal"] for l in lines])):
            line["discount"] = share
        o.update(lines=lines, subtotal=subtotal, shippingFee=fee)
        if o["discount"]:
            o["discount"]["amount"] = amount
        return self.order_view(o)

    # ------------------------------------------------------------ Craftly import

    @staticmethod
    def craftly_price(v: Any) -> int:
        if not isinstance(v, dict) or not is_num(v.get("amount")) or not is_num(v.get("divisor")) or v["divisor"] <= 0:
            raise Invalid("missing price")
        price = cents(round(v["amount"] / v["divisor"], 2))
        if price is None:
            raise Invalid("invalid price")
        return price

    def parse_listing(self, raw: Any) -> dict:
        if not isinstance(raw, dict) or raw.get("listing_id") is None:
            raise Invalid("missing listing_id")
        if not text(raw.get("title")):
            raise Invalid("missing title")
        path = raw.get("taxonomy_path")
        if not isinstance(path, list) or not path or not text(path[-1]):
            raise Invalid("missing taxonomy_path")
        out = {"listingId": str(raw["listing_id"]), "name": raw["title"], "description": raw.get("description"),
               "category": path[-1].lower(), "active": raw.get("state") != "inactive", "options": [], "variants": []}
        var = raw.get("variations")
        if var:
            props = {str(x["property_id"]): x["name"].lower() for x in var.get("properties") or []}
            out["options"] = list(props.values())
            for off in var.get("offerings") or []:
                values = off.get("values") or {}
                if not text(off.get("sku")) or set(map(str, values)) != set(props):
                    raise Invalid("invalid offering")
                out["variants"].append({"sku": off["sku"], "options": {props[str(k)]: v for k, v in values.items()},
                                        "price": self.craftly_price(off.get("price", raw.get("price"))),
                                        "stock": off.get("quantity") if is_int(off.get("quantity")) else 0})
            if not out["variants"]:
                raise Invalid("no offerings")
        else:
            skus = raw.get("sku")
            if not isinstance(skus, list) or not skus or not text(skus[0]):
                raise Invalid("missing sku")
            out["variants"] = [{"sku": skus[0], "options": {}, "price": self.craftly_price(raw.get("price")),
                                "stock": raw.get("quantity") if is_int(raw.get("quantity")) else 0}]
        return out

    def import_craftly(self, shop: str, b: dict) -> dict:
        if not isinstance(b.get("listings"), list):
            raise Invalid()
        out: dict[str, Any] = {"created": 0, "updated": 0, "unchanged": 0, "rejected": []}
        owner = {v["sku"]: p["id"] for p in self.products.values() if p["shopId"] == shop for v in p["variants"]}
        by_listing = {p["source"]["listingId"]: p for p in self.products.values()
                      if p["shopId"] == shop and p["source"]}
        for raw in b["listings"]:
            try:
                spec = self.parse_listing(raw)
                p = by_listing.get(spec["listingId"])
                if any(owner.get(v["sku"], p and p["id"]) != (p and p["id"]) for v in spec["variants"]):
                    raise Invalid("sku used by another product")
            except Invalid as e:
                lid = raw.get("listing_id") if isinstance(raw, dict) else None
                out["rejected"].append({"listingId": None if lid is None else str(lid), "error": str(e) or "invalid"})
                continue
            if p is None:
                body = {"shopId": shop, "name": spec["name"], "category": spec["category"], "options": spec["options"],
                        "variants": [{**v, "price": money(v["price"])} for v in spec["variants"]],
                        "description": spec["description"], "active": spec["active"]}
                p = self.new_product(body)
                p["source"] = {"marketplace": "craftly", "listingId": spec["listingId"]}
                by_listing[spec["listingId"]] = p
                out["created"] += 1
            else:
                before = json.dumps(self.products[p["id"]], sort_keys=True)
                p = self.patch_product(p, {k: spec[k] for k in ("name", "category", "description", "active")})
                for v in spec["variants"]:
                    mine = next((x for x in p["variants"] if x["sku"] == v["sku"]), None)
                    if mine:
                        mine["price"] = v["price"]
                    elif v["options"].keys() == set(p["options"]):
                        self.add_variant(p, {**v, "price": money(v["price"])})
                by_listing[spec["listingId"]] = p
                out["updated" if json.dumps(p, sort_keys=True) != before else "unchanged"] += 1
            p["raw"] = copy.deepcopy(raw)
            for v in p["variants"]:
                owner[v["sku"]] = p["id"]
        return out

    # ------------------------------------------------------------ stock history

    def adjust(self, vid: str, b: dict) -> dict:
        change, note = b.get("change"), b.get("note")
        if not is_int(change) or change == 0 or (note is not None and not isinstance(note, str)):
            raise Invalid()
        v = self.variant(vid)
        if v["stock"] + change < 0:
            raise Conflict()
        v["stock"] += change
        return self.move(vid, change, "adjustment", note=note)  # type: ignore[return-value]

    def variant_movements(self, vid: str, q: dict) -> list[dict]:
        lo, hi = parse_time(q.get("from")), parse_time(q.get("to"))
        if ("from" in q and lo is None) or ("to" in q and hi is None):
            raise Invalid()
        out = [m for m in self.movements if m["variantId"] == vid
               and (lo is None or parse_time(m["at"]) >= lo) and (hi is None or parse_time(m["at"]) <= hi)]  # type: ignore[operator]
        return out[:min(int(q.get("limit", 100)), 1000)]

    def stock_at(self, q: dict) -> dict:
        at = parse_time(q.get("at"))
        if at is None:
            raise Invalid()
        totals: dict[str, int] = {}
        for m in self.movements:
            if parse_time(m["at"]) <= at:  # type: ignore[operator]
                totals[m["variantId"]] = totals.get(m["variantId"], 0) + m["change"]
        return {"variants": [{"variantId": v["id"], "sku": v["sku"], "stock": totals.get(v["id"], 0)}
                             for p in self.products.values() if p["shopId"] == q.get("shopId") for v in p["variants"]]}

    # ------------------------------------------------------------ reports

    def report(self, q: dict) -> dict:
        shop, group_by = q.get("shopId"), [k for k in (q.get("groupBy") or "").split(",") if k]
        if not shop or not group_by:
            raise Invalid()
        for k in group_by:
            if not (k in REPORT_KEYS or (k.startswith("option.") and len(k) > 7) or (k.startswith("attr.") and len(k) > 5)):
                raise Invalid()
        frm, to = q.get("from"), q.get("to")
        if (frm is not None and not valid_date(frm)) or (to is not None and not valid_date(to)):
            raise Invalid()
        groups: dict[tuple, dict] = {}
        for o in self.orders.values():
            if o["shopId"] != shop or o["status"] == "cancelled":
                continue
            day = o["createdAt"][:10]
            if (frm and day < frm) or (to and day > to):
                continue
            for l in o["lines"]:
                p = self.products.get(l["productId"])
                known = {"day": day, "month": day[:7], "productId": l["productId"], "variantId": l["variantId"],
                         "category": p["category"] if p else None,
                         "discountCode": o["discount"]["code"] if o["discount"] else None}
                key = tuple(known[k] if k in known else l["options"].get(k[7:]) if k.startswith("option.")
                            else (p["attributes"].get(k[5:]) if p else None) for k in group_by)
                g = groups.setdefault(key, {"orders": set(), "units": 0, "gross": 0, "discounts": 0, "refunds": 0})
                g["orders"].add(o["id"])
                g["units"] += l["quantity"] - l["returnedQuantity"]
                g["gross"] += l["lineTotal"]
                g["discounts"] += l["discount"]
                g["refunds"] += l["refunded"]
        return {"groups": [{**dict(zip(group_by, key)), "orders": len(g["orders"]), "units": g["units"],
                            "gross": money(g["gross"]), "discounts": money(g["discounts"]),
                            "refunds": money(g["refunds"]), "net": money(g["gross"] - g["discounts"] - g["refunds"])}
                           for key, g in groups.items()]}


class Handler(BaseHTTPRequestHandler):
    # Keep-alive: the checks make thousands of requests, and a new connection each exhausts Windows' ports.
    protocol_version = "HTTP/1.1"

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
        parts = [unquote(x) for x in url.path.strip("/").split("/")]
        q = dict(parse_qsl(url.query, keep_blank_values=True))
        try:
            raw = self.rfile.read(int(self.headers.get("Content-Length", 0))) if method in ("POST", "PATCH", "PUT") else b""
            body = json.loads(raw or b"{}")
            if method in ("POST", "PATCH", "PUT") and not isinstance(body, dict):
                raise Invalid()
            if (method, parts) == ("POST", ["orders"]):
                code, out = 201, self.st.place_order(body)
            else:
                with self.st.lock:
                    code, out = self.route(method, parts, q, body)
        except (Invalid, Forbidden, NotFound, Conflict) as e:
            code, out = e.code, {"error": type(e).__name__}
        except json.JSONDecodeError:
            code, out = 400, {"error": "bad JSON"}
        except ValueError:
            code, out = 400, {"error": "bad request"}
        self.send(code, out)

    def do_PUT(self) -> None:
        self.handle_any("PUT")

    def do_DELETE(self) -> None:
        self.handle_any("DELETE")

    def do_GET(self) -> None:
        self.handle_any("GET")

    def do_POST(self) -> None:
        self.handle_any("POST")

    def do_PATCH(self) -> None:
        self.handle_any("PATCH")

    def product(self, pid: str) -> dict:
        if pid not in self.st.products:
            raise NotFound()
        return self.st.products[pid]

    def order(self, oid: str) -> dict:
        if oid not in self.st.orders:
            raise NotFound()
        return self.st.orders[oid]

    @staticmethod
    def found(table: dict, key: str) -> dict:
        if key not in table:
            raise NotFound()
        return table[key]

    def route(self, method: str, parts: list[str], q: dict, b: dict) -> tuple[int, Any]:
        st = self.st
        limit = min(int(q.get("limit", 50)), 500)
        match method, parts:
            case "POST", ["__backfill_material"]:
                st.backfill_material()
                return 200, {}
            case "POST", ["products"]:
                return 201, st.product_view(st.new_product(b))
            case "GET", ["products", "facets"]:
                return 200, st.facets(q)
            case "GET", ["products", "search"]:
                return 200, {"results": st.search(q)}
            case "GET", ["products"]:
                lang = st.check_lang(q.get("shopId"), q.get("lang"))
                return 200, [st.product_view(p, lang) for p in st.product_filter(q)][:limit]
            case "GET", ["products", pid]:
                p = self.product(pid)
                return 200, st.product_view(p, st.check_lang(p["shopId"], q.get("lang")))
            case "GET", ["products", pid, "source"]:
                p = self.product(pid)
                if "raw" not in p:
                    raise NotFound()
                return 200, p["raw"]
            case "POST", ["products", pid, "reviews"]:
                return 201, st.review_view(st.add_review(self.product(pid), b))
            case "GET", ["products", pid, "reviews"]:
                return 200, st.product_reviews(self.product(pid)["id"], q)
            case "PATCH", ["reviews", rid]:
                return 200, st.review_view(st.patch_review(self.found(st.reviews, rid), b))
            case "POST", ["reviews", rid, "helpful"]:
                r = self.found(st.reviews, rid)
                if not valid_email(b.get("customerEmail")):
                    raise Invalid()
                if b["customerEmail"].lower() not in r["voters"]:
                    r["voters"].append(b["customerEmail"].lower())
                return 200, st.review_view(r)
            case "PATCH", ["products", pid]:
                return 200, st.product_view(st.patch_product(self.product(pid), b))
            case "POST", ["products", pid, "variants"]:
                return 201, st.product_view(st.add_variant(self.product(pid), b))
            case "PATCH", ["products", pid, "variants", vid]:
                return 200, st.product_view(st.patch_variant(self.product(pid), vid, b))
            case "GET", ["orders"]:
                return 200, [st.order_view(o) for o in st.order_filter(q)][:limit]
            case "GET", ["orders", oid]:
                return 200, st.order_view(self.order(oid))
            case "PATCH", ["orders", oid]:
                return 200, st.edit_order(self.order(oid), b)
            case "GET", ["orders", oid, "revisions"]:
                return 200, self.order(oid)["revisions"]
            case "POST", ["orders", oid, "cancel"]:
                return 200, st.cancel(self.order(oid))
            case "POST", ["orders", oid, "returns"]:
                return 201, st.add_return(self.order(oid), b)
            case "GET", ["orders", oid, "returns"]:
                return 200, [st.return_view(r) for r in st.returns.get(self.order(oid)["id"], [])]
            case "POST", ["shops", shop, "categories", category, "attributes"]:
                return 201, st.add_attr_def(shop, category, b)
            case "GET", ["shops", shop, "categories", category, "attributes"]:
                return 200, st.attr_defs.get((shop, category), [])
            case "POST", ["shops", shop, "discounts"]:
                return 201, st.discount_view(shop, st.add_discount(shop, b))
            case "GET", ["shops", shop, "discounts"]:
                return 200, [st.discount_view(shop, d) for d in st.discounts.get(shop, {}).values()]
            case "GET", ["shops", shop, "discounts", code]:
                d = st.discounts.get(shop, {}).get(code.upper())
                if not d:
                    raise NotFound()
                return 200, st.discount_view(shop, d)
            case "PATCH", ["shops", shop, "discounts", code]:
                d = st.discounts.get(shop, {}).get(code.upper())
                if not d:
                    raise NotFound()
                if not isinstance(b.get("active"), bool):
                    raise Invalid()
                d["active"] = b["active"]
                return 200, st.discount_view(shop, d)
            case "GET", ["reports", "sales"]:
                return 200, st.report(q)
            case "GET", ["reports", "stock"]:
                return 200, st.stock_at(q)
            case "PUT", ["shops", shop, "languages"]:
                default, others = b.get("default"), b.get("others", [])
                if (not isinstance(default, str) or not LANG.match(default) or not isinstance(others, list)
                        or not all(isinstance(x, str) and LANG.match(x) for x in others) or default in others
                        or len(set(others)) != len(others)):
                    raise Invalid()
                st.languages[shop] = {"default": default, "others": list(others)}
                return 200, st.languages[shop]
            case "GET", ["shops", shop, "languages"]:
                return 200, st.langs(shop)
            case "PUT", ["shops", shop, "settings"]:
                hold = b.get("cartHoldSeconds")
                if not is_int(hold) or not 5 <= hold <= 86400:
                    raise Invalid()
                st.settings[shop] = {"cartHoldSeconds": hold}
                return 200, st.settings[shop]
            case "GET", ["shops", shop, "settings"]:
                return 200, {"cartHoldSeconds": st.hold_seconds(shop)}
            case "POST", ["carts"]:
                return 201, st.cart_view(st.new_cart(b))
            case "GET", ["carts", cid]:
                return 200, st.cart_view(self.found(st.carts, cid))
            case "DELETE", ["carts", cid]:
                self.found(st.carts, cid)
                del st.carts[cid]
                return 204, None
            case "PUT", ["carts", cid, "items", vid]:
                return 200, st.cart_view(st.set_cart_item(self.found(st.carts, cid), vid, b))
            case "POST", ["carts", cid, "checkout"]:
                return 201, st.checkout(self.found(st.carts, cid), b)
            case "POST", ["shops", shop, "delivery-zones"]:
                return 201, st.zone_view(st.add_zone(shop, b))
            case "GET", ["shops", shop, "delivery-zones"]:
                return 200, [st.zone_view(z) for z in st.zones.values() if z["shopId"] == shop]
            case "DELETE", ["delivery-zones", zid]:
                self.found(st.zones, zid)
                del st.zones[zid]
                return 204, None
            case "POST", ["shops", shop, "pickup-points"]:
                if not text(b.get("name")) or not valid_location(b.get("location")):
                    raise Invalid()
                pp = {"id": uuid.uuid4().hex, "shopId": shop, "name": b["name"],
                      "location": {"lat": b["location"]["lat"], "lng": b["location"]["lng"]}}
                st.pickups[pp["id"]] = pp
                return 201, pp
            case "GET", ["shops", shop, "pickup-points"]:
                return 200, st.pickup_points(shop, q)
            case "POST", ["subscriptions"]:
                return 201, st.new_subscription(b)
            case "GET", ["subscriptions"]:
                return 200, [s for s in st.subscriptions.values() if s["shopId"] == q.get("shopId")
                             and q.get("status", s["status"]) == s["status"]
                             and q.get("customerEmail", s["customerEmail"]).lower() == s["customerEmail"].lower()][:limit]
            case "GET", ["subscriptions", sid]:
                return 200, self.found(st.subscriptions, sid)
            case "PATCH", ["subscriptions", sid]:
                return 200, st.patch_subscription(self.found(st.subscriptions, sid), b)
            case "POST", ["shops", shop, "subscriptions", "run"]:
                return 200, st.run_subscriptions(shop, b)
            case "POST", ["shops", shop, "imports", "craftly"]:
                return 200, st.import_craftly(shop, b)
            case "POST", ["variants", vid, "adjustments"]:
                if vid not in st.variant_product:
                    raise NotFound()
                return 201, st.adjust(vid, b)
            case "GET", ["variants", vid, "movements"]:
                if vid not in st.variant_product:
                    raise NotFound()
                return 200, st.variant_movements(vid, q)
        raise NotFound()


def serve(live_prices: bool = False, racy_stock: bool = False, lazy_expiry: bool = False,
          unversioned_edits: bool = False) -> tuple[ThreadingHTTPServer, str]:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    srv.state = State(live_prices, racy_stock, lazy_expiry, unversioned_edits)  # type: ignore[attr-defined]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"

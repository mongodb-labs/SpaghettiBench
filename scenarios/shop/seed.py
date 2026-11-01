"""Deterministic "production" data, created through the app's own API after the initial build.

The same RNG seed is used for every run, so both database arms get identical data. Some prices change
halfway through the orders, so older orders carry older prices. Two orders have fixed keys because
later checks act on them: `late_cancel` (cancelled in the variants phase) and `late_return` (partly
returned in the returns phase). Northloom's tops have names covering the edge cases of the material
rule.
"""
from __future__ import annotations

import random
from typing import Any

import httpx

from harness.suite import get_id

SEED = 20261002

SHOPS: dict[str, dict[str, Any]] = {
    "clay-and-kiln": {
        "prefix": "CK",
        "products": {
            "mugs": ["Speckled mug", "Tall latte mug", "Espresso cup pair", "Moon glaze mug", "Stoneware tankard"],
            "plates": ["Dinner plate – oat", "Side plate – ash", "Serving platter", "Dessert plate set"],
            "bowls": ["Ramen bowl", "Nesting bowls (3)", "Salad bowl – large", "Breakfast bowl"],
            "vases": ["Bud vase", "Tall cylinder vase", "Ikebana vase", "Pebble vase"],
        },
        "prices": (1200, 12000, 50),
        "customers": ["ines.moreau@example.com", "tom.becker@mail.de", "sofia.r@gmail.com", "li.wei@outlook.com",
                      "jane.doe@proton.me", "marek.nowak@wp.pl", "anna.k@example.org", "oli@studio-oli.co.uk",
                      "kenji.sato@example.jp", "ava.martin@gmail.com"],
        "orders": 45,
    },
    "northloom": {
        "prefix": "NL",
        "products": {
            # Edge cases of the material rule: blends, whole words only, upper case, "merino".
            "tops": ["Linen Camp Shirt", "Cotton-Linen Blend Tee", "Merino Crew Sweater", "Organic Cotton Tee",
                     "Cottonwood Print Tee", "WOOL Cardigan", "Heavyweight Hoodie", "Wool-Cotton Overshirt",
                     "Brushed cotton flannel shirt", "Woolly Mammoth Graphic Tee", "Merino-Silk Base Layer",
                     "Relaxed Poplin Shirt"],
            "bottoms": ["Wide-leg trousers", "Selvedge jeans", "Linen shorts", "Pleated skirt", "Cargo pants"],
            "accessories": ["Wool beanie", "Canvas tote", "Silk scarf", "Leather belt", "Cotton socks (3 pairs)"],
        },
        "prices": (1500, 16000, 100),
        "customers": ["maya.jones@gmail.com", "lukas.hofer@gmx.at", "chloe.b@example.fr", "sam.okafor@outlook.com",
                      "priya.n@example.in", "erik.lund@mail.se", "zoe.m@proton.me", "diego.f@gmail.com",
                      "hana.k@example.jp", "noah.w@icloud.com", "fatima.h@example.com", "ben.c@mail.co.uk"],
        "orders": 50,
    },
    "voltbox": {
        "prefix": "VB",
        "products": {
            "cables": ["USB-C to USB-C 1m", "USB-C to Lightning 2m", "Braided USB-C 3m", "HDMI 2.1 cable",
                       "Magnetic charging cable"],
            "chargers": ["65W GaN charger", "Travel charger 3-port", "Wireless charging pad", "Car charger dual",
                         "Power bank 10000mAh"],
            "cases": ["Laptop sleeve 14\"", "Phone case – clear", "Hard shell earbuds case", "Tablet folio"],
        },
        "prices": (799, 8999, 100),
        "customers": ["dev.patel@gmail.com", "kim.lee@example.kr", "olga.s@mail.ru", "tim.j@outlook.com",
                      "rosa.g@example.es", "yusuf.a@example.com", "emma.v@proton.me", "jonas.b@web.de"],
        "orders": 45,
    },
}


def run(base_url: str) -> dict[str, Any]:
    rng = random.Random(SEED)
    out: dict[str, Any] = {"products": [], "orders": [], "failures": []}
    with httpx.Client(base_url=base_url, timeout=30) as c:

        def call(kind: str, key: str, method: str, path: str, body: dict | None = None) -> dict | None:
            try:
                r = c.request(method, path, json=body)
                if r.status_code in (200, 201):
                    return r.json()
                out["failures"].append({"kind": kind, "key": key, "status": r.status_code, "body": r.text[:300]})
            except (httpx.HTTPError, ValueError) as e:
                out["failures"].append({"kind": kind, "key": key, "error": str(e)[:300]})
            return None

        for shop, spec in SHOPS.items():
            products: list[dict[str, Any]] = []
            n = 0
            for category, names in spec["products"].items():
                for name in names:
                    n += 1
                    lo, hi, step = spec["prices"]
                    price = rng.randrange(lo, hi, step)
                    stock = 0 if rng.random() < 0.1 else rng.randint(8, 60)
                    payload = {"shopId": shop, "sku": f"{spec['prefix']}-{n:03d}", "name": name,
                               "category": category, "price": price / 100, "stock": stock}
                    body = call("product", f"{shop}:p{n}", "POST", "/products", payload)
                    pid = get_id(body)
                    if pid:
                        entry = {"key": f"{shop}:p{n}", "shop": shop, "id": pid, "stock": stock,
                                 "payload": {k: v for k, v in payload.items() if k != "stock"}}
                        out["products"].append(entry)
                        products.append({"entry": entry, "cents": price, "left": stock})

            def place(key: str, items: list[tuple[dict, int]], email: str) -> dict | None:
                payload = {"shopId": shop, "customerEmail": email,
                           "items": [{"productId": p["entry"]["id"], "quantity": q} for p, q in items]}
                body = call("order", key, "POST", "/orders", payload)
                oid = get_id(body)
                if not oid:
                    return None
                lines = []
                for p, q in items:
                    p["left"] -= q
                    lines.append({"productId": p["entry"]["id"], "sku": p["entry"]["payload"]["sku"],
                                  "name": p["entry"]["payload"]["name"], "unitPrice": p["cents"] / 100,
                                  "quantity": q, "lineTotal": p["cents"] * q / 100})
                entry = {"key": key, "shop": shop, "id": oid, "payload": payload, "lines": lines,
                         "total": sum(p["cents"] * q for p, q in items) / 100, "cancelled": False}
                out["orders"].append(entry)
                return entry

            for i in range(spec["orders"]):
                if i == spec["orders"] // 2:
                    for p in rng.sample([p for p in products], 3):
                        p["cents"] += rng.choice([100, 250, 500])
                        if call("product", p["entry"]["key"], "PATCH", f"/products/{p['entry']['id']}",
                                {"price": p["cents"] / 100}):
                            p["entry"]["payload"]["price"] = p["cents"] / 100
                available = [p for p in products if p["left"] >= 3]
                picked = rng.sample(available, min(len(available), rng.choice([1, 1, 2, 2, 3])))
                if not picked:
                    continue
                email = rng.choice(spec["customers"])
                if rng.random() < 0.15:
                    email = email[0].upper() + email[1:]
                entry = place(f"{shop}:o{i}", [(p, rng.randint(1, 3)) for p in picked], email)
                if entry and rng.random() < 0.12:
                    if call("order", entry["key"], "POST", f"/orders/{entry['id']}/cancel"):
                        entry["cancelled"] = True
                        for line, p in zip(entry["lines"], picked):
                            p["left"] += line["quantity"]

            stocked = [p for p in products if p["left"] >= 3]
            if shop == "northloom":
                place("late_cancel", [(stocked[0], 1), (stocked[1], 2)], "late.cancel@example.com")
            if shop == "clay-and-kiln":
                place("late_return", [(stocked[0], 2)], "late.return@example.com")
    return out

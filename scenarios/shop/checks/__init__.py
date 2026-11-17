"""Hidden acceptance checks for the shop platform, run from the host against the deployed app.

One module per phase; each later phase re-runs the checks of all earlier ones. Every suite works in
fresh shops (ids nobody has used yet), so fixture checks can compare exact sets; checks on the seeded
"production" data compare against the seed and the app's own data. Leniencies beyond the shared ones
in `harness.suite`: amounts compare to a tenth of a cent and may be decimal strings, and email
addresses compare case-insensitively.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import httpx

from harness.scenario_file import load
from harness.suite import client
from scenarios.shop.checks.bestsellers import i2
from scenarios.shop.checks.carts import v10
from scenarios.shop.checks.category_attributes import v2
from scenarios.shop.checks.craftly_import import v14
from scenarios.shop.checks.delivery import v11
from scenarios.shop.checks.discount_codes import v4
from scenarios.shop.checks.initial import v0
from scenarios.shop.checks.order_edits import v13
from scenarios.shop.checks.order_numbers import i1
from scenarios.shop.checks.personalization import v3
from scenarios.shop.checks.returns import v5
from scenarios.shop.checks.reviews import v7
from scenarios.shop.checks.sales_reports import v6
from scenarios.shop.checks.search import v8
from scenarios.shop.checks.stock_history import v15
from scenarios.shop.checks.subscriptions import v12
from scenarios.shop.checks.translations import v9
from scenarios.shop.checks.variants import v1
from scenarios.shop.checks.vat import vat

__all__ = ["PHASES", "PROBES", "run", "run_probe"]

PHASES = [p["name"] for p in load(Path(__file__).parents[1])["phases"]]
# Change requests given to finished apps (harness.probe); each runs after all phases' checks.
PROBES: dict[str, Callable[[httpx.Client, dict | None], dict]] = {"vat": vat}


def run_probe(probe: str, base_url: str, seed: dict | None) -> dict:
    with client(base_url) as c:
        return PROBES[probe](c, seed)


def run(phase: str, base_url: str, seed: dict | None) -> dict:
    n = PHASES.index(phase)
    with client(base_url) as c:
        suites: dict[str, Callable[[], dict]] = {
            "initial": lambda: v0(c, seed), "variants": lambda: v1(c, seed), "order_numbers": lambda: i1(c, seed),
            "category_attributes": lambda: v2(c, seed), "personalization": lambda: v3(c, seed),
            "discount_codes": lambda: v4(c, seed), "returns": lambda: v5(c, seed), "bestsellers": lambda: i2(c, seed),
            "sales_reports": lambda: v6(c, seed),
            "reviews": lambda: v7(c, seed), "search": lambda: v8(c, seed), "translations": lambda: v9(c, seed),
            "carts": lambda: v10(c, seed), "delivery": lambda: v11(c, seed), "subscriptions": lambda: v12(c, seed),
            "order_edits": lambda: v13(c, seed), "craftly_import": lambda: v14(c, seed),
            "stock_history": lambda: v15(c, seed),
        }
        results: dict = {}
        for name in PHASES[: n + 1]:
            results.update(suites[name]())
        return results

"""Hidden acceptance checks for the agency CRM, run from the host against the deployed app.

One module per phase; each later phase re-runs the checks of all earlier ones. Every suite works in
fresh agencies (ids nobody has used yet), so fixture checks can compare exact sets; checks on the
seeded "production" data compare against the seed and the app's own data. Leniencies beyond the
shared ones in `harness.suite`: email addresses compare case-insensitively and phone numbers by their
digits.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from harness.scenario_file import load
from harness.suite import client
from scenarios.crm.checks.activity_timeline import v7
from scenarios.crm.checks.company_roles import v6
from scenarios.crm.checks.contact_merge import v5
from scenarios.crm.checks.currencies import v10
from scenarios.crm.checks.custom_fields import v2
from scenarios.crm.checks.follow_ups import i3
from scenarios.crm.checks.helpers import MERGED
from scenarios.crm.checks.import_csv import v12
from scenarios.crm.checks.inbound_leads import v3
from scenarios.crm.checks.initial import v0
from scenarios.crm.checks.multiple_emails import v1
from scenarios.crm.checks.name_search import i1
from scenarios.crm.checks.open_pipeline import i2
from scenarios.crm.checks.pipeline_reports import v4
from scenarios.crm.checks.search import v9
from scenarios.crm.checks.segments import v8
from scenarios.crm.checks.territories import v11
from scenarios.crm.checks.trash import v13
from scenarios.crm.checks.webhooks import v14

__all__ = ["PHASES", "run"]

PHASES = [p["name"] for p in load(Path(__file__).parents[1])["phases"]]


def run(phase: str, base_url: str, seed: dict | None) -> dict:
    n = PHASES.index(phase)
    exclude = MERGED if n >= PHASES.index("contact_merge") else frozenset()
    with client(base_url) as c:
        suites: dict[str, Callable[[], dict]] = {
            "initial": lambda: v0(c, seed, exclude), "multiple_emails": lambda: v1(c, seed, exclude),
            "name_search": lambda: i1(c, seed, exclude), "custom_fields": lambda: v2(c, seed),
            "inbound_leads": lambda: v3(c, seed), "pipeline_reports": lambda: v4(c, seed),
            "open_pipeline": lambda: i2(c, seed, exclude), "contact_merge": lambda: v5(c, seed),
            "company_roles": lambda: v6(c, seed, exclude),
            "activity_timeline": lambda: v7(c, seed), "segments": lambda: v8(c, seed),
            "follow_ups": lambda: i3(c, seed, exclude), "search": lambda: v9(c, seed, exclude),
            "currencies": lambda: v10(c, seed, exclude), "territories": lambda: v11(c, seed),
            "import": lambda: v12(c, seed), "trash": lambda: v13(c, seed), "webhooks": lambda: v14(c, seed),
        }
        results: dict = {}
        for name in PHASES[: n + 1]:
            results.update(suites[name]())
        return results

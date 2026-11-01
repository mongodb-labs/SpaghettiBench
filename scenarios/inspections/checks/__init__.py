"""Hidden acceptance checks for the field-inspections app, run from the host against the deployed app.

One module per phase; each later phase re-runs the checks of all earlier ones. Leniencies beyond the
shared ones in `harness.suite`: timestamps are compared as instants, and group-item `id`s are ignored
when comparing answers (they are checked separately in the multi-level phase).
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from harness.scenario_file import load
from harness.suite import client
from scenarios.inspections.checks.advanced_search import v4
from scenarios.inspections.checks.attachments import v22
from scenarios.inspections.checks.bulk_actions import v11
from scenarios.inspections.checks.compliance_reports import v6
from scenarios.inspections.checks.due_dates import i4
from scenarios.inspections.checks.export_import import v18
from scenarios.inspections.checks.findings import v8
from scenarios.inspections.checks.findings_dashboard import v16
from scenarios.inspections.checks.findings_search import v9
from scenarios.inspections.checks.form_versions import v3
from scenarios.inspections.checks.initial import v0
from scenarios.inspections.checks.item_api import v7
from scenarios.inspections.checks.item_catalog import v15
from scenarios.inspections.checks.item_history import v14
from scenarios.inspections.checks.move_items import v12
from scenarios.inspections.checks.multi_level import item_id_snapshot, v2
from scenarios.inspections.checks.offline_edits import v13
from scenarios.inspections.checks.offline_sync import v5
from scenarios.inspections.checks.reinspection import v20
from scenarios.inspections.checks.rename_field import i3
from scenarios.inspections.checks.repeating_groups import v1
from scenarios.inspections.checks.report_timezone import i2
from scenarios.inspections.checks.snapshots import v19
from scenarios.inspections.checks.submission_documents import v17
from scenarios.inspections.checks.text_filters import i1
from scenarios.inspections.checks.type_details import v21
from scenarios.inspections.checks.users_and_roles import v10

__all__ = ["PHASES", "run", "item_id_snapshot"]

PHASES = [p["name"] for p in load(Path(__file__).parents[1])["phases"]]


def run(phase: str, base_url: str, seed: dict | None) -> dict:
    n = PHASES.index(phase)
    with client(base_url) as c:
        # Suites run in phase order: v3 edits a seeded form after the earlier suites have checked it.
        suites: dict[str, Callable[[], dict]] = {
            "initial": lambda: v0(c, seed, exclude_forms={"acme_fire"} if n >= 1 else frozenset()),
            "repeating_groups": lambda: v1(c, seed), "multi_level": lambda: v2(c, seed),
            "text_filters": lambda: i1(c, seed), "form_versions": lambda: v3(c, seed),
            "advanced_search": lambda: v4(c, seed), "offline_sync": lambda: v5(c, seed),
            "compliance_reports": lambda: v6(c, seed), "report_timezone": lambda: i2(c, seed),
            "item_api": lambda: v7(c, seed), "findings": lambda: v8(c, seed),
            "findings_search": lambda: v9(c, seed), "users_and_roles": lambda: v10(c, seed),
            "bulk_actions": lambda: v11(c, seed), "move_items": lambda: v12(c, seed),
            "offline_edits": lambda: v13(c, seed), "item_history": lambda: v14(c, seed),
            "rename_field": lambda: i3(c, seed), "item_catalog": lambda: v15(c, seed),
            "findings_dashboard": lambda: v16(c, seed), "due_dates": lambda: i4(c, seed),
            "submission_documents": lambda: v17(c, seed), "export_import": lambda: v18(c, seed),
            "snapshots": lambda: v19(c, seed), "reinspection": lambda: v20(c, seed),
            "type_details": lambda: v21(c, seed), "attachments": lambda: v22(c, seed),
        }
        results: dict = {}
        for name in PHASES[: n + 1]:
            results.update(suites[name]())
        return results

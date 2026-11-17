"""Agency CRM: contacts, companies and deals for small agencies. Feature changes alternate between
favouring documents (multiple_emails, custom_fields, inbound_leads) and relations (pipeline_reports,
contact_merge, company_roles); two improvements change existing behaviour (name_search, open_pipeline)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from harness.scenario_file import load
from scenarios.crm import checks, seed, storage

_settings = load(Path(__file__).parent)
APP_DIR, DB_NAME, PORT = _settings["app_dir"], _settings["db_name"], _settings["port"]
PHASES = _settings["phases"]
MERGED = _settings["merged"]  # one-shot steps; empty if the scenario has none
PROBES = _settings["probes"]  # change requests for finished apps (harness.probe)


def seed_data(base_url: str) -> dict[str, Any]:
    return seed.run(base_url)


def run_checks(phase: str, base_url: str, seeded: dict[str, Any] | None) -> dict[str, Any]:
    return checks.run(phase, base_url, seeded)


def after_checks(run: Any, phase: str) -> dict[str, Any]:
    return {}


def storage_lines(run_dir: Path, first_row: dict[str, Any], row: dict[str, Any]) -> list[str]:
    return storage.lines(run_dir, first_row, row)

"""Field inspections: a form-builder backend whose customers' checklists keep changing shape."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from harness.scenario_file import load
from scenarios.inspections import checks, seed, storage

_settings = load(Path(__file__).parent)
APP_DIR, DB_NAME, PORT = _settings["app_dir"], _settings["db_name"], _settings["port"]
PHASES = _settings["phases"]


def seed_data(base_url: str) -> dict[str, Any]:
    return seed.run(base_url)


def seed_more(phase: str, base_url: str, seeded: dict[str, Any]) -> dict[str, Any] | None:
    """Production data added later: Tidewater's nested inspections once nested groups exist, and a few
    findings recorded without a due date once findings exist."""
    if phase == "report_timezone":
        return {"late": seed.run_late(base_url)}
    if phase == "findings" and seeded.get("late"):
        return {"late_findings": seed.run_late_findings(base_url, seeded["late"])}
    return None


def run_checks(phase: str, base_url: str, seeded: dict[str, Any] | None) -> dict[str, Any]:
    return checks.run(phase, base_url, seeded)


def after_checks(run: Any, phase: str) -> dict[str, Any]:
    """Checks that need the harness to act on the deployment (called only if the app started)."""
    if phase != "multi_level":
        return {}
    return {"v2.item_ids_stable_across_redeploy": redeploy_id_check(run, phase)}


def redeploy_id_check(run: Any, phase: str) -> dict[str, Any]:
    """Redeploy (restart + start.sh, which may re-run migrations) and check existing item ids didn't change."""
    try:
        before = checks.item_id_snapshot(run.base_url, run.seed_data)
        run.dc("restart", "workspace")
        if not run.start_app():
            return {"pass": False, "detail": "app did not start on redeploy"}
        after = checks.item_id_snapshot(run.base_url, run.seed_data)
    except Exception as e:  # a crashing app must fail the check, not the run
        return {"pass": False, "detail": f"{type(e).__name__}: {e}"[:500]}
    finally:
        (run.dir / f"app_{phase}_redeploy.log").write_text(run.sh("tail -c 200000 /tmp/app.log"), encoding="utf-8")
    changed = sum(1 for sid, ids in before.items() if after.get(sid) != ids)
    if not any(before.values()):
        return {"pass": False, "detail": "no item ids found on Acme's submissions"}
    return {"pass": changed == 0} if not changed else \
        {"pass": False, "detail": f"{changed}/{len(before)} Acme submissions have different item ids after redeploy"}


def storage_lines(run_dir: Path, first_row: dict[str, Any], row: dict[str, Any]) -> list[str]:
    result = storage.analyze(run_dir, first_row, row)
    return storage.summary(result) if result else ["storage check skipped (no seed data or dumps)"]

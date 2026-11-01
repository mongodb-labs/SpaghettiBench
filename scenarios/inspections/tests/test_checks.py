"""Tests for the harness's own checks: a correct app must pass everything, a subtly wrong one must fail."""
import httpx
import pytest

from scenarios import inspections
from scenarios.inspections import checks, seed
from scenarios.inspections.tests.reference_app import serve

SAME_ITEM_CHECKS = {
    "v1.group_filter_same_item",
    "v2.deep_filter_same_item",
    "v2.deep_filter_same_floor",
    "v2.deep_filter_same_floor_2",
    "v2.deep_filter_same_room",
    "v4.range_same_item",
    "v4.range_same_item_none",
    "v4.company_same_item",
    "i1.text_filter_same_item",
}
OWN_VERSION_CHECKS = {
    "v3.version_filter_old_type",
    "v3.version_filter_removed_field",
    "v3.seeded_filter_on_removed_field",
    "v4.seeded_contains_across_versions",
    "v5.late_upload_filter_old_version",
    "v5.seeded_late_upload_filterable",
}


LOST_UPDATE_CHECKS = {
    "v2.append_concurrent_no_lost_items",
    "v7.concurrent_patches_of_different_items",
    "v7.concurrent_patches_and_appends",
    "v8.concurrent_comments_never_lost",
    "v8.concurrent_comments_and_item_changes",
    "v11.concurrent_bulk_comments_and_edits",
    "v12.concurrent_moves_and_edits",
    "v13.concurrent_if_rev_exactly_one_wins",
    "v14.concurrent_changes_recorded_once",
    "v17.concurrent_uploads_one_wins",
    "v17.concurrent_changes_all_counted",
}
# The data change each phase asks for, done by the reference app's test-only endpoints.
MIGRATIONS = {"repeating_groups": "__migrate1", "multi_level": "__migrate2", "findings": "__backfill_findings",
              "rename_field": "__rename_notes", "due_dates": "__due_dates"}


def lifecycle(base: str) -> tuple[dict, dict]:
    """Seed, then per phase: its data change, any production data added after its deploy, its checks."""
    data = seed.run(base)
    results = {}
    for phase in checks.PHASES:
        if phase in MIGRATIONS:
            httpx.post(f"{base}/{MIGRATIONS[phase]}")
        data.update(inspections.seed_more(phase, base, data) or {})
        results[phase] = checks.run(phase, base, data)
    return data, results


@pytest.fixture(scope="module")
def reference():
    srv, base = serve()
    data, results = lifecycle(base)
    yield base, data, results
    srv.shutdown()


@pytest.fixture(scope="module")
def naive():
    srv, base = serve(naive_filters=True)
    _, results = lifecycle(base)
    yield results
    srv.shutdown()


@pytest.fixture(scope="module")
def latest_version():
    srv, base = serve(latest_version_filters=True)
    _, results = lifecycle(base)
    yield results
    srv.shutdown()


@pytest.fixture(scope="module")
def lost_updates():
    srv, base = serve(lost_updates=True)
    _, results = lifecycle(base)
    yield results
    srv.shutdown()


def test_correct_app_passes_every_check(reference):
    _, data, results = reference
    assert not data["failures"], data["failures"][:3]
    for key in ("late", "late_findings"):
        assert key in data and not data[key]["failures"], f"{key}: {data.get(key, {}).get('failures')}"
    failed = {f"{phase}: {k}": v.get("detail") for phase, res in results.items() for k, v in res.items() if not v["pass"]}
    assert not failed, "\n".join(f"{k}: {v}" for k, v in failed.items())


def test_checks_pass_again_when_rerun(reference):
    base, data, _ = reference
    rerun = checks.run(checks.PHASES[-1], base, data)
    assert not {k: v.get("detail") for k, v in rerun.items() if not v["pass"]}


def test_item_ids_survive_redeploy(reference):
    base, data, _ = reference
    before = checks.item_id_snapshot(base, data)
    httpx.post(f"{base}/__migrate2")
    assert any(before.values())
    assert checks.item_id_snapshot(base, data) == before


def test_naive_filter_fails_exactly_the_same_item_checks(naive):
    failed = {k for res in naive.values() for k, v in res.items() if not v["pass"]}
    assert failed == SAME_ITEM_CHECKS


def test_latest_version_filter_fails_exactly_the_own_version_checks(latest_version):
    failed = {k for res in latest_version.values() for k, v in res.items() if not v["pass"]}
    assert failed == OWN_VERSION_CHECKS


def test_lost_updates_fail_exactly_the_concurrent_change_checks(lost_updates):
    failed = {k for res in lost_updates.values() for k, v in res.items() if not v["pass"]}
    assert failed == LOST_UPDATE_CHECKS

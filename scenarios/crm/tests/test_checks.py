"""Tests for the CRM checks: a correct app must pass everything, a subtly wrong one must fail."""
import os

import httpx
import pytest

from scenarios.crm import checks, seed
from scenarios.crm.tests.reference_app import serve

os.environ["CHECKS_CALLBACK_HOST"] = "127.0.0.1"  # the reference app runs on this machine, not in Docker

STALE_NAME_CHECKS = {
    "v0.deal_follows_contact_company",
    "v0.deal_shows_current_contact_name",
    "v0.seeded_contacts_intact",  # after the company_roles rename, on every later stage
    "v4.report_reflects_current_data",
    "v5.merge_moves_deals",
    "v6.deal_follows_primary_company",
    "v6.rename_shows_everywhere",
    "v6.report_uses_primary_company",
    "v6.seeded_rename_shows_everywhere",
    "v9.reflects_current_data",
}
CASE_CHECKS = {
    "v0.filter_email_case_insensitive",
    "v0.seeded_filter_email",
    "v1.filter_email_any_position",
    "v3.lead_appends_new_addresses",
    "v3.lead_matches_case_insensitively",
    "v3.leadpipe_v2_matches_existing_contact",
    "v3.list_leads_by_contact",
    "v3.seeded_lead_links_existing_contact",
    "v5.lead_after_merge_links_kept_contact",
    "v5.merge_combines_emails",
    "v5.seeded_merged_contacts",
    "v9.searches_related_text",
    "v12.existing_contact_filled_in",
    "v12.import_counts",
    "v12.new_contacts_and_companies",
}
ANY_WORD_CHECKS = {"v9.every_word_must_match"}
FROZEN_RATE_CHECKS = {"v10.rate_changes_apply_everywhere"}


def lifecycle(base: str, until: str | None = None) -> tuple[dict, dict]:
    """Seed, then run every phase's checks; the seniority backfill is the only data change needing a step."""
    data = seed.run(base)
    results = {}
    for phase in checks.PHASES[: checks.PHASES.index(until) + 1 if until else None]:
        if phase == "custom_fields":
            httpx.post(f"{base}/__backfill_seniority")
        results[phase] = checks.run(phase, base, data)
    return data, results


def failed(results: dict) -> set[str]:
    return {k for res in results.values() for k, v in res.items() if not v["pass"]}


@pytest.fixture(scope="module")
def reference():
    srv, base = serve()
    data, results = lifecycle(base)
    yield base, data, results
    srv.shutdown()


@pytest.fixture(scope="module")
def stale():
    srv, base = serve(stale_names=True)
    _, results = lifecycle(base)
    yield results
    srv.shutdown()


@pytest.fixture(scope="module")
def case_sensitive():
    srv, base = serve(case_sensitive_emails=True)
    _, results = lifecycle(base)
    yield results
    srv.shutdown()


@pytest.fixture(scope="module")
def any_word():
    srv, base = serve(search_any_word=True)
    _, results = lifecycle(base, until="search")
    yield results
    srv.shutdown()


@pytest.fixture(scope="module")
def frozen():
    srv, base = serve(frozen_rates=True)
    _, results = lifecycle(base, until="currencies")
    yield results
    srv.shutdown()


def test_correct_app_passes_every_check(reference):
    _, data, results = reference
    assert not data["failures"], data["failures"][:3]
    for phase, res in results.items():
        bad = {k: v.get("detail") for k, v in res.items() if not v["pass"]}
        assert not bad, f"{phase}: {bad}"


def test_checks_pass_again_when_rerun(reference):
    base, data, _ = reference
    assert failed({"rerun": checks.run(checks.PHASES[-1], base, data)}) == set()


def test_stale_names_fail_exactly_the_current_name_checks(stale):
    assert failed(stale) == STALE_NAME_CHECKS


def test_case_sensitive_emails_fail_exactly_the_case_checks(case_sensitive):
    assert failed(case_sensitive) == CASE_CHECKS


def test_search_matching_any_word_fails_exactly_the_word_checks(any_word):
    assert failed(any_word) == ANY_WORD_CHECKS


def test_frozen_rates_fail_exactly_the_rate_change_check(frozen):
    assert failed(frozen) == FROZEN_RATE_CHECKS

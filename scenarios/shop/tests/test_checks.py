"""Tests for the shop checks: a correct app must pass everything, a subtly wrong one must fail."""
import httpx
import pytest

from scenarios.shop import checks, seed
from scenarios.shop.tests.reference_app import serve

LIVE_PRICE_CHECKS = {
    "v0.order_keeps_price_and_name_at_order_time",
    "v0.seeded_orders_intact",
    "v1.order_keeps_variant_price",
    "v9.orders_in_a_language",
    "v13.kept_lines_keep_their_price",
}
RACY_CHECKS = {
    "v0.concurrent_orders_never_oversell",
    "v0.concurrent_multi_item_orders_consistent",
    "v1.concurrent_variant_orders_never_oversell",
    "i1.concurrent_orders_numbered_without_gaps",
    "v4.concurrent_orders_respect_max_uses",
    "v4.concurrent_orders_respect_once_per_customer",
    "v15.movements_add_up_under_concurrency",
}
LAZY_EXPIRY_CHECKS = {"v10.expired_holds_free_stock_at_once"}
# the accepted stale edit also shifts the version and revision counts the next check expects
UNVERSIONED_CHECKS = {"v13.rejected_edits_change_nothing", "v13.concurrent_edits_one_wins", "v13.revisions_and_cancel"}


def lifecycle(base: str, until: str | None = None) -> tuple[dict, dict]:
    """Seed, then run every phase's checks; the material backfill is the only data change needing a step."""
    data = seed.run(base)
    results = {}
    for phase in checks.PHASES[: checks.PHASES.index(until) + 1 if until else None]:
        if phase == "category_attributes":
            httpx.post(f"{base}/__backfill_material")
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
def live_prices():
    srv, base = serve(live_prices=True)
    _, results = lifecycle(base)
    yield results
    srv.shutdown()


@pytest.fixture(scope="module")
def racy():
    srv, base = serve(racy_stock=True)
    _, results = lifecycle(base)
    yield results
    srv.shutdown()


@pytest.fixture(scope="module")
def lazy():
    srv, base = serve(lazy_expiry=True)
    _, results = lifecycle(base, until="carts")
    yield results
    srv.shutdown()


@pytest.fixture(scope="module")
def unversioned():
    srv, base = serve(unversioned_edits=True)
    _, results = lifecycle(base, until="order_edits")
    yield results
    srv.shutdown()


def test_correct_app_passes_every_check(reference):
    _, data, results = reference
    assert not data["failures"], data["failures"][:3]
    bad = {f"{phase}: {k}": v.get("detail") for phase, res in results.items() for k, v in res.items() if not v["pass"]}
    assert not bad, "\n".join(f"{k}: {v}" for k, v in bad.items())


def test_checks_pass_again_when_rerun(reference):
    base, data, _ = reference
    rerun = checks.run(checks.PHASES[-1], base, data)
    assert not {k: v.get("detail") for k, v in rerun.items() if not v["pass"]}


def test_live_prices_fail_exactly_the_price_at_order_time_checks(live_prices):
    assert failed(live_prices) == LIVE_PRICE_CHECKS


def test_racy_orders_fail_exactly_the_concurrent_order_checks(racy):
    assert failed(racy) == RACY_CHECKS


def test_lazy_expiry_fails_exactly_the_expiry_check(lazy):
    assert failed(lazy) == LAZY_EXPIRY_CHECKS


def test_unversioned_edits_fail_exactly_the_version_checks(unversioned):
    assert failed(unversioned) == UNVERSIONED_CHECKS

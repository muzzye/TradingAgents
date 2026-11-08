"""Valuation reconciliation: per-share math with consistent classes and dates.

Regression fixture: the BRK-B report of 2026-11-08 computed P/B against a
class-A book value with a class-B price — wrong by 1500x — and paired a
market cap with a share count from a different vintage. Nothing here is
per-ticker: the class ratio is an input, never derived from prices.
"""

import pytest

from tradingagents.dataflows.validator import OK, QUARANTINED, UNVERIFIED
from tradingagents.dataflows.valuation import EquityInputs, reconcile_valuation


def _single_class(**overrides) -> EquityInputs:
    """A clean single-class input set: price 100, equity 50e9, 1e9 shares."""
    base = dict(
        price=100.0,
        price_class=None,
        price_currency="USD",
        equity=50e9,
        equity_currency="USD",
        shares_outstanding=1e9,
        bvps_reported=None,
        bvps_class=None,
        bvps_currency=None,
        share_class_ratio=None,
    )
    base.update(overrides)
    return EquityInputs(**base)


class TestSingleClass:
    def test_bvps_market_cap_and_pb_are_computed(self):
        result = reconcile_valuation(_single_class())
        assert result.book_value_per_share == pytest.approx(50.0)
        assert result.market_cap == pytest.approx(100e9)
        assert result.price_to_book == pytest.approx(2.0)
        assert result.is_usable
        assert result.issues.status == OK

    def test_missing_shares_leaves_cap_and_bvps_none(self):
        result = reconcile_valuation(_single_class(shares_outstanding=None))
        assert result.book_value_per_share is None
        assert result.market_cap is None
        assert result.price_to_book is None

    def test_currency_mismatch_blocks_the_math(self):
        result = reconcile_valuation(_single_class(equity_currency="GBP"))
        assert result.issues.has_errors
        assert result.book_value_per_share is None
        assert result.issues.status == QUARANTINED

    def test_unknown_currency_is_a_warning_not_an_error(self):
        result = reconcile_valuation(_single_class(price_currency=None))
        assert not result.issues.has_errors
        assert result.issues.status == UNVERIFIED
        assert result.price_to_book == pytest.approx(2.0)


class TestShareClass:
    def test_reported_bvps_of_other_class_is_converted(self):
        # 1 A = 1500 B: a per-A BVPS of 75000 is 50 per B share.
        result = reconcile_valuation(_single_class(
            price_class="B",
            bvps_reported=75000.0,
            bvps_class="A",
            bvps_currency="USD",
            share_class_ratio=1500,
        ))
        assert result.book_value_per_share == pytest.approx(50.0)
        assert result.price_to_book == pytest.approx(2.0)
        assert result.is_usable
        codes = [i.code for i in result.issues.issues]
        assert "SHARE_CLASS_CONVERTED" in codes

    def test_unconvertible_cross_class_bvps_is_an_error(self):
        result = reconcile_valuation(_single_class(
            price_class="B",
            bvps_reported=75000.0,
            bvps_class="A",
            bvps_currency="USD",
            share_class_ratio=None,
        ))
        assert not result.is_usable
        assert result.price_to_book is None
        codes = [i.code for i in result.issues.issues]
        assert "SHARE_CLASS_MISMATCH" in codes

    def test_derived_bvps_uses_the_equity_and_share_count(self):
        # Equity and shares are per-B by construction here: 50e9 / 1e9 = 50.
        result = reconcile_valuation(_single_class(price_class="B"))
        assert result.book_value_per_share == pytest.approx(50.0)
        assert result.price_to_book == pytest.approx(2.0)


class TestReconciliation:
    def test_reported_bvps_beyond_materiality_is_an_error(self):
        result = reconcile_valuation(_single_class(
            bvps_reported=60.0, bvps_currency="USD"
        ))
        assert not result.is_usable
        codes = [i.code for i in result.issues.issues]
        assert "BVPS_MISMATCH" in codes

    def test_reported_bvps_within_materiality_is_a_warning(self):
        result = reconcile_valuation(_single_class(
            bvps_reported=50.2, bvps_currency="USD"
        ))
        assert result.is_usable
        codes = [i.code for i in result.issues.issues]
        assert "BVPS_MINOR_DRIFT" in codes

    def test_summary_shape(self):
        result = reconcile_valuation(_single_class())
        summary = result.summary()
        assert summary["price_to_book"] == pytest.approx(2.0)
        assert summary["status"] == OK
        assert summary["issues"] == []

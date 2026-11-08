"""Technical arithmetic: numeric moves and divergence checks over closes.

Regression fixture: the BRK-B report of 2026-11-08 stated a "-0.25%" move
and a "bearish divergence" the underlying closes did not support. The
arithmetic here is computed, never asserted.
"""

import pytest

from tradingagents.dataflows.technical_arithmetic import (
    check_divergence,
    last_session_move_pct,
    last_two_closes,
    pct_change,
    summarize_technicals,
)


def _rows(*closes: float) -> list[dict]:
    return [{"date": f"2026-11-{i + 1:02d}", "close": c} for i, c in enumerate(closes)]


class TestPctChange:
    def test_simple_move(self):
        assert pct_change(572.33, 570.89) == pytest.approx(-0.251655, abs=1e-4)

    def test_zero_previous_is_none(self):
        assert pct_change(0.0, 10.0) is None

    def test_missing_values_are_none(self):
        assert pct_change(None, 10.0) is None
        assert pct_change(10.0, None) is None


class TestLastTwoCloses:
    def test_returns_last_pair_in_order(self):
        assert last_two_closes(_rows(100.0, 101.0, 99.0)) == (101.0, 99.0)

    def test_fewer_than_two_rows_is_none(self):
        assert last_two_closes(_rows(100.0)) is None
        assert last_two_closes([]) is None


class TestLastSessionMove:
    def test_move_is_computed_from_the_last_pair(self):
        assert last_session_move_pct(_rows(572.33, 570.89)) == pytest.approx(
            -0.251655, abs=1e-4
        )

    def test_single_row_has_no_move(self):
        assert last_session_move_pct(_rows(570.89)) is None


class TestDivergence:
    def test_price_up_momentum_down_is_bearish(self):
        check = check_divergence([100.0, 102.0, 104.0], [70.0, 65.0, 60.0])
        assert check.is_divergence
        assert check.direction == "bearish"

    def test_price_down_momentum_up_is_bullish(self):
        check = check_divergence([104.0, 102.0, 100.0], [60.0, 65.0, 70.0])
        assert check.is_divergence
        assert check.direction == "bullish"

    def test_agreeing_trends_are_no_divergence(self):
        check = check_divergence([100.0, 102.0, 104.0], [60.0, 62.0, 64.0])
        assert not check.is_divergence
        assert check.direction == "none"

    def test_flat_series_is_no_divergence(self):
        check = check_divergence([100.0, 100.0, 100.0], [60.0, 60.0, 60.0])
        assert not check.is_divergence

    def test_short_series_is_no_divergence(self):
        check = check_divergence([100.0], [60.0])
        assert not check.is_divergence

    def test_flat_band_treats_small_moves_as_flat(self):
        check = check_divergence(
            [100.0, 100.2], [60.0, 59.9], flat_band=0.5
        )
        assert not check.is_divergence


class TestSummarize:
    def test_summary_carries_move_and_divergence(self):
        summary = summarize_technicals(_rows(572.33, 570.89))
        assert summary.last_move_pct == pytest.approx(-0.251655, abs=1e-4)
        assert summary.divergence is not None
        assert summary.divergence.direction == "none"
        assert any("last session" in n for n in summary.notes)

    def test_summary_with_momentum_series(self):
        rows = _rows(100.0, 102.0, 104.0)
        summary = summarize_technicals(rows, momentum=[70.0, 65.0, 60.0])
        assert summary.divergence.direction == "bearish"
        data = summary.summary()
        assert data["divergence"]["direction"] == "bearish"
        assert data["last_move_pct"] == pytest.approx(1.9608, abs=1e-3)

    def test_empty_rows_give_none_move(self):
        summary = summarize_technicals([])
        assert summary.last_move_pct is None

"""P0 MATH-005 / ENTRY-007 — arithmetic and signal validation.

Regressions from the BRK-B report of 2026-11-08:

- MATH-005: the 2026-11-05 session (O=584.23, C=572.33, prevC=579.09) was
  attributed -2.03% as *the* move; the open-to-close move is about -2.04%
  and the close-to-close move about -1.17%. Two quantities, one label.
- ENTRY-007: a 554-558 entry band requiring a close above 557.91 collapsed
  the signal candle and the order instant into one moment.

Pure logic, frozen numbers, no network.
"""

from __future__ import annotations

import pytest

from tradingagents.dataflows.signal_validator import (
    SignalTrigger,
    candle_range_pct,
    check_stated_move,
    close_to_close_return,
    next_session_after,
    open_to_close_return,
    validate_stop_side,
)

# The 2026-11-05 session as the report stated it.
OPEN_PX, CLOSE_PX, PREV_C = 584.23, 572.33, 579.09


class TestLabelledReturns:
    def test_open_to_close_is_about_minus_two_point_zero_four(self):
        assert open_to_close_return(OPEN_PX, CLOSE_PX) == pytest.approx(-2.0369, abs=1e-3)

    def test_close_to_close_is_about_minus_one_point_one_seven(self):
        assert close_to_close_return(PREV_C, CLOSE_PX) == pytest.approx(-1.1673, abs=1e-3)

    def test_the_two_quantities_are_distinct(self):
        """-2.03% was presented as the close-over-close move; it is not."""
        o2c = open_to_close_return(OPEN_PX, CLOSE_PX)
        c2c = close_to_close_return(PREV_C, CLOSE_PX)
        assert abs(o2c - c2c) > 0.5  # they differ by more than half a point

    def test_zero_open_is_none_not_a_crash(self):
        assert open_to_close_return(0.0, CLOSE_PX) is None

    def test_candle_range(self):
        assert candle_range_pct(586.0, 570.0, CLOSE_PX) == pytest.approx(2.793, abs=1e-2)

    def test_inverted_range_is_none(self):
        assert candle_range_pct(570.0, 586.0, CLOSE_PX) is None


class TestStatedMove:
    def test_the_reported_minus_two_point_zero_three_matches_open_to_close(self):
        result = check_stated_move(-2.03, OPEN_PX, CLOSE_PX, PREV_C)
        assert not result.has_errors

    def test_a_figure_matching_neither_quantity_is_an_error(self):
        """The report's other figure (-0.01% on 11-06) matched nothing."""
        result = check_stated_move(-0.01, OPEN_PX, CLOSE_PX, PREV_C)
        assert result.has_errors
        issue = result.issues[0]
        assert issue.code == "MOVE_MISMATCH"
        # The error names both true values so the label cannot be swapped.
        assert "open->close" in issue.expected and "close->close" in issue.expected

    def test_a_correct_close_to_close_figure_passes(self):
        result = check_stated_move(-1.17, OPEN_PX, CLOSE_PX, PREV_C)
        assert not result.has_errors


class TestSignalTrigger:
    def test_signal_and_eligibility_are_separate_moments(self):
        """The 554-558 band's trigger (close > 557.91) fires on one candle;
        the order may be placed only from the next session."""
        trigger = SignalTrigger(
            ticker="BRK-B", description="close above 557.91",
            trigger_price=557.91, signal_date="2026-11-06",
            eligible_from="2026-11-09",
        )
        assert trigger.condition_met(570.89)
        assert not trigger.is_orderable_on("2026-11-06")
        assert trigger.is_orderable_on("2026-11-09")

    def test_no_eligible_from_means_not_orderable(self):
        """A trigger never evaluated for orderability cannot order."""
        trigger = SignalTrigger(
            ticker="BRK-B", description="close above 557.91",
            trigger_price=557.91, signal_date="2026-11-06", eligible_from=None,
        )
        assert trigger.condition_met(570.89)
        assert not trigger.is_orderable_on("2026-11-09")

    def test_a_retroactive_eligibility_is_rejected(self):
        with pytest.raises(ValueError, match="retroactive"):
            SignalTrigger(
                ticker="BRK-B", description="x", trigger_price=557.91,
                signal_date="2026-11-06", eligible_from="2026-11-05",
            )

    def test_same_day_eligibility_is_allowed_but_explicit(self):
        """eligible_from == signal_date is the caller's explicit choice; the
        default (None) never collapses the two moments silently."""
        trigger = SignalTrigger(
            ticker="BRK-B", description="x", trigger_price=557.91,
            signal_date="2026-11-06", eligible_from="2026-11-06",
        )
        assert trigger.is_orderable_on("2026-11-06")

    def test_short_direction_condition(self):
        trigger = SignalTrigger(
            ticker="X", description="close below 50", trigger_price=50.0,
            signal_date="2026-11-06", eligible_from="2026-11-09",
            direction="SHORT",
        )
        assert trigger.condition_met(48.0)
        assert not trigger.condition_met(52.0)

    def test_an_unknown_direction_is_rejected(self):
        with pytest.raises(ValueError, match="LONG or SHORT"):
            SignalTrigger(
                ticker="X", description="x", trigger_price=50.0,
                signal_date="2026-11-06", eligible_from=None, direction="BOTH",
            )

    def test_condition_with_no_price_is_false(self):
        trigger = SignalTrigger(
            ticker="X", description="x", trigger_price=50.0,
            signal_date="2026-11-06", eligible_from=None,
        )
        assert not trigger.condition_met(None)


class TestNextSession:
    def test_friday_signal_eligible_monday(self):
        sessions = ["2026-11-04", "2026-11-05", "2026-11-06", "2026-11-09"]
        assert next_session_after("2026-11-06", sessions) == "2026-11-09"

    def test_no_later_session_is_none(self):
        assert next_session_after("2026-11-09", ["2026-11-06"]) is None


class TestStopSide:
    def test_long_stop_below_entry_passes(self):
        assert not validate_stop_side(100.0, 95.0, "LONG").has_errors

    def test_long_stop_at_or_above_entry_is_an_error(self):
        result = validate_stop_side(100.0, 100.0, "LONG")
        assert result.has_errors
        assert result.issues[0].code == "STOP_WRONG_SIDE"

    def test_short_stop_above_entry_passes(self):
        assert not validate_stop_side(100.0, 105.0, "SHORT").has_errors

    def test_short_stop_below_entry_is_an_error(self):
        result = validate_stop_side(100.0, 95.0, "SHORT")
        assert result.has_errors
        assert result.issues[0].code == "STOP_WRONG_SIDE"

    def test_unknown_direction_is_an_error(self):
        result = validate_stop_side(100.0, 95.0, "BOTH")
        assert result.has_errors
        assert result.issues[0].code == "STOP_DIRECTION_UNKNOWN"

    def test_nonpositive_levels_are_an_error(self):
        assert validate_stop_side(0.0, 95.0, "LONG").has_errors
        assert validate_stop_side(100.0, -1.0, "LONG").has_errors

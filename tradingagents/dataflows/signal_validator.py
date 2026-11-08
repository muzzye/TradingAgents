"""Arithmetic & signal validator (P0 MATH-005, ENTRY-007).

The BRK-B report attributed -2.03% to the 2026-11-05 session's
close-over-close move when the open-to-close move was about -2.04% and the
close-to-close move about -1.17%: two different quantities, one label. It
also proposed an entry band whose trigger ("close above 557.91") and the
order instant were the same candle — a signal and its execution collapsed
into one moment.

This module computes the quantities separately and types the trigger so a
report's stated numbers can be checked against the data, and a signal can
never be its own execution:

- ``open_to_close_return`` / ``close_to_close_return`` — labelled, distinct;
- ``candle_range_pct`` — the session's traded range;
- ``SignalTrigger`` — a conditional entry with its own ``eligible_from``
  date, so the candle that fires the signal is never the candle that
  executes it (no retroactive orders);
- ``validate_stop_side`` — a stop must sit on the protective side of the
  entry for the position's direction (LONG below, SHORT above).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime

from tradingagents.dataflows.validator import ValidationResult


def _finite(value: float | None) -> bool:
    return value is not None and math.isfinite(value)


def _pct(previous: float, current: float) -> float | None:
    if not (_finite(previous) and _finite(current)) or previous == 0:
        return None
    return (current - previous) / previous * 100.0


def open_to_close_return(open_price: float, close_price: float) -> float | None:
    """The session's open-to-close move in percent: what the candle itself did."""
    return _pct(open_price, close_price)


def close_to_close_return(previous_close: float, close_price: float) -> float | None:
    """The session's close-over-close move in percent: the day-over-day change."""
    return _pct(previous_close, close_price)


def candle_range_pct(high: float, low: float, close: float) -> float | None:
    """The session's traded range as a percent of the close."""
    if not (_finite(high) and _finite(low) and _finite(close)) or close == 0:
        return None
    if high < low:
        return None
    return (high - low) / close * 100.0


def check_stated_move(
    stated_pct: float,
    open_price: float,
    close_price: float,
    previous_close: float,
    *,
    tolerance_pct: float = 0.05,
) -> ValidationResult:
    """Whether a stated session move matches either labelled quantity.

    The report's figure is compared against both computations; matching
    neither is an error naming the two true values, so the label cannot be
    quietly swapped. Matching either passes — the report may legitimately
    describe either quantity, as long as the number is right for one.
    """
    result = ValidationResult()
    o2c = open_to_close_return(open_price, close_price)
    c2c = close_to_close_return(previous_close, close_price)
    matches_o2c = o2c is not None and abs(stated_pct - o2c) <= tolerance_pct
    matches_c2c = c2c is not None and abs(stated_pct - c2c) <= tolerance_pct
    if not (matches_o2c or matches_c2c):
        result.add(
            "MOVE_MISMATCH", "error",
            f"stated move {stated_pct:+.2f}% matches neither the open-to-close "
            f"({o2c:+.2f}%)" if o2c is not None else
            f"stated move {stated_pct:+.2f}% is not computable from the candle",
            expected=f"open->close {o2c:+.4f}% / close->close {c2c:+.4f}%",
            actual=stated_pct,
        )
    elif matches_o2c and matches_c2c and abs(o2c - c2c) > tolerance_pct:
        # Both match only when the two quantities coincide; otherwise the
        # label is ambiguous and must say which one it means.
        result.add(
            "MOVE_LABEL_AMBIGUOUS", "warning",
            f"the stated move matches both quantities within tolerance; "
            f"name which one it is (open->close {o2c:+.2f}% vs "
            f"close->close {c2c:+.2f}%)",
        )
    return result


# ---------------------------------------------------------------------------
# Signal triggers: the signal candle and the order instant are distinct
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SignalTrigger:
    """A conditional entry whose firing and eligibility are separate moments.

    ``signal_date`` is the session whose data fires the condition (e.g. a
    close above ``trigger_price``); ``eligible_from`` is the first session
    an order may be placed as a result — strictly after the signal candle,
    so the same candle never both proves the condition and executes it.
    A trigger with no ``eligible_from`` is not orderable: the caller must
    compute it (the next session) rather than default to the signal day.
    """

    ticker: str
    description: str                  # e.g. "close above 557.91"
    trigger_price: float | None       # the level the condition references
    signal_date: str                  # YYYY-MM-DD: the candle that fires it
    eligible_from: str | None         # YYYY-MM-DD: first orderable session
    direction: str = "LONG"           # LONG | SHORT

    def __post_init__(self):
        if self.direction not in ("LONG", "SHORT"):
            raise ValueError(f"direction must be LONG or SHORT, got {self.direction!r}")
        signal = _parse_date(self.signal_date)
        if signal is None:
            raise ValueError(f"signal_date must be YYYY-MM-DD, got {self.signal_date!r}")
        if self.eligible_from is not None:
            eligible = _parse_date(self.eligible_from)
            if eligible is None:
                raise ValueError(
                    f"eligible_from must be YYYY-MM-DD, got {self.eligible_from!r}"
                )
            if eligible < signal:
                raise ValueError(
                    f"eligible_from {self.eligible_from} precedes the signal "
                    f"date {self.signal_date}: a retroactive order is not a "
                    "trigger evaluation"
                )

    def is_orderable_on(self, session_date: str) -> bool:
        """Whether an order may be placed on ``session_date``.

        False when no eligible date was computed (the trigger was never
        evaluated for orderability) or the session precedes eligibility.
        """
        if self.eligible_from is None:
            return False
        eligible = _parse_date(self.eligible_from)
        session = _parse_date(session_date)
        if eligible is None or session is None:
            return False
        return session >= eligible

    def condition_met(self, close_price: float | None) -> bool:
        """Whether the trigger's price condition holds at ``close_price``."""
        if not _finite(close_price) or not _finite(self.trigger_price):
            return False
        if self.direction == "LONG":
            return close_price > self.trigger_price
        return close_price < self.trigger_price


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value).strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def next_session_after(signal_date: str, sessions: list[str]) -> str | None:
    """The first session strictly after ``signal_date``, or None.

    The caller computes ``eligible_from`` from the market calendar; this
    helper never guesses a date the calendar does not name.
    """
    signal = _parse_date(signal_date)
    if signal is None:
        return None
    parsed = sorted(
        s for s in (_parse_date(x) for x in sessions) if s is not None
    )
    for session in parsed:
        if session > signal:
            return f"{session:%Y-%m-%d}"
    return None


# ---------------------------------------------------------------------------
# Stop direction: a stop must protect the position's side
# ---------------------------------------------------------------------------


def validate_stop_side(
    entry: float,
    stop: float,
    direction: str = "LONG",
) -> ValidationResult:
    """Whether the stop sits on the protective side of the entry.

    A LONG's stop is below the entry; a SHORT's is above. A stop on the
    wrong side protects nothing — it is an error, not a wide stop. A
    strategic rebalancing level is not a stop and must not be validated as
    one; the caller passes only levels intended to cut the position.
    """
    result = ValidationResult()
    if direction not in ("LONG", "SHORT"):
        result.add("STOP_DIRECTION_UNKNOWN", "error",
                   f"direction {direction!r} is not LONG or SHORT")
        return result
    if not (_finite(entry) and _finite(stop)) or entry <= 0 or stop <= 0:
        result.add("STOP_LEVELS_INVALID", "error",
                   f"entry {entry} / stop {stop} must be positive finite prices")
        return result
    if direction == "LONG" and stop >= entry:
        result.add(
            "STOP_WRONG_SIDE", "error",
            f"a LONG stop ({stop}) must sit below the entry ({entry}); at or "
            "above it protects nothing",
            field="stop", expected="< entry", actual=stop,
        )
    if direction == "SHORT" and stop <= entry:
        result.add(
            "STOP_WRONG_SIDE", "error",
            f"a SHORT stop ({stop}) must sit above the entry ({entry}); at or "
            "below it protects nothing",
            field="stop", expected="> entry", actual=stop,
        )
    return result

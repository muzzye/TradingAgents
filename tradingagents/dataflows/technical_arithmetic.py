"""Technical arithmetic: numeric moves and pattern checks over verified closes.

The BRK-B report stated a "-0.25%" move and a "bearish divergence" that the
underlying closes did not support — prose arithmetic, not computed arithmetic.
This module computes the figures deterministically from the verified
snapshot's OHLCV rows so a report's stated numbers can be checked against
the data, and so the snapshot itself can carry the computed values.

All functions are pure and ticker-agnostic: they take rows (mappings with
``date``/``close``/``high``/``low`` keys) and return numbers or typed
results. No rounding is applied to stored values; formatting is the
report's job.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

Row = Mapping[str, Any]


def pct_change(previous: float, current: float) -> float | None:
    """Percentage change between two closes; None when not computable."""
    if previous is None or current is None:
        return None
    if not (math.isfinite(previous) and math.isfinite(current)) or previous == 0:
        return None
    return (current - previous) / previous * 100.0


def last_two_closes(rows: Sequence[Row]) -> tuple[float, float] | None:
    """The last two closes in row order; None when fewer than two rows."""
    closes = [r.get("close") for r in rows if r.get("close") is not None]
    if len(closes) < 2:
        return None
    return float(closes[-2]), float(closes[-1])


def last_session_move_pct(rows: Sequence[Row]) -> float | None:
    """The last session's close-over-close move in percent, or None."""
    pair = last_two_closes(rows)
    if pair is None:
        return None
    return pct_change(pair[0], pair[1])


@dataclass(frozen=True)
class DivergenceCheck:
    """Whether price and momentum agree, computed — not asserted."""

    direction: str                   # "bullish" | "bearish" | "none"
    price_trend: str                 # "up" | "down" | "flat"
    momentum_trend: str              # "up" | "down" | "flat"
    detail: str

    @property
    def is_divergence(self) -> bool:
        return self.direction != "none"


def _trend(values: Sequence[float | None], *, flat_band: float = 0.0) -> str:
    """Up/down/flat over a series, using first-to-last direction."""
    clean = [v for v in values if v is not None and math.isfinite(v)]
    if len(clean) < 2:
        return "flat"
    delta = clean[-1] - clean[0]
    if abs(delta) <= flat_band:
        return "flat"
    return "up" if delta > 0 else "down"


def check_divergence(
    closes: Sequence[float | None],
    momentum: Sequence[float | None],
    *,
    flat_band: float = 0.0,
) -> DivergenceCheck:
    """Price vs momentum divergence from two series of equal length.

    A divergence is a *computed* disagreement: price makes a higher high
    while momentum makes a lower high (bearish), or the reverse (bullish).
    With fewer than two points on either side, or when both trends agree,
    the verdict is "none" — never a pattern asserted from prose.
    """
    price_trend = _trend(closes, flat_band=flat_band)
    momentum_trend = _trend(momentum, flat_band=flat_band)
    if price_trend == "up" and momentum_trend == "down":
        direction, detail = "bearish", "price rising while momentum falls"
    elif price_trend == "down" and momentum_trend == "up":
        direction, detail = "bullish", "price falling while momentum rises"
    else:
        direction = "none"
        detail = f"price {price_trend}, momentum {momentum_trend}: no divergence"
    return DivergenceCheck(direction, price_trend, momentum_trend, detail)


@dataclass
class TechnicalSummary:
    """The computed figures the snapshot can carry beside its raw tables."""

    last_move_pct: float | None = None
    divergence: DivergenceCheck | None = None
    notes: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "last_move_pct": self.last_move_pct,
            "divergence": (
                {
                    "direction": self.divergence.direction,
                    "price_trend": self.divergence.price_trend,
                    "momentum_trend": self.divergence.momentum_trend,
                }
                if self.divergence else None
            ),
            "notes": list(self.notes),
        }


def summarize_technicals(
    rows: Sequence[Row],
    *,
    momentum: Sequence[float | None] | None = None,
    flat_band: float = 0.0,
) -> TechnicalSummary:
    """Compute the last move and the divergence verdict from verified rows.

    ``momentum`` defaults to the closes themselves (a price-only check);
    pass an indicator series (e.g. RSI) for a true price/momentum check.
    """
    summary = TechnicalSummary()
    summary.last_move_pct = last_session_move_pct(rows)
    series = list(momentum) if momentum is not None else [
        r.get("close") for r in rows
    ]
    closes = [r.get("close") for r in rows]
    summary.divergence = check_divergence(closes, series, flat_band=flat_band)
    if summary.last_move_pct is not None:
        summary.notes.append(
            f"last session close-over-close move: {summary.last_move_pct:+.4f}%"
        )
    if summary.divergence is not None:
        summary.notes.append(f"divergence check: {summary.divergence.detail}")
    return summary

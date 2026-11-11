"""Plan-number extraction (P0.5 wiring): entry/stop from the plan text.

The Execution Gate's risk checks need the plan's own numbers — entry,
stop, ATR, direction. The Trader's proposal states them in prose
("Entry 560.00, stop 552.07, ATR 8.0"); this module extracts them
deterministically so the exposure-capped sizing runs in the real flow
without explicit caller parameters.

Extraction is conservative: a number is accepted only from an explicit
labelled pattern (``entry``, ``stop``, ``atr``, ``long``/``short``), never
guessed from bare prices in the text. A plan whose numbers cannot be
extracted yields None fields — the caller's fail-closed rule then skips
the risk check rather than inventing values, exactly as the contract
requires.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Labelled money/number patterns. Structural, any ticker: the labels are
# the plan vocabulary, not per-ticker rules.
_ENTRY_RE = re.compile(
    r"\bentry(?:\s+price)?\s*[:=]?\s*\$?\s*(\d+(?:[.,]\d+)?)", re.IGNORECASE)
_STOP_RE = re.compile(
    r"\bstop(?:\s*-?\s*loss)?\s*[:=]?\s*\$?\s*(\d+(?:[.,]\d+)?)", re.IGNORECASE)
_ATR_RE = re.compile(
    r"\bATR(?:\s*\(\s*\d+\s*\))?\s*[:=]?\s*\$?\s*(\d+(?:[.,]\d+)?)",
    re.IGNORECASE)
_DIRECTION_RE = re.compile(r"\b(long|short)\b", re.IGNORECASE)
# FIX #5: how the plan intends to get in. Only an explicit phrase counts —
# "buy stop above 560", "breakout above 560", "limit buy at 540", "buy the dip
# below 540". A bare "Entry 540.00" states a level and says nothing about the
# comparison it implies, so the mode stays unstated and the trigger evaluator
# refuses to guess which side of the level the buyer wants.
_BREAKOUT_RE = re.compile(
    r"\b(?:buy[- ]stop|breakout|break\s+above|buy\s+(?:on\s+)?(?:a\s+)?break)\b",
    re.IGNORECASE)
_PULLBACK_RE = re.compile(
    r"\b(?:limit\s+buy|buy\s+limit|buy\s+the\s+dip|pullback|retracement|"
    r"buy\s+(?:on\s+)?(?:a\s+)?(?:dip|pullback))\b",
    re.IGNORECASE)
# The level the mode phrase itself names: "buy stop above 540.00",
# "breakout above 540", "limit buy at 540", "buy the dip below 540". Plans
# write the entry this way as often as with an "Entry" label, and without this
# the mode was recognised while its own number was not.
_MODE_LEVEL_RE = re.compile(
    r"\b(?:above|below|at|over|under)\s+\$?\s*(\d+(?:[.,]\d+)?)",
    re.IGNORECASE)


@dataclass(frozen=True)
class PlanNumbers:
    """The plan's numbers as extracted from its text (None when absent)."""

    entry: float | None = None
    stop: float | None = None
    atr: float | None = None
    direction: str = "LONG"
    # FIX #5: None means the plan did not state how it enters. It is
    # deliberately not defaulted to BREAKOUT: a plan that says only
    # "Entry 540.00" has not said whether it buys a break above 540 or a dip
    # below it, and guessing would manufacture a signal.
    entry_mode: str | None = None

    @property
    def has_plan(self) -> bool:
        """Whether at least an entry and a stop were extracted."""
        return self.entry is not None and self.stop is not None


def _number(raw: str) -> float | None:
    """Parse a plan number; a comma decimal separator is normalized."""
    try:
        return float(raw.replace(",", "."))
    except ValueError:
        return None


def extract_plan_numbers(plan_text: str | None) -> PlanNumbers:
    """Extract entry/stop/ATR/direction from the plan's own text.

    Only explicit labelled patterns count: "entry 560", "stop: 552.07",
    "ATR 8", "long"/"short". Bare numbers in prose are never guessed — a
    wrong extraction would size a position the plan never proposed.
    """
    if not plan_text:
        return PlanNumbers()

    entry = None
    stop = None
    atr = None

    entry_match = _ENTRY_RE.search(plan_text)
    if entry_match:
        entry = _number(entry_match.group(1))
    stop_match = _STOP_RE.search(plan_text)
    if stop_match:
        stop = _number(stop_match.group(1))
    atr_match = _ATR_RE.search(plan_text)
    if atr_match:
        atr = _number(atr_match.group(1))

    direction = "LONG"
    direction_match = _DIRECTION_RE.search(plan_text)
    if direction_match:
        direction = direction_match.group(1).upper()

    # FIX #5: both phrasings present is not a stated mode — it is an
    # ambiguity, and an ambiguous plan must not produce a signal.
    entry_mode = None
    if plan_text:
        is_breakout = bool(_BREAKOUT_RE.search(plan_text))
        is_pullback = bool(_PULLBACK_RE.search(plan_text))
        if is_breakout and not is_pullback:
            entry_mode = "BREAKOUT"
        elif is_pullback and not is_breakout:
            entry_mode = "PULLBACK"
        elif is_breakout and is_pullback:
            entry_mode = "AMBIGUOUS"

        # A mode phrase often carries the level itself. Only used when no
        # explicit "Entry" label was present, so an explicit label always wins.
        if entry is None and entry_mode in ("BREAKOUT", "PULLBACK", "AMBIGUOUS"):
            level = _MODE_LEVEL_RE.search(plan_text)
            if level:
                entry = _number(level.group(1))

    return PlanNumbers(entry=entry, stop=stop, atr=atr, direction=direction,
                       entry_mode=entry_mode)

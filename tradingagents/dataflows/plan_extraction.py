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


@dataclass(frozen=True)
class PlanNumbers:
    """The plan's numbers as extracted from its text (None when absent)."""

    entry: float | None = None
    stop: float | None = None
    atr: float | None = None
    direction: str = "LONG"

    @property
    def has_plan(self) -> bool:
        """Whether at least an entry and a stop were extracted."""
        return self.entry is not None and self.stop is not None


@dataclass(frozen=True)
class TranchePlan:
    """One leg of a plan, priced on its own entry and stop.

    A plan that states one entry and one stop is a single tranche — the
    degenerate case of the same code path, not a special one.
    """

    entry: float
    stop: float
    atr: float | None = None
    direction: str = "LONG"
    trigger: str | None = None

    @property
    def has_atr(self) -> bool:
        return self.atr is not None and self.atr > 0


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

    return PlanNumbers(entry=entry, stop=stop, atr=atr, direction=direction)


# A tranche line names its own entry and stop: "Tranche 2: entry 560.00,
# stop 547.00". The label is required — a bare pair of numbers in a sentence
# is prose, not a leg, and guessing one would size a position the plan never
# proposed.
_TRANCHE_RE = re.compile(
    r"tranche\s*(?:\d+)?\s*[:：-]?\s*(?P<body>[^\n]*)", re.IGNORECASE)
_LEG_ENTRY_RE = re.compile(
    r"\bentry(?:\s+price)?\s*[:=]?\s*\$?\s*(\d+(?:[.,]\d+)?)", re.IGNORECASE)
_LEG_STOP_RE = re.compile(
    r"\bstop(?:\s*-?\s*loss)?\s*[:=]?\s*\$?\s*(\d+(?:[.,]\d+)?)", re.IGNORECASE)


def extract_tranche_plans(
    plan_text: str | None,
    default_atr: float | None = None,
) -> list[TranchePlan]:
    """Extract the plan's legs, each with its own entry and stop.

    Two shapes are accepted, both from explicit labels only:

    - **Multi-leg**: lines labelled ``Tranche N:`` (or ``Tranche:``) each
      carrying an entry and a stop. A leg missing either is dropped: without
      a protective stop it cannot be priced, and inventing one would size a
      position the plan never proposed.
    - **Single leg**: the plan states one labelled entry and stop and no
      tranche labels. That is one tranche — the degenerate case of the same
      path, so callers need no branch.

    A plan that labels neither yields an empty list, never a guess. This is
    what keeps the Italian regression report's prose ("una tranche pari a
    ~25-30% della posizione obiettivo") from becoming an invented leg: it
    states a weight, not a price.

    ``default_atr`` supplies the ATR when the plan states it once for all
    legs (``ATR(14): 9.408`` on its own line) instead of per leg.
    """
    if not plan_text:
        return []

    shared_atr = None
    atr_match = _ATR_RE.search(plan_text)
    if atr_match:
        shared_atr = _number(atr_match.group(1))
    if shared_atr is None:
        shared_atr = default_atr

    direction_match = _DIRECTION_RE.search(plan_text)
    direction = direction_match.group(1).upper() if direction_match else "LONG"

    legs: list[TranchePlan] = []
    for match in _TRANCHE_RE.finditer(plan_text):
        body = match.group("body") or ""
        entry_match = _LEG_ENTRY_RE.search(body)
        stop_match = _LEG_STOP_RE.search(body)
        if not entry_match or not stop_match:
            continue
        entry = _number(entry_match.group(1))
        stop = _number(stop_match.group(1))
        if entry is None or stop is None:
            continue
        legs.append(TranchePlan(entry, stop, shared_atr, direction))

    if legs:
        return legs

    # No tranche labels: the plan's single labelled entry/stop is one leg.
    single = extract_plan_numbers(plan_text)
    if single.entry is not None and single.stop is not None:
        return [TranchePlan(single.entry, single.stop,
                            single.atr if single.atr is not None else shared_atr,
                            single.direction)]
    return []

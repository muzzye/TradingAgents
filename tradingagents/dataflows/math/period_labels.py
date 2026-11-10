"""Macro period labels (§5 of P2): YoY means twelve months, and nothing else.

A previous report called a comparison between September 2026 and November 2025
"YoY". Those periods are ten months apart, so the figure was a 10-month
change labelled as an annual one — a real number with a false label. Nothing in
the pipeline caught it, because the figure was computed correctly from the data;
only its *name* was wrong.

The labels carry an arithmetic contract, and each one is checkable:

``YoY``          the same period one year earlier (12 months apart)
``MoM``          the immediately preceding month (1 month apart)
``QoQ``          the immediately preceding quarter
``quarterly YoY`` the same quarter of the previous year (4 quarters)
``annualized``   a stated formula over a stated frequency — never a bare label

The check is arithmetic on the periods, not a spell-check on the words: two
labels can both say "YoY" and only one be right. This module reports what a
label claims and whether the periods support it, and refuses to compute growth
across periods that do not satisfy the claim.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from tradingagents.dataflows.math.precision import PERCENT_QUANTUM, quantize

_MONTHS = {name: index for index, name in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"], start=1)}

# Three-letter abbreviations, and the order the label's own words may take.
_SHORT_MONTHS = {name[:3]: index for name, index in _MONTHS.items()}


class PeriodKind(StrEnum):
    """The periodicity a growth label asserts."""

    YEAR_OVER_YEAR = "YEAR_OVER_YEAR"     # 12 months apart
    QUARTERLY_YOY = "QUARTERLY_YOY"       # same quarter, 4 quarters apart
    MONTH_OVER_MONTH = "MONTH_OVER_MONTH"  # 1 month apart
    QUARTER_OVER_QUARTER = "QUARTER_OVER_QUARTER"  # 1 quarter apart
    ANNUALIZED = "ANNUALIZED"             # a stated formula over a stated frequency
    UNKNOWN = "UNKNOWN"


class PeriodVerdict(StrEnum):
    """Whether the periods satisfy the label's arithmetic."""

    CONSISTENT = "CONSISTENT"
    PERIOD_MISMATCH = "PERIOD_MISMATCH"   # the label does not describe the periods
    UNVERIFIED = "UNVERIFIED"             # a period could not be read


# The exact distance each label requires, in months. This is the contract.
_REQUIRED_MONTHS: dict[PeriodKind, int] = {
    PeriodKind.YEAR_OVER_YEAR: 12,
    PeriodKind.QUARTERLY_YOY: 12,
    PeriodKind.MONTH_OVER_MONTH: 1,
    PeriodKind.QUARTER_OVER_QUARTER: 3,
}


@dataclass(frozen=True)
class PeriodLabel:
    """One labelled period: what it says it is, and where it ends.

    ``year``/``month``/``quarter`` describe the observation; ``raw`` keeps the
    text as written so a report can show what the analyst actually said.
    """

    raw: str
    year: int
    month: int | None = None
    quarter: int | None = None
    kind: PeriodKind = PeriodKind.UNKNOWN

    @property
    def months_from_epoch(self) -> int | None:
        """The period as a month index, so two periods can be subtracted.

        A quarter resolves to its last month (Q3 2026 → 2026-09), which is how
        a quarterly comparison and a monthly one are made commensurable.
        """
        if self.quarter is not None:
            return self.year * 12 + self.quarter * 3
        if self.month is not None:
            return self.year * 12 + self.month
        return self.year * 12 + 12


def parse_period(text: str | None) -> PeriodLabel | None:
    """Read a period out of a label such as ``Sep 2026``, ``2026-Q3`` or ``FY2025``."""
    if not text:
        return None
    raw = str(text).strip()

    # A quarter, in any of the spellings the vendors use.
    quarter = re.search(r"(?:^|\b)(?:Q([1-4])[\s-]*(\d{4})|(\d{4})[\s-]*Q([1-4]))", raw, re.I)
    if quarter:
        q = int(quarter.group(1) or quarter.group(4))
        year = int(quarter.group(2) or quarter.group(3))
        return PeriodLabel(raw=raw, year=year, quarter=q)

    # A month by name, with the year beside it.
    for name, index in _MONTHS.items():
        if re.search(rf"\b{name[:3]}", raw, re.I):
            year = re.search(r"(\d{4})", raw)
            if year:
                return PeriodLabel(raw=raw, year=int(year.group(1)), month=index)
    for name, index in _SHORT_MONTHS.items():
        if re.search(rf"\b{name}\b", raw, re.I):
            year = re.search(r"(\d{4})", raw)
            if year:
                return PeriodLabel(raw=raw, year=int(year.group(1)), month=index)

    # A bare year, possibly fiscal.
    year = re.search(r"(\d{4})", raw)
    if year:
        return PeriodLabel(raw=raw, year=int(year.group(1)))
    return None


def label_kind(text: str | None) -> PeriodKind:
    """What periodicity a growth label claims, from its wording."""
    if not text:
        return PeriodKind.UNKNOWN
    low = str(text).lower()
    if "annualis" in low or "annualiz" in low or "annualised" in low:
        return PeriodKind.ANNUALIZED
    if "yoy" in low or "year-over-year" in low or "year over year" in low \
            or "year-on-year" in low or "a/a" in low:
        # A quarterly YoY is still a YoY: checked by the same 12-month rule.
        if "q" in low and re.search(r"\bq[1-4]\b", low):
            return PeriodKind.QUARTERLY_YOY
        return PeriodKind.YEAR_OVER_YEAR
    if "mom" in low or "month-over-month" in low or "month over month" in low \
            or "m/m" in low:
        return PeriodKind.MONTH_OVER_MONTH
    if "qoq" in low or "quarter-over-quarter" in low or "quarter over quarter" in low \
            or "q/q" in low:
        return PeriodKind.QUARTER_OVER_QUARTER
    return PeriodKind.UNKNOWN


@dataclass(frozen=True)
class PeriodCheck:
    """Whether two periods satisfy the label applied to them."""

    kind: PeriodKind
    verdict: PeriodVerdict
    earlier: str | None = None
    later: str | None = None
    months_apart: int | None = None
    required_months: int | None = None
    reason: str = ""

    @property
    def blocks(self) -> bool:
        """A mislabelled period may not be quoted as the growth it claims."""
        return self.verdict is PeriodVerdict.PERIOD_MISMATCH

    def summary(self) -> dict:
        return {
            "label_kind": str(self.kind),
            "verdict": str(self.verdict),
            "earlier": self.earlier,
            "later": self.later,
            "months_apart": self.months_apart,
            "required_months": self.required_months,
            "blocks": self.blocks,
            "reason": self.reason,
        }


def check_period_label(label: str | None, earlier: str | None,
                       later: str | None) -> PeriodCheck:
    """Does ``label`` describe the distance between ``earlier`` and ``later``?

    The audit's case: label "YoY", periods September 2026 and November 2025 —
    ten months apart, so ``PERIOD_MISMATCH``.
    """
    kind = label_kind(label)
    if kind is PeriodKind.UNKNOWN:
        return PeriodCheck(kind, PeriodVerdict.UNVERIFIED,
                           earlier, later, reason=f"'{label}' names no periodicity")

    if kind is PeriodKind.ANNUALIZED:
        # Annualising is a formula, not a period pair: it must state both.
        return PeriodCheck(kind, PeriodVerdict.UNVERIFIED, earlier, later,
                           reason=("an annualized figure must state its formula "
                                   "and frequency; the label alone checks nothing"))

    early, late = parse_period(earlier), parse_period(later)
    if early is None or late is None:
        return PeriodCheck(kind, PeriodVerdict.UNVERIFIED, earlier, later,
                           reason="a period could not be read from the label")

    distance = abs(late.months_from_epoch - early.months_from_epoch)
    required = _REQUIRED_MONTHS[kind]
    if distance == required:
        return PeriodCheck(kind, PeriodVerdict.CONSISTENT, earlier, later,
                           distance, required,
                           reason=f"{label} requires {required} months and the "
                                  f"periods are {distance} apart")
    return PeriodCheck(kind, PeriodVerdict.PERIOD_MISMATCH, earlier, later,
                       distance, required,
                       reason=(f"{label} requires {required} months but "
                               f"{earlier} to {later} is {distance}: the label "
                               "does not describe the periods"))


def growth_for_label(earlier: float | None, later: float | None,
                     label: str | None,
                     earlier_period: str | None = None,
                     later_period: str | None = None,
                     ) -> tuple[Decimal | None, PeriodCheck]:
    """The growth and the label check, refusing a mismatched pair.

    Returning ``None`` for a ``PERIOD_MISMATCH`` is deliberate: the arithmetic
    would produce a number, and a number is exactly what a reader would quote
    without reading the label.
    """
    check = check_period_label(label, earlier_period, later_period)
    if check.blocks:
        return None, check
    if earlier is None or later is None or earlier == 0:
        return None, check
    value = (Decimal(str(later)) / Decimal(str(earlier)) - 1) * 100
    return quantize(value, PERCENT_QUANTUM), check

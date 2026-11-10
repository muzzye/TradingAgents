"""Numeric claims validator (§9): never trust a number because a model said it.

Agents write prose containing figures — "about 9.5% above the 50-day", "P/B
of 1.35x", "stop at 2.5 ATR". Some are rounding of a real computation, some
are estimates, and some are simply wrong. This module classifies a claim and
decides whether it may reach a decision.

The classification the task requires is explicit:

``COMPUTED``
    The engine has a verified value for this metric. The claim is compared.
``OBSERVED``
    A figure read off a source (a close price). Checkable when the source
    value is supplied.
``ESTIMATE`` / ``FORECAST`` / ``SCENARIO``
    The agent's own projection. Not checked against reality, and never
    promoted to a fact.
``UNSUPPORTED``
    Nothing to check it against. It cannot authorize an order.

The extractor is deliberately **not** the security boundary. The task says
it plainly: "Non basare la sicurezza esclusivamente sull'estrazione di
numeri da testo libero tramite regex." Regex here only *finds* claims so
they can be reported; what blocks execution is the structured check in
``check_claim`` against an engine-computed metric. A number that regex
misses is not thereby trusted — it simply never became a verified fact,
which is the fail-closed default.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any

from tradingagents.dataflows.math.metric_record import (
    CalculationStatus,
    MetricRecord,
    SourceStatus,
    metric,
)
from tradingagents.dataflows.math.precision import (
    PERCENT_QUANTUM,
    RATIO_QUANTUM,
    as_decimal,
    quantize,
)


class ClaimKind(StrEnum):
    """What kind of numeric statement an agent made."""

    COMPUTED = "COMPUTED"          # a derived metric the engine owns
    OBSERVED = "OBSERVED"          # read from a source
    ESTIMATE = "ESTIMATE"          # analyst estimate
    FORECAST = "FORECAST"          # projection
    SCENARIO = "SCENARIO"          # hypothetical
    UNSUPPORTED = "UNSUPPORTED"    # nothing to check against


class ClaimVerdict(StrEnum):
    """The outcome of checking a claim."""

    MATCHES = "MATCHES"
    MISMATCH = "MISMATCH"
    NOT_CHECKABLE = "NOT_CHECKABLE"
    ACCEPTED_NON_FACT = "ACCEPTED_NON_FACT"  # estimate/forecast: allowed, not a fact


# Default tolerance for a stated percentage, in percentage points: a report
# may round to one decimal, not be a different number.
PCT_TOLERANCE = Decimal("0.1")
# Default tolerance for a multiple (1.35 vs 1.4801 is far beyond).
MULTIPLE_TOLERANCE = Decimal("0.02")
# FIX #4: a multiple's tolerance must scale with its magnitude. An absolute
# 0.02 is calibrated for a P/B near 1.6, where it allows a two-decimal
# rounding. Applied to a P/E near 25 it rejects a report that rounds 25.4375
# to the correct 25.4 — a difference of 0.0375, which is 0.15% and no
# contradiction at all. The rule is therefore "absolute floor OR one percent",
# whichever is larger: small multiples keep the tight check they need, large
# ones get the tolerance their own rounding requires. One percent cannot hide a
# materially wrong figure — 18.2 against 25.4 is 28% off.
_MULTIPLE_RELATIVE_TOLERANCE = Decimal("0.01")


def _multiple_tolerance(computed: Decimal | None) -> Decimal:
    """The permitted absolute difference for a multiple of this magnitude."""
    if computed is None:
        return MULTIPLE_TOLERANCE
    return max(MULTIPLE_TOLERANCE,
               abs(computed) * _MULTIPLE_RELATIVE_TOLERANCE)


@dataclass(frozen=True)
class NumericClaim:
    """One number an agent asserted, with where it came from."""

    claim_id: str
    label: str
    value: Decimal | None
    unit: str
    agent: str
    kind: ClaimKind
    source_text: str = ""

    def summary(self) -> dict:
        return {
            "claim_id": self.claim_id,
            "label": self.label,
            "value": str(self.value) if self.value is not None else None,
            "unit": self.unit,
            "agent": self.agent,
            "kind": str(self.kind),
            "source_text": self.source_text[:200],
        }


@dataclass(frozen=True)
class ClaimResult:
    """A claim, the engine's verdict, and why."""

    claim: NumericClaim
    verdict: ClaimVerdict
    record: MetricRecord | None = None
    reason: str = ""

    @property
    def blocks(self) -> bool:
        """Only a contradicted *fact* blocks.

        A ``MISMATCH`` on a computed metric is a report asserting something
        the engine disproves — it blocks. ``NOT_CHECKABLE`` also blocks when
        the metric is critical: an unverifiable number must not authorize an
        order (§9). Estimates and forecasts are explicitly allowed through
        as non-facts so the agents can still reason about scenarios.
        """
        if self.verdict is ClaimVerdict.MISMATCH:
            return True
        if self.verdict is ClaimVerdict.NOT_CHECKABLE:
            return self.claim.kind in (ClaimKind.COMPUTED, ClaimKind.OBSERVED)
        return False

    def summary(self) -> dict:
        payload = self.claim.summary()
        payload["verdict"] = str(self.verdict)
        payload["blocks"] = self.blocks
        payload["reason"] = self.reason
        if self.record is not None:
            payload["computed_value"] = self.record.display
            payload["formula_id"] = self.record.formula_id
        return payload


def check_claim(claim: NumericClaim, computed: Any, *,
                tolerance: Any | None = None,
                formula_id: str = "unknown",
                period_end: str | None = None,
                affected_decisions: tuple[str, ...] = ()) -> ClaimResult:
    """Compare a claim against the engine's value for the same metric.

    ``computed`` is the engine's own result — never another agent's text.
    When it is ``None`` the claim is ``NOT_CHECKABLE``: there is no verified
    value to compare against, and an unverifiable critical claim must not
    pass as a fact.

    ``tolerance`` defaults by unit: percentage points for percentages,
    absolute multiple difference for ratios, so a caller cannot loosen one
    kind of check by forgetting a parameter.
    """
    computed_d = as_decimal(computed)
    # A claim may arrive as a float from a tool argument or from prose; convert
    # it once so the comparison is Decimal against Decimal. Mixing the two
    # raises TypeError, which surfaced only when a tool built a claim directly.
    claimed_d = as_decimal(claim.value)
    is_pct = claim.unit in ("percent", "percentage_points")
    tol = (as_decimal(tolerance) if tolerance is not None
           else (PCT_TOLERANCE if is_pct else _multiple_tolerance(computed_d)))
    quantum = PERCENT_QUANTUM if is_pct else RATIO_QUANTUM

    if computed_d is None or claimed_d is None:
        return ClaimResult(
            claim=claim, verdict=ClaimVerdict.NOT_CHECKABLE,
            record=metric(
                claim.label, computed_d, unit=claim.unit, formula_id=formula_id,
                period_end=period_end, llm_claimed_value=claimed_d,
                source_status=SourceStatus.LLM_CLAIMED,
                calculation_status=CalculationStatus.UNSUPPORTED,
                reason="no computed value to check the claim against",
                affected_decisions=affected_decisions, quantum=quantum,
            ),
            reason="the claim cannot be verified: no engine value for this metric",
        )

    difference = computed_d - claimed_d
    within = abs(difference) <= tol
    record = metric(
        claim.label, computed_d, unit=claim.unit, formula_id=formula_id,
        period_end=period_end, llm_claimed_value=claimed_d,
        tolerance=tol, source_status=SourceStatus.LLM_CLAIMED,
        calculation_status=(CalculationStatus.VERIFIED if within
                            else CalculationStatus.MISMATCH),
        reason=("" if within else
                f"the agent stated {quantize(claimed_d, quantum)}, the engine "
                f"computes {quantize(computed_d, quantum)} "
                f"(off by {quantize(difference, quantum)}, tolerance {tol})"),
        affected_decisions=affected_decisions, quantum=quantum,
    )
    return ClaimResult(
        claim=claim,
        verdict=ClaimVerdict.MATCHES if within else ClaimVerdict.MISMATCH,
        record=record,
        reason=record.reason,
    )


def accept_non_fact(claim: NumericClaim, *, reason: str = "") -> ClaimResult:
    """Accept an estimate, forecast or scenario **as a non-fact**.

    The distinction is the whole point of §9: an analyst may say "I expect
    8% growth", and that must not be treated as a verified 8%. The claim is
    allowed to inform reasoning and is recorded as what it is, so nothing
    downstream can mistake it for an observed value.
    """
    if claim.kind in (ClaimKind.COMPUTED, ClaimKind.OBSERVED):
        raise ValueError(
            f"{claim.kind} claims must be checked, not accepted as non-facts")
    return ClaimResult(
        claim=claim, verdict=ClaimVerdict.ACCEPTED_NON_FACT,
        reason=reason or f"{claim.kind} claim accepted as a non-fact, not verified",
    )


# --- Extraction: finding claims so they can be *reported* -------------------
#
# These patterns locate labelled figures in prose. They are a reporting aid,
# not a trust boundary: a claim they miss is simply never verified, which is
# the safe direction. Each requires an explicit label, because a bare number
# in a sentence has no metric identity to check it against.

# Each entry is (label, pattern, unit, value_group). ``value_group`` names the
# capture holding the claimed figure, because a pattern may also capture a
# window ("SMA200") whose number is not the claim: taking the last group
# turned "4.5% above SMA200" into a claim of 200%.
# The P/E label forms, named so the qualifier logic can locate *other* figures in
# the same sentence without duplicating the expression. `P/E` must not be part of
# a longer word ("P/EBITDA"), hence the lookarounds.
_PE_LABEL_PATTERN = re.compile(
    r"(?:trailing\s+P/?E"
    r"|P/?E\s+trailing"
    r"|P/?E\s+ratio"
    r"|Price[-\s]to[-\s]Earnings(?:\s+ratio)?"
    r"|(?<![A-Za-z])P/?E(?![A-Za-z]))"
    r"[^.\d\n%]{0,24}?(\d+(?:[.,]\d+)?)\s*x?",
    re.IGNORECASE)

_PE_PER_PATTERN = re.compile(
    r"\bPER\b(?=[^.\d\n]{0,30}?\b(?:multiplo|rapporto|ratio|dell['\u2019]?"
    r"utile|prezzo[-\s]utili)\b)"
    r"[^.\d\n%]{0,30}?(\d+(?:[.,]\d+)?)\s*x?",
    re.IGNORECASE)

_CLAIM_PATTERNS: tuple[tuple[str, re.Pattern[str], str, int], ...] = (
    # Reports write this both ways round — "11.3% below the high" and
    # "-2.63% from the high" — so both orders are matched.
    ("distance_from_high",
     re.compile(
         r"(?:([-+]?\d+(?:\.\d+)?)\s*%\s+(?:below|above|from|off)\s+(?:the\s+)?"
         r"(?:52[- ]week\s+)?high)"
         r"|(?:(?:from|below|off)\s+(?:the\s+)?(?:52[- ]week\s+)?high[^.]{0,20}?"
         r"([-+]?\d+(?:\.\d+)?)\s*%)",
         re.IGNORECASE), "percent", -1),
    ("distance_from_sma",
     re.compile(r"([-+]?\d+(?:\.\d+)?)\s*%\s+(?:above|below)\s+(?:the\s+)?(?:SMA|EMA)\s*\d*",
                re.IGNORECASE), "percent", 1),
    # The multiple may use either separator: `1.49x` in English reports,
    # `1,49x` in Italian ones. The comma is a decimal here, not a thousands
    # group — a multiple of 1,490 would be absurd and the parser treats a
    # three-digit tail separately.
    ("price_to_book",
     re.compile(r"P/?B[^.\d,]{0,20}(\d+(?:[.,]\d+)?)\s*x?", re.IGNORECASE),
     "multiple", 1),
    # FIX #4: a trailing P/E. The label must appear and the figure must be the
    # *current trailing* multiple — not a forward one, and not one qualified by
    # another period. Both are checked by `_pe_claim_value` on the matched text
    # rather than by lookarounds, because "forward" may precede or follow the
    # label ("Forward P/E of 18.2x", "P/E forward 18,2x") and a single regular
    # expression covering both orders became unreadable and untestable.
    ("pe_trailing", _PE_LABEL_PATTERN, "multiple", 1),
    # `PER` is the Italian and Spanish abbreviation but also an ordinary word
    # fragment, so it is only read as a multiple when the surrounding text names
    # the multiple ("PER del multiplo 25,4"). Ambiguity resolves to *not* a
    # claim, which is the safe direction: an unextracted claim is never
    # verified, and a false positive would invent a contradiction.
    ("pe_trailing", _PE_PER_PATTERN, "multiple", 1),
    ("stop_atr",
     re.compile(r"(\d+(?:\.\d+)?)\s*(?:x\s*)?ATR[s]?\b", re.IGNORECASE), "multiple", 1),
    ("risk_reward",
     re.compile(r"(?:R:?R|risk[/-]reward)[^.\d]{0,12}(\d+(?:\.\d+)?)", re.IGNORECASE),
     "multiple", 1),
    # EPS growth or contraction, in the phrasings a report actually uses:
    # "EPS contracts 37%", "EPS growth of -37%", "a 37% decline in EPS".
    # Case A of the 2026-11-09 19:24 report is exactly this claim.
    ("eps_change",
     re.compile(
         r"\bEPS\b[^.\d%]{0,40}?([-+]?\d+(?:\.\d+)?)\s*%"
         r"|([-+]?\d+(?:\.\d+)?)\s*%\s+(?:decline|contraction|drop|growth|increase|"
         r"decrease)\s+in\s+\bEPS\b",
         re.IGNORECASE), "percent", -1),
    # VIX change, the same shape: "VIX rose 58%", "VIX at 28.18 from 16.61
    # (+58%)".
    ("vix_change",
     re.compile(
         r"\bVIX\b[^.\d%]{0,40}?([-+]?\d+(?:\.\d+)?)\s*%"
         r"|\(?([-+]?\d+(?:\.\d+)?)\s*%\)?[^.]{0,30}?\bVIX\b",
         re.IGNORECASE), "percent", -1),
)

# FIX #4: a P/E claim is only comparable to `pe_trailing` when it describes the
# same thing. Two exclusions, applied to the matched sentence rather than to the
# pattern, because the qualifier may sit on either side of the label:
#
# - a *forward* multiple is a different metric the engine does not certify at
#   all, so comparing it to the trailing value would manufacture a contradiction
#   out of a correct statement;
# - a P/E qualified by another period ("the 2024 P/E", "FY2025") is a historical
#   figure, and the engine's value is the current one.
#
# Both resolve to "not a claim", which is the fail-closed direction: an
# unextracted claim is never verified, whereas a false MISMATCH blocks a run
# that said nothing wrong.
_PE_FORWARD = re.compile(
    r"\bforward\b|\batteso\b|\bprevisto\b|\bexpected\b|\bestimat\w*|\bprospett\w*|"
    r"\bnext\s+(?:year|fiscal|quarter)\b|\bFY\s*20\d\d\b",
    re.IGNORECASE)
_PE_HISTORICAL = re.compile(
    r"\bhistor\w*|\bstoric\w*|\bFY\s*(?:19|20)\d\d\b|\b(?:19|20)\d\d\b|"
    r"\bpast\b|\bprevious\b|\bprecedente\b|\banno\s+scorso\b",
    re.IGNORECASE)


# A sentence boundary is a period/newline/semicolon **not** sitting between two
# digits. `.` inside `25.4` is a decimal point, not a full stop: stopping there
# Where a qualifier stops describing a figure: a clause separator. A decimal
# point is not one, so `\.` only counts when it is not between two digits.
_PE_QUALIFIER_STOP = re.compile(
    r"[;,\n]|(?<![\d])\.(?![\d])|\b(?:and|while|but|whereas|e|ma)\b",
    re.IGNORECASE)

# A qualifier is short: "Forward", "(FY2024)", "— storico". These caps are a
# backstop for prose with no punctuation at all, not the primary boundary.
_PE_PREFIX_CAP = 20
_PE_SUFFIX_CAP = 24


def _pe_qualifier_scope(text: str, start: int, end: int) -> str:
    """The text that may qualify *this* figure, on either side of it.

    A qualifier describes a figure from either side, but only within the same
    clause:

    - before the label: "Forward P/E of 18.2", "FY2024 P/E 12x";
    - between label and number: "P/E forward 18,2";
    - after the number, bracketed or dashed: "P/E 18.2x (forward)",
      "P/E 18,2x — storico".

    The trailing side was missing from an earlier version, which read
    `P/E 18.2x (forward)` as a trailing claim and produced a false MISMATCH.

    The boundary is the clause separator, not a character radius. A radius
    cannot work: `(forward) and P/E 25.4x (trailing)` puts 13 characters between
    the two figures, so any radius reaching `(trailing)` also reaches
    `(forward)`. Separators end a qualifier because that is where prose stops
    qualifying this figure and starts qualifying the next one.
    """
    prefix_start = 0
    for separator in _PE_QUALIFIER_STOP.finditer(text, 0, start):
        prefix_start = separator.end()
    # A closing bracket also ends the previous figure's qualifier.
    closing = text.rfind(")", prefix_start, start)
    if closing != -1:
        prefix_start = closing + 1
    prefix_start = max(prefix_start, start - _PE_PREFIX_CAP)

    separator = _PE_QUALIFIER_STOP.search(text, end)
    suffix_end = separator.start() if separator else len(text)
    suffix_end = min(suffix_end, end + _PE_SUFFIX_CAP)

    return text[prefix_start:suffix_end]


def _pe_claim_value(text: str, match: re.Match[str]) -> str | None:
    """The claimed figure, or ``None`` when the claim is not the trailing one."""
    scope = _pe_qualifier_scope(text, match.start(), match.end())
    if _PE_FORWARD.search(scope) or _PE_HISTORICAL.search(scope):
        return None
    return match.group(1)


def _decimal_claim(raw: str | None) -> Decimal | None:
    """A captured figure, reading either decimal separator.

    ``as_decimal`` strips every comma as a thousands separator, which is right
    for a revenue and wrong for a multiple: `P/B 1,49x` in an Italian report
    means one point four nine, and reading it as 149 would turn a correct figure
    into a mismatch. A comma followed by one or two digits is a decimal; three
    digits is a thousands group, the rule `math/precision` documents.
    """
    if not raw:
        return as_decimal(raw)
    text = raw.strip()
    if "," in text and "." not in text:
        head, _, tail = text.rpartition(",")
        if len(tail) in (1, 2):
            text = f"{head}.{tail}"
    return as_decimal(text)


def extract_claims(text: str | None, agent: str = "unknown") -> list[NumericClaim]:
    """Locate labelled numeric claims in an agent's prose.

    Returns claims with ``kind=COMPUTED`` — they *purport* to be derived
    metrics. Whether each is true is decided by ``check_claim`` against the
    engine, never here. Numbers without a recognised label are not returned,
    and that is intentional: they carry no metric identity, so they can be
    neither verified nor used.
    """
    if not text:
        return []
    found: list[NumericClaim] = []
    seen: set[tuple[str, str]] = set()
    for label, pattern, unit, value_group in _CLAIM_PATTERNS:
        for match in pattern.finditer(text):
            if value_group == -1:
                # Alternation pattern: whichever branch captured wins.
                raw = next((g for g in match.groups() if g is not None), None)
                value = as_decimal(raw)
            elif label == "pe_trailing":
                # FIX #4: a forward or historical multiple is a different claim
                # and must not be judged against the current trailing metric.
                value = _decimal_claim(_pe_claim_value(text, match))
            else:
                value = _decimal_claim(match.group(value_group))
            if value is None:
                continue
            key = (label, str(value))
            if key in seen:
                continue
            seen.add(key)
            start = max(0, match.start() - 40)
            found.append(NumericClaim(
                claim_id=f"{label}:{len(found)}",
                label=label, value=value, unit=unit, agent=agent,
                kind=ClaimKind.COMPUTED,
                source_text=text[start:match.end()].strip(),
            ))
    return found


@dataclass
class ClaimsReport:
    """Every claim found in a run, with the blocking subset highlighted."""

    results: list[ClaimResult] = field(default_factory=list)

    @property
    def blocking(self) -> list[ClaimResult]:
        return [r for r in self.results if r.blocks]

    @property
    def blocks_decision(self) -> bool:
        return bool(self.blocking)

    @property
    def blocking_labels(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(r.claim.label for r in self.blocking))

    def summary(self) -> dict:
        return {
            "claims": [r.summary() for r in self.results],
            "blocking_claims": list(self.blocking_labels),
            "status": "BLOCKED" if self.blocks_decision else (
                "OK" if self.results else None),
        }


def check_reports(reports: dict[str, str], computed: dict[str, Any], *,
                  tolerances: dict[str, Any] | None = None
                  ) -> ClaimsReport:
    """Check every claim across several agents' reports against the engine.

    ``computed`` maps a claim label to the engine's verified value for it.
    A label absent from ``computed`` yields ``NOT_CHECKABLE`` — the honest
    outcome when the engine has not computed that metric, and the reason a
    report cannot certify a figure nobody verified.
    """
    report = ClaimsReport()
    tolerances = tolerances or {}
    for agent, text in reports.items():
        for claim in extract_claims(text, agent=agent):
            if claim.label not in computed:
                report.results.append(ClaimResult(
                    claim=claim, verdict=ClaimVerdict.NOT_CHECKABLE,
                    reason=(f"the engine has no computed value for "
                            f"'{claim.label}' to verify this claim against"),
                ))
                continue
            report.results.append(check_claim(
                claim, computed[claim.label],
                tolerance=tolerances.get(claim.label),
                formula_id=f"claims.{claim.label}",
            ))
    return report

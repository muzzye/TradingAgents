"""P0 regression gates for the BRK-B 18:53 report (2026-11-08 18:53:34).

The 18:53 report repeated — and in places worsened — the 17:05 defects:
Sentiment presented Friday's -0.01% move as the reaction to Saturday's Q3
release; Fundamentals leaned on Q2 equity while the Q3 filing was already
public; P/B mixed periods and share classes; TTM was called "accelerating"
against an FY figure; EPS types were conflated; a tranche plan sized to the
stop-distance alone ignored the exposure cap; three tranches could stack
risk; and two entry conditions contradicted each other.

This module is the deterministic enforcement layer for those defects. It
extends the existing reliability stack (causal_guard, fact_checker,
validator, risk_engine, execution_gate) with the missing pieces and is
wired into ``gate_integration.evaluate_decision``. Every check is a pure
function returning a typed verdict with a stable reason code; nothing is
hardcoded for BRK-B — the report's numbers live only in the tests.

Reason codes (stable, machine-readable):

- ``INVALID_EVENT_CAUSALITY``   — a reaction claim dated before/at the
  event or on a session that had not traded (EVENT-REG-001);
- ``STALE_FUNDAMENTALS``        — the used period is older than the latest
  filing known at the analysis date (FILING-REG-002);
- ``FACT_CONFLICT_UNRESOLVED``  — same-scope metric assertions disagree
  without a verified primary source (FACT-REG-003);
- ``UNRECONCILED_VALUATION``    — P/B inputs mix periods/classes/currencies
  (VAL-REG-004);
- ``INVALID_TREND_COMPARISON``  — trend claimed across incomparable period
  types or against the data (MATH-REG-005);
- ``EPS_DEFINITION_MISMATCH``   — GAAP/operating/adjusted/consensus EPS
  conflated (METRIC-REG-006);
- ``EXPOSURE_CAP_EXCEEDED``     — sizing must respect the per-symbol
  notional cap, not only the stop-distance budget (RISK-REG-007);
- ``AGGREGATE_RISK_EXCEEDED``   — tranche + open-position risk over the
  aggregate budget (RISK-REG-008);
- ``INVALID_ENTRY_CONDITION``   — mutually-exclusive or unobservable entry
  conditions (ENTRY-REG-009).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from tradingagents.dataflows.validator import ValidationResult

# ---------------------------------------------------------------------------
# Stable reason codes (machine-readable, audit + gate)
# ---------------------------------------------------------------------------

INVALID_EVENT_CAUSALITY = "INVALID_EVENT_CAUSALITY"
STALE_FUNDAMENTALS = "STALE_FUNDAMENTALS"
FUNDAMENTALS_UNAVAILABLE = "FUNDAMENTALS_UNAVAILABLE"
FACT_CONFLICT_UNRESOLVED = "FACT_CONFLICT_UNRESOLVED"
UNRECONCILED_VALUATION = "UNRECONCILED_VALUATION"
INVALID_TREND_COMPARISON = "INVALID_TREND_COMPARISON"
EPS_DEFINITION_MISMATCH = "EPS_DEFINITION_MISMATCH"
EXPOSURE_CAP_EXCEEDED = "EXPOSURE_CAP_EXCEEDED"
AGGREGATE_RISK_EXCEEDED = "AGGREGATE_RISK_EXCEEDED"
INVALID_ENTRY_CONDITION = "INVALID_ENTRY_CONDITION"

# Causal-guard statuses this module reads.
NOT_YET_OBSERVABLE = "NOT_YET_OBSERVABLE"
OBSERVABLE = "OBSERVABLE"
INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value).strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _finite(value) -> bool:
    return value is not None and isinstance(value, (int, float)) \
        and math.isfinite(value)


# ---------------------------------------------------------------------------
# EVENT-REG-001 — event causality of a claimed reaction
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EventCausalityVerdict:
    """Whether a claimed market reaction to an event is temporally possible."""

    status: str
    reason_code: str
    claimed_reaction_date: str | None
    event_date: str | None
    reason: str

    @property
    def blocks_decision(self) -> bool:
        return self.status != OBSERVABLE

    def summary(self) -> dict:
        return {
            "status": self.status,
            "reason_code": self.reason_code,
            "claimed_reaction_date": self.claimed_reaction_date,
            "event_date": self.event_date,
            "reason": self.reason,
        }


def check_event_causality(
    claimed_reaction_date: str | None,
    event_published_at: str | None,
    sessions: list[str],
    *,
    as_of_date: str | None = None,
) -> EventCausalityVerdict:
    """Check a claimed reaction date against the event and market calendar.

    Rules (calendar-based, any ticker):

    - The claimed reaction date must be a *session* strictly after the
      event's publication. A date on/before the event, or a date on which
      no session traded (weekend, holiday), is temporally impossible:
      ``INVALID_EVENT_CAUSALITY``.
    - A claim dated after the event but before the first post-event session
      has actually traded (relative to ``as_of_date``) is
      ``NOT_YET_OBSERVABLE`` — also blocking: the reaction cannot be
      asserted yet.
    - An unknown event date cannot validate any reaction claim.
    """
    event_d = _parse_date(event_published_at)
    claim_d = _parse_date(claimed_reaction_date)
    as_of_d = _parse_date(as_of_date)

    if event_d is None:
        return EventCausalityVerdict(
            status=INSUFFICIENT_DATA,
            reason_code="EVENT_DATE_UNKNOWN",
            claimed_reaction_date=claimed_reaction_date,
            event_date=None,
            reason="event publication date unknown; a reaction claim cannot be validated",
        )

    parsed = sorted(s for s in (_parse_date(s) for s in sessions) if s is not None)

    def _is_session(d: date) -> bool:
        return d in parsed

    if claim_d is None:
        return EventCausalityVerdict(
            status=INSUFFICIENT_DATA,
            reason_code="REACTION_DATE_UNKNOWN",
            claimed_reaction_date=claimed_reaction_date,
            event_date=f"{event_d:%Y-%m-%d}",
            reason="claimed reaction date is missing or unparseable",
        )

    if claim_d <= event_d:
        # The claimed reaction happened on/before the publication: impossible.
        return EventCausalityVerdict(
            status=INVALID_EVENT_CAUSALITY,
            reason_code=INVALID_EVENT_CAUSALITY,
            claimed_reaction_date=f"{claim_d:%Y-%m-%d}",
            event_date=f"{event_d:%Y-%m-%d}",
            reason=(
                f"claimed reaction on {claim_d:%Y-%m-%d} is on or before the "
                f"event publication ({event_d:%Y-%m-%d}): temporally impossible"
            ),
        )

    if not _is_session(claim_d):
        # A date the market did not trade (weekend/holiday): no reaction
        # price can exist there.
        return EventCausalityVerdict(
            status=INVALID_EVENT_CAUSALITY,
            reason_code=INVALID_EVENT_CAUSALITY,
            claimed_reaction_date=f"{claim_d:%Y-%m-%d}",
            event_date=f"{event_d:%Y-%m-%d}",
            reason=(
                f"claimed reaction on {claim_d:%Y-%m-%d} but no trading "
                "session occurred on that date (weekend or holiday)"
            ),
        )

    # The claim names a real session after the event. It is only observable
    # if that session has actually traded by the analysis date.
    if as_of_d is not None and claim_d > as_of_d:
        return EventCausalityVerdict(
            status=NOT_YET_OBSERVABLE,
            reason_code="POST_EVENT_NOT_YET_OBSERVABLE",
            claimed_reaction_date=f"{claim_d:%Y-%m-%d}",
            event_date=f"{event_d:%Y-%m-%d}",
            reason=(
                f"the claimed reaction session {claim_d:%Y-%m-%d} is after "
                f"the analysis date ({as_of_d:%Y-%m-%d}): not yet observable"
            ),
        )

    return EventCausalityVerdict(
        status=OBSERVABLE,
        reason_code="OK",
        claimed_reaction_date=f"{claim_d:%Y-%m-%d}",
        event_date=f"{event_d:%Y-%m-%d}",
        reason=(
            f"claimed reaction on {claim_d:%Y-%m-%d} is a session after the "
            f"event ({event_d:%Y-%m-%d}) and has traded by {as_of_d:%Y-%m-%d}"
        ),
    )


# ---------------------------------------------------------------------------
# FILING-REG-002 — filing freshness (period-level, source-agnostic)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FilingFreshnessVerdict:
    """Whether the used fundamentals period is the freshest known."""

    status: str
    reason_code: str
    latest_known_period_end: str | None
    used_period_end: str | None
    reason: str

    @property
    def blocks_decision(self) -> bool:
        return self.status in (STALE_FUNDAMENTALS, FUNDAMENTALS_UNAVAILABLE)

    def summary(self) -> dict:
        return {
            "status": self.status,
            "reason_code": self.reason_code,
            "latest_known_period_end": self.latest_known_period_end,
            "used_period_end": self.used_period_end,
            "reason": self.reason,
        }


def check_filing_freshness(
    used_period_end: str | None,
    known_period_ends: list[str],
    as_of_date: str,
    *,
    publication_lag_days: int = 38,
) -> FilingFreshnessVerdict:
    """Compare the used fundamentals period against the latest known filing.

    ``known_period_ends`` are the fiscal period ends the source reports.
    A period end is treated as *known* at ``as_of_date`` only once a
    conservative publication lag has elapsed after the period end
    (``publication_lag_days``, default 38 — a quarter's figures cannot be
    public the day the period closes, and treating the period end as the
    publication date is exactly the look-ahead the 18:53 report committed).
    Callers with a verified publication date per period should pass only
    the period ends already known at ``as_of_date`` and set the lag to 0.

    Using an older period while a newer one was already published is
    ``STALE_FUNDAMENTALS``; no known filing or an unknown used period is
    ``FUNDAMENTALS_UNAVAILABLE``. Both block executive decisions that
    depend on the fundamentals.
    """
    as_of = _parse_date(as_of_date)
    used = _parse_date(used_period_end)

    # No look-ahead: a period's figures are not public until the
    # publication lag after the period end has elapsed.
    known = sorted(
        p for p in (_parse_date(p) for p in known_period_ends) if p
    )
    if as_of is not None and publication_lag_days > 0:
        cutoff = as_of - timedelta(days=publication_lag_days)
        known = [p for p in known if p <= cutoff]

    if not known:
        return FilingFreshnessVerdict(
            status=FUNDAMENTALS_UNAVAILABLE,
            reason_code=FUNDAMENTALS_UNAVAILABLE,
            latest_known_period_end=None,
            used_period_end=used_period_end,
            reason=f"no authoritative filing is known as of {as_of_date}",
        )
    latest = known[-1]
    if used is None:
        return FilingFreshnessVerdict(
            status=FUNDAMENTALS_UNAVAILABLE,
            reason_code=FUNDAMENTALS_UNAVAILABLE,
            latest_known_period_end=f"{latest:%Y-%m-%d}",
            used_period_end=used_period_end,
            reason="the period the fundamentals report used is unknown or undated",
        )
    if used >= latest:
        return FilingFreshnessVerdict(
            status="CURRENT",
            reason_code="FUNDAMENTALS_CURRENT",
            latest_known_period_end=f"{latest:%Y-%m-%d}",
            used_period_end=f"{used:%Y-%m-%d}",
            reason="the used period matches the latest filing known at the analysis date",
        )
    return FilingFreshnessVerdict(
        status=STALE_FUNDAMENTALS,
        reason_code=STALE_FUNDAMENTALS,
        latest_known_period_end=f"{latest:%Y-%m-%d}",
        used_period_end=f"{used:%Y-%m-%d}",
        reason=(
            f"the report uses the period ended {used:%Y-%m-%d} but a later "
            f"period (ended {latest:%Y-%m-%d}) was already published at "
            f"{as_of_date}: the older period must not be presented as current"
        ),
    )


# ---------------------------------------------------------------------------
# FACT-REG-003 / METRIC-REG-006 — scoped metric observations and conflicts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MetricObservation:
    """One metric as one agent asserted it, fully scoped.

    ``metric_id`` names the *definition* (``eps_gaap`` vs
    ``eps_operating`` vs ``eps_consensus_adjusted``): different definitions
    are never compared. ``period``/``period_end`` scope the time window
    (a quarter, H1, FY, TTM); ``segment`` scopes the entity (company vs
    BNSF/GEICO). ``value=None`` means the agent asserted an absence —
    recorded as an absence, never converted to 0.
    """

    metric_id: str
    agent: str
    value: float | None
    unit: str = ""
    currency: str | None = None
    period: str | None = None
    period_end: str | None = None
    segment: str | None = None
    metric_definition: str = ""
    source_type: str = "unknown"
    source_uri: str | None = None
    verified: bool = False
    statement: str = ""

    @property
    def is_primary(self) -> bool:
        return self.source_type in ("company_release", "sec_filing")

    def scope_key(self) -> tuple:
        """The scope a value must match to be compared with another's."""
        return (self.metric_id, self.period, self.period_end, self.segment,
                self.unit, self.currency, self.metric_definition)


@dataclass
class MetricConflict:
    """Incompatible assertions about the same scoped metric."""

    metric_id: str
    period: str | None
    segment: str | None
    unit: str
    currency: str | None
    metric_definition: str
    assertions: list[MetricObservation] = field(default_factory=list)
    resolved_by: str | None = None      # agent whose value wins
    reason_code: str = FACT_CONFLICT_UNRESOLVED

    @property
    def resolved(self) -> bool:
        return self.resolved_by is not None

    def summary(self) -> dict:
        return {
            "metric_id": self.metric_id,
            "period": self.period,
            "segment": self.segment,
            "unit": self.unit,
            "currency": self.currency,
            "metric_definition": self.metric_definition,
            "assertions": [
                {
                    "agent": a.agent,
                    "value": a.value,
                    "source_type": a.source_type,
                    "verified": a.verified,
                    "statement": a.statement[:200],
                }
                for a in self.assertions
            ],
            "resolved_by": self.resolved_by,
            "reason_code": self.reason_code,
            "resolved": self.resolved,
        }


@dataclass
class MetricConsistencyVerdict:
    """All scoped-metric conflicts for one run, decision-ready."""

    conflicts: list[MetricConflict] = field(default_factory=list)
    not_reported: list[str] = field(default_factory=list)

    @property
    def unresolved(self) -> list[MetricConflict]:
        return [c for c in self.conflicts if not c.resolved]

    @property
    def blocks_decision(self) -> bool:
        return bool(self.unresolved)

    def summary(self) -> dict:
        return {
            "conflicts": [c.summary() for c in self.conflicts],
            "unresolved": [c.metric_id for c in self.unresolved],
            "not_reported": list(self.not_reported),
        }


def _agree(a: float, b: float, tolerance_pct: float) -> bool:
    if b == 0:
        return a == 0
    return abs(a - b) / abs(b) * 100 <= tolerance_pct


def check_metric_definition(
    observations: list[MetricObservation],
    *,
    tolerance_pct: float = 1.0,
) -> MetricConsistencyVerdict:
    """Compare same-scope metric assertions; record conflicts.

    Only observations with an identical scope key (metric_id, period,
    period_end, segment, unit, currency, metric_definition) are compared.
    Agreement within tolerance corroborates; disagreement conflicts. A
    conflict is resolved only by a verified primary source (company
    release / SEC filing); otherwise it stays unresolved and blocks.

    Two further rules close the 18:53 loopholes:

    - **An absence never refutes a sourced value.** An observation with
      ``value=None`` for a metric/period another agent sourced is a
      conflict (``FACT_CONFLICT_UNRESOLVED``): "no buyback reported" from
      an absent vendor field cannot stand against a sourced amount.
    - **EPS definitions are not interchangeable.** Observations of
      different EPS-family definitions (GAAP, operating, adjusted,
      consensus) presented together — the shape of a "beat" claim — are
      flagged ``EPS_DEFINITION_MISMATCH``: no verified beat without a
      compatible definition and period.
    """
    verdict = MetricConsistencyVerdict()
    groups: dict[tuple, list[MetricObservation]] = {}
    for obs in observations:
        if obs.value is None:
            # An absence: conflicting only when another agent sourced a
            # value for the same metric and period.
            contradicting = [
                o for o in observations
                if o is not obs and o.value is not None
                and o.metric_id == obs.metric_id and o.period == obs.period
                and o.segment == obs.segment
            ]
            if contradicting:
                conflict = MetricConflict(
                    metric_id=obs.metric_id, period=obs.period,
                    segment=obs.segment, unit=contradicting[0].unit,
                    currency=contradicting[0].currency,
                    metric_definition=contradicting[0].metric_definition,
                    assertions=[*contradicting, obs],
                )
                # An absence is never a verified primary: it cannot win.
                verdict.conflicts.append(conflict)
            elif obs.metric_id not in verdict.not_reported:
                verdict.not_reported.append(obs.metric_id)
            continue
        groups.setdefault(obs.scope_key(), []).append(obs)

    for key, group in sorted(groups.items(), key=lambda kv: str(kv[0])):
        if len(group) < 2:
            continue
        reference = group[0].value
        if all(_agree(o.value, reference, tolerance_pct) for o in group[1:]):
            continue
        conflict = MetricConflict(
            metric_id=key[0], period=key[1], segment=key[3],
            unit=key[4], currency=key[5], metric_definition=key[6],
            assertions=list(group),
        )
        primaries = [o for o in group if o.verified and o.is_primary]
        if primaries and not _is_eps(group[0]):
            # A verified primary source settles the value — except for the
            # EPS family: a per-share figure feeds sizing and "beat" claims,
            # so a disagreement there stays unresolved until the definitions
            # are reconciled, whatever the sources rank.
            conflict.resolved_by = primaries[0].agent
            conflict.reason_code = "PRIMARY_SOURCE_WINS"
        verdict.conflicts.append(conflict)

    # EPS-family definitions presented together: a "beat" claim across
    # GAAP/operating/adjusted/consensus is not certifiable.
    verdict.conflicts.extend(_eps_definition_conflicts(observations))
    return verdict


# EPS definition families that must never be compared for a "beat".
_EPS_DEFINITION_MARKERS = (
    "eps_gaap", "eps_operating", "eps_adjusted", "eps_consensus",
    "gaap_diluted", "operating_eps", "adjusted_eps", "consensus_eps",
)


def _is_eps(obs: MetricObservation) -> bool:
    metric = obs.metric_id.lower()
    definition = (obs.metric_definition or "").lower()
    if metric.startswith("eps_") or metric.endswith("_eps"):
        return True
    return any(marker in definition for marker in _EPS_DEFINITION_MARKERS)


def _eps_definition_conflicts(
    observations: list[MetricObservation],
) -> list[MetricConflict]:
    """Flag a consensus/estimate EPS compared against an actual of another
    definition for the same period.

    A "beat" claim rests on comparing an actual EPS against an expected
    one. When the two sides carry different EPS definitions — a GAAP or
    operating actual against a consensus *adjusted* estimate — the
    comparison is not certifiable: recorded as an unresolved conflict so
    no verified beat can be produced from incomparable definitions. Two
    company-reported actuals of different definitions (GAAP vs operating)
    are separate facts, not a comparison, and are not flagged.
    """
    eps_obs = [o for o in observations if o.value is not None and _is_eps(o)]
    if len(eps_obs) < 2:
        return []
    conflicts: list[MetricConflict] = []
    seen_pairs: set[tuple] = set()
    for i, a in enumerate(eps_obs):
        for b in eps_obs[i + 1:]:
            if (a.metric_id, a.metric_definition) == (b.metric_id, b.metric_definition):
                continue
            if (a.period, a.segment) != (b.period, b.segment):
                continue   # different periods: separate facts, not a beat claim
            # Only an estimate-vs-actual pair is a beat comparison.
            if _is_estimate(a) == _is_estimate(b):
                continue
            pair = tuple(sorted((a.agent, b.agent)))
            key = (a.period, a.segment, pair)
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            conflicts.append(MetricConflict(
                metric_id="eps", period=a.period, segment=a.segment,
                unit=a.unit or b.unit, currency=a.currency or b.currency,
                metric_definition=(
                    f"{a.metric_definition or a.metric_id} vs "
                    f"{b.metric_definition or b.metric_id}"
                ),
                assertions=[a, b],
                reason_code=EPS_DEFINITION_MISMATCH,
            ))
    return conflicts


# Definition markers for consensus/estimate figures: what the market
# expected, as opposed to what the company reported.
_ESTIMATE_MARKERS = ("consensus", "estimate", "expected", "forecast")


def _is_estimate(obs: MetricObservation) -> bool:
    metric = obs.metric_id.lower()
    definition = (obs.metric_definition or "").lower()
    return any(m in metric or m in definition for m in _ESTIMATE_MARKERS)


# ---------------------------------------------------------------------------
# VAL-REG-004 — valuation reconciliation with explicit timestamps
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ValuationVerdict:
    """P/B computed only from aligned periods, classes and currencies."""

    status: str
    price_to_book: float | None
    book_value_per_share: float | None
    reason_code: str
    reason: str
    issues: ValidationResult = field(default_factory=ValidationResult)

    @property
    def blocks_decision(self) -> bool:
        return self.status == UNRECONCILED_VALUATION

    def summary(self) -> dict:
        return {
            "status": self.status,
            "price_to_book": self.price_to_book,
            "book_value_per_share": self.book_value_per_share,
            "reason_code": self.reason_code,
            "reason": self.reason,
            "issues": [
                {"code": i.code, "severity": i.severity, "message": i.message}
                for i in self.issues.issues
            ],
        }


def reconcile_valuation(
    *,
    price: float | None,
    price_class: str | None,
    price_currency: str | None,
    price_as_of: str | None,
    equity: float | None,
    equity_period_end: str | None,
    equity_currency: str | None,
    shares_outstanding: float | None,
    shares_period_end: str | None,
    book_value_class: str | None = None,
    tolerance_pct: float = 1.0,
) -> ValuationVerdict:
    """Compute P/B only from aligned inputs; otherwise UNRECONCILED.

    Requirements for a *verified* P/B:

    - equity and share count from the **same period end** (mixing Q2 equity
      with a Q3-era share count is the 18:53 defect);
    - the book value's share class matches the price's class (or the caller
      supplies the class the equity is per — a mismatch is UNRECONCILED,
      never silently converted);
    - price and equity in comparable currencies;
    - all inputs finite and positive.

    Anything else returns ``UNRECONCILED_VALUATION`` with ``price_to_book``
    None: no "verified" P/B may be produced from mixed inputs.
    """
    issues = ValidationResult()
    bvps: float | None = None
    pb: float | None = None

    def _unreconciled(reason: str, code: str = UNRECONCILED_VALUATION) -> ValuationVerdict:
        return ValuationVerdict(
            status=UNRECONCILED_VALUATION, price_to_book=None,
            book_value_per_share=bvps, reason_code=code, reason=reason,
            issues=issues,
        )

    if not (_finite(price) and price > 0):
        issues.add("PRICE_INVALID", "error", f"price {price!r} is not a positive finite number")
    if not (_finite(equity) and equity > 0):
        issues.add("EQUITY_INVALID", "error", f"equity {equity!r} is not a positive finite number")
    if not (_finite(shares_outstanding) and shares_outstanding > 0):
        issues.add("SHARES_INVALID", "error",
                   f"share count {shares_outstanding!r} is not a positive finite number")
    if issues.has_errors:
        return _unreconciled("valuation inputs missing or invalid")

    # Period alignment: equity and share count must come from one period.
    if equity_period_end != shares_period_end:
        issues.add(
            "PERIOD_MISMATCH", "error",
            f"equity is from {equity_period_end!r} but the share count is "
            f"from {shares_period_end!r}: periods must align before P/B",
            field="period", expected=equity_period_end, actual=shares_period_end,
        )
        return _unreconciled("equity and share count are from different periods")

    # Currency alignment.
    if price_currency and equity_currency and \
            price_currency.strip() != equity_currency.strip():
        issues.add(
            "CURRENCY_MISMATCH", "error",
            f"price in {price_currency} vs equity in {equity_currency}: "
            "not comparable without conversion",
            field="currency", expected=price_currency, actual=equity_currency,
        )
        return _unreconciled("price and equity currencies differ")

    # Share-class alignment: the equity is per some class; the price is per
    # another. Without an explicit matching class the ratio is not produced.
    effective_bvps_class = book_value_class or price_class
    if price_class and effective_bvps_class and \
            price_class.upper() != effective_bvps_class.upper():
        issues.add(
            "SHARE_CLASS_MISMATCH", "error",
            f"price is per {price_class} share but the book value is per "
            f"{effective_bvps_class} share; convert before dividing",
            field="share_class", expected=price_class, actual=effective_bvps_class,
        )
        return _unreconciled("price and book value are per different share classes")

    bvps = equity / shares_outstanding
    pb = price / bvps
    return ValuationVerdict(
        status="VERIFIED",
        price_to_book=pb,
        book_value_per_share=bvps,
        reason_code="OK",
        reason=(
            f"P/B computed from price {price} ({price_class or 'single class'}, "
            f"{price_currency}) and equity {equity} with {shares_outstanding} "
            f"shares, both from {equity_period_end}"
        ),
        issues=issues,
    )


# ---------------------------------------------------------------------------
# MATH-REG-005 — trend comparisons across period types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TrendVerdict:
    """Whether a claimed trend rests on comparable periods and directions."""

    status: str
    reason_code: str
    reason: str

    @property
    def blocks_decision(self) -> bool:
        return self.status == INVALID_TREND_COMPARISON

    def summary(self) -> dict:
        return {
            "status": self.status,
            "reason_code": self.reason_code,
            "reason": self.reason,
        }


# Period-type buckets: only same-bucket comparisons are valid trends.
_PERIOD_TYPES = {
    "TTM": "ttm",
    "FY": "fiscal_year",
    "YTD": "year_to_date",
    "H1": "half_year",
    "H2": "half_year",
}


def _period_type(period: str | None, definition: str) -> str:
    """Classify a period label/definition into a comparable bucket."""
    p = (period or "").upper()
    d = (definition or "").lower()
    for label, bucket in _PERIOD_TYPES.items():
        if p == label or p.endswith(f"-{label}") or label in p.split():
            return bucket
    if "ttm" in d or "trailing" in d:
        return "ttm"
    if "year_to_date" in d or "ytd" in d:
        return "year_to_date"
    if "fiscal_year" in d or d.endswith("_fy"):
        return "fiscal_year"
    if "quarter" in d or "-Q" in p or p.startswith("Q"):
        return "quarter"
    return "unknown"


def check_trend_comparison(
    earlier: MetricObservation,
    later: MetricObservation,
    claimed_direction: str,
) -> TrendVerdict:
    """Whether a claimed trend compares like with like, in the right order.

    Rules:

    - The two observations must be the same metric *definition* and scope
      (segment, unit, currency).
    - The period *types* must match: TTM vs FY, YTD vs quarter etc. are not
      comparable without an explicit transformation — claiming a trend
      across them is ``INVALID_TREND_COMPARISON``.
    - The claimed direction must agree with the actual delta: "accelerating"
      when the value fell, or "declining" when it rose, is invalid.
    """
    if earlier.metric_id != later.metric_id:
        return TrendVerdict(
            status=INVALID_TREND_COMPARISON, reason_code=INVALID_TREND_COMPARISON,
            reason=f"different metrics compared: {earlier.metric_id} vs {later.metric_id}",
        )
    if earlier.metric_definition != later.metric_definition:
        return TrendVerdict(
            status=INVALID_TREND_COMPARISON, reason_code=INVALID_TREND_COMPARISON,
            reason=(
                f"different metric definitions compared: "
                f"{earlier.metric_definition!r} vs {later.metric_definition!r}"
            ),
        )
    if (earlier.segment, earlier.unit, earlier.currency) != \
            (later.segment, later.unit, later.currency):
        return TrendVerdict(
            status=INVALID_TREND_COMPARISON, reason_code=INVALID_TREND_COMPARISON,
            reason="scope (segment/unit/currency) differs between the two periods",
        )
    type_e = _period_type(earlier.period, earlier.metric_definition)
    type_l = _period_type(later.period, later.metric_definition)
    if type_e != type_l or type_e in ("unknown",):
        return TrendVerdict(
            status=INVALID_TREND_COMPARISON, reason_code=INVALID_TREND_COMPARISON,
            reason=(
                f"incomparable period types: {earlier.period!r} ({type_e}) vs "
                f"{later.period!r} ({type_l}); TTM, FY, YTD and quarterly "
                "figures are not comparable for a trend without transformation"
            ),
        )
    if not (_finite(earlier.value) and _finite(later.value)) or earlier.value == 0:
        return TrendVerdict(
            status=INVALID_TREND_COMPARISON, reason_code=INVALID_TREND_COMPARISON,
            reason="one of the two values is missing or zero; trend undefined",
        )
    rising = later.value > earlier.value
    falling = later.value < earlier.value
    claim = (claimed_direction or "").lower()
    if claim in ("accelerating", "rising", "growing", "up") and falling:
        return TrendVerdict(
            status=INVALID_TREND_COMPARISON, reason_code=INVALID_TREND_COMPARISON,
            reason=(
                f"claimed 'accelerating' but the value fell from "
                f"{earlier.value} to {later.value}"
            ),
        )
    if claim in ("declining", "falling", "shrinking", "down") and rising:
        return TrendVerdict(
            status=INVALID_TREND_COMPARISON, reason_code=INVALID_TREND_COMPARISON,
            reason=(
                f"claimed 'declining' but the value rose from "
                f"{earlier.value} to {later.value}"
            ),
        )
    return TrendVerdict(
        status="VALID", reason_code="OK",
        reason=(
            f"like-for-like comparison ({type_e}): {earlier.value} -> "
            f"{later.value}, claimed '{claimed_direction}'"
        ),
    )


# ---------------------------------------------------------------------------
# RISK-REG-007 — sizing under stop-budget AND exposure cap simultaneously
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExposureSizingResult:
    """Sizing subject to the stop-budget AND the per-symbol exposure cap."""

    qty: int
    notional: float
    planned_risk: float
    binding_constraint: str | None    # "RISK_BUDGET" | "EXPOSURE_CAP" | None
    reasons: tuple[str, ...] = field(default_factory=tuple)

    @property
    def blocked(self) -> bool:
        return self.qty <= 0 or bool(self.reasons)


def size_with_exposure_cap(
    *,
    entry: float,
    stop: float,
    atr: float,
    risk_budget: float | None,
    equity: float | None,
    max_symbol_exposure: float | None,
    direction: str = "LONG",
    cash: float | None = None,
    gap_atr: float = 0.0,
    slippage_pct: float = 0.0,
) -> ExposureSizingResult:
    """Size a tranche under BOTH the stop-distance budget and the exposure cap.

    The 18:53 report sized to the stop-distance alone: equity 100k, budget
    1,000, entry 560, stop 552 gives floor(1000/8) = 125 shares = 70,000 USD
    notional = 70% of equity. With a 5% per-symbol exposure cap the binding
    constraint is the cap: floor(0.05 * 100000 / 560) = 8 shares (4,480 USD).
    The engine applies the *most binding* constraint and reports which one
    bound the size. A wrong-side stop for the direction blocks outright.
    """
    reasons: list[str] = []

    # Direction sanity: the stop must protect the position's side.
    if direction == "LONG" and stop >= entry:
        return ExposureSizingResult(
            0, 0.0, 0.0, None, ("STOP_WRONG_SIDE",))
    if direction == "SHORT" and stop <= entry:
        return ExposureSizingResult(
            0, 0.0, 0.0, None, ("STOP_WRONG_SIDE",))
    if direction not in ("LONG", "SHORT"):
        return ExposureSizingResult(0, 0.0, 0.0, None, ("DIRECTION_INVALID",))
    if not (_finite(entry) and entry > 0) or not (_finite(stop) and stop > 0):
        return ExposureSizingResult(0, 0.0, 0.0, None, ("PRICE_INVALID",))
    if risk_budget is None or not _finite(risk_budget) or risk_budget <= 0:
        return ExposureSizingResult(0, 0.0, 0.0, None, ("RISK_BUDGET_MISSING",))

    risk_per_share = abs(entry - stop)
    stressed_extra = max(0.0, gap_atr) * atr + max(0.0, slippage_pct) * entry
    stressed_risk = risk_per_share + stressed_extra

    qty_risk = int(math.floor(risk_budget / stressed_risk)) if stressed_risk > 0 else 0

    # Exposure cap: notional at entry must stay within the fraction of equity.
    qty_exposure = None
    if max_symbol_exposure is not None:
        if equity is None or not _finite(equity) or equity <= 0:
            return ExposureSizingResult(
                0, 0.0, 0.0, None, ("PORTFOLIO_CONTEXT_MISSING",))
        if not (0 < max_symbol_exposure <= 1):
            return ExposureSizingResult(
                0, 0.0, 0.0, None, ("EXPOSURE_CAP_INVALID",))
        max_notional = equity * max_symbol_exposure
        qty_exposure = int(math.floor(max_notional / entry))

    if qty_exposure is not None:
        qty = min(qty_risk, qty_exposure)
        binding = "EXPOSURE_CAP" if qty_exposure < qty_risk else "RISK_BUDGET"
        # The cap binding is the policy working as intended, not a failure:
        # the reason code records which constraint bound the size for the
        # audit, but the sized plan is not blocked by its own cap.
    else:
        qty = qty_risk
        binding = "RISK_BUDGET"

    if qty <= 0:
        reasons.append("NON_POSITIVE_SIZE")
        return ExposureSizingResult(0, 0.0, 0.0, None, tuple(reasons))

    notional = qty * entry
    if cash is not None and (not _finite(cash) or cash < 0 or notional > cash):
        reasons.append("CASH_EXCEEDED")
        return ExposureSizingResult(0, 0.0, 0.0, None, tuple(reasons))

    planned_risk = qty * stressed_risk
    return ExposureSizingResult(qty, notional, planned_risk, binding,
                                tuple(reasons))


# ---------------------------------------------------------------------------
# RISK-REG-008 — aggregate risk across tranches and open positions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TranchePlan:
    """One tranche of a conditional plan, with its own entry and stop.

    ``proposed_stop`` models a *proposed* stop move (e.g. to breakeven):
    it does NOT reduce computed risk until the stop is actually registered
    with the paper broker — the engine always prices the registered stop.
    """

    entry: float
    stop: float
    atr: float
    proposed_stop: float | None = None
    direction: str = "LONG"


@dataclass(frozen=True)
class TrancheOutcome:
    """One tranche's sizing outcome within the aggregate plan."""

    entry: float
    stop: float
    qty: int
    notional: float
    risk: float
    reason_code: str | None = None    # None when the tranche is fine

    @property
    def blocked(self) -> bool:
        return self.reason_code is not None


@dataclass(frozen=True)
class AggregateRiskResult:
    """Aggregate sizing over all tranches plus open exposure."""

    tranches: list[TrancheOutcome] = field(default_factory=list)
    total_qty: int = 0
    total_notional: float = 0.0
    total_risk: float = 0.0
    blocked: bool = False
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def summary(self) -> dict:
        return {
            "tranches": [
                {
                    "entry": t.entry, "stop": t.stop, "qty": t.qty,
                    "notional": t.notional, "risk": t.risk,
                    "reason_code": t.reason_code,
                }
                for t in self.tranches
            ],
            "total_qty": self.total_qty,
            "total_notional": self.total_notional,
            "total_risk": self.total_risk,
            "blocked": self.blocked,
            "reasons": list(self.reasons),
        }


def aggregate_tranche_risk(
    tranches: list[TranchePlan],
    *,
    risk_budget: float,
    equity: float,
    max_symbol_exposure: float,
    existing_notional: float = 0.0,
    existing_risk: float = 0.0,
    gap_atr: float = 0.0,
    slippage_pct: float = 0.0,
) -> AggregateRiskResult:
    """Size every tranche and enforce the AGGREGATE budgets simultaneously.

    The 18:53 report planned three tranches at different prices, reusing
    one stop and quoting "1% per tranche" without an aggregate check. This
    function sizes each tranche to its own stop (a stop at or above a
    lower tranche's entry is INVALID_STOP for that tranche), then enforces:

    - total risk (all tranches + existing open risk) <= ``risk_budget``;
    - total notional (all tranches + existing notional) <=
      ``max_symbol_exposure`` * equity.

    A *proposed* stop move (e.g. to breakeven) never reduces computed risk:
    the engine prices the registered stop until the move is actually
    recorded with the paper broker.
    """
    outcomes: list[TrancheOutcome] = []
    reasons: list[str] = []
    total_qty = 0
    total_notional = existing_notional
    total_risk = existing_risk

    remaining_risk = risk_budget - existing_risk
    remaining_notional = equity * max_symbol_exposure - existing_notional

    for t in tranches:
        # Side sanity per tranche.
        if t.direction == "LONG" and t.stop >= t.entry:
            outcomes.append(TrancheOutcome(t.entry, t.stop, 0, 0.0, 0.0,
                                           "INVALID_STOP"))
            reasons.append("INVALID_STOP")
            continue
        if t.direction == "SHORT" and t.stop <= t.entry:
            outcomes.append(TrancheOutcome(t.entry, t.stop, 0, 0.0, 0.0,
                                           "INVALID_STOP"))
            reasons.append("INVALID_STOP")
            continue
        if not (_finite(t.entry) and t.entry > 0) or not (_finite(t.stop) and t.stop > 0):
            outcomes.append(TrancheOutcome(t.entry, t.stop, 0, 0.0, 0.0,
                                           "PRICE_INVALID"))
            reasons.append("PRICE_INVALID")
            continue

        # Risk is priced from the REGISTERED stop; a proposed stop move
        # (e.g. to breakeven) does not reduce risk until registered.
        risk_per_share = abs(t.entry - t.stop)
        stressed_extra = max(0.0, gap_atr) * t.atr + max(0.0, slippage_pct) * t.entry
        stressed_risk = risk_per_share + stressed_extra

        qty_risk = int(math.floor(remaining_risk / stressed_risk)) \
            if stressed_risk > 0 else 0
        qty_exp = int(math.floor(remaining_notional / t.entry)) \
            if remaining_notional > 0 else 0
        qty = min(qty_risk, qty_exp)

        if qty <= 0:
            # The aggregate budget or exposure cap is exhausted: this
            # tranche is simply not taken. That is the policy binding
            # across tranches (RISK-REG-008), not a plan error — only an
            # invalid stop/price on the tranche itself blocks the plan.
            outcomes.append(TrancheOutcome(t.entry, t.stop, 0, 0.0, 0.0, None))
            continue

        notional = qty * t.entry
        risk = qty * stressed_risk
        outcomes.append(TrancheOutcome(t.entry, t.stop, qty, notional, risk))
        total_qty += qty
        total_notional += notional
        total_risk += risk
        remaining_risk -= risk
        remaining_notional -= notional

    blocked = bool(reasons)
    return AggregateRiskResult(
        tranches=outcomes,
        total_qty=total_qty,
        total_notional=total_notional,
        total_risk=total_risk,
        blocked=blocked,
        reasons=tuple(dict.fromkeys(reasons)),
    )


# ---------------------------------------------------------------------------
# ENTRY-REG-009 — conditional entry validation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EntryCondition:
    """One entry condition as stated in a conditional plan.

    ``basis`` is what the condition observes: ``open``, ``close`` or
    ``price`` (intraday). A close-based condition can only be evaluated
    after the close — it can never authorize an order at that same
    candle's price. ``action`` is what the condition does: BUY, CANCEL_SETUP
    or NONE (informational).
    """

    description: str
    basis: str                        # "open" | "close" | "price"
    operator: str                     # "<" | ">" | "between"
    threshold: float
    second_threshold: float | None = None
    action: str = "BUY"               # "BUY" | "CANCEL_SETUP" | "NONE"

    def __post_init__(self):
        if self.basis not in ("open", "close", "price"):
            raise ValueError(f"basis must be open/close/price, got {self.basis!r}")
        if self.operator not in ("<", ">", "between"):
            raise ValueError(f"operator must be </>/between, got {self.operator!r}")
        if self.operator == "between" and self.second_threshold is None:
            raise ValueError("operator 'between' requires second_threshold")


@dataclass(frozen=True)
class EntryConditionVerdict:
    """Whether the observed prices authorize a buy under the stated conditions."""

    status: str
    reason_code: str
    authorizes_buy: bool
    blocks_decision: bool
    reason: str

    def summary(self) -> dict:
        return {
            "status": self.status,
            "reason_code": self.reason_code,
            "authorizes_buy": self.authorizes_buy,
            "blocks_decision": self.blocks_decision,
            "reason": self.reason,
        }


def _condition_holds(cond: EntryCondition, value: float) -> bool:
    if cond.operator == "<":
        return value < cond.threshold
    if cond.operator == ">":
        return value > cond.threshold
    lo, hi = sorted((cond.threshold, cond.second_threshold))
    return lo <= value <= hi


def validate_entry_conditions(
    conditions: list[EntryCondition],
    *,
    open_price: float | None = None,
    close_price: float | None = None,
    close_observed: bool = False,
) -> EntryConditionVerdict:
    """Evaluate the plan's entry conditions against observed prices.

    Rules (ENTRY-REG-009):

    - A cancel rule (``CANCEL_SETUP``) that fires makes the whole setup
      invalid: ``INVALID_ENTRY_CONDITION``, no buy authorized, decision
      blocked. The 18:53 report had "open below 559.61 cancels the setup"
      *and* "buy the dip toward 557-562": an open at 558 satisfies the dip
      but violates the cancel rule — the cancel rule wins.
    - A close-based condition can only be evaluated once the close is
      observed; before that it authorizes nothing
      (``CLOSE_TRIGGER_NOT_YET_EVALUABLE``) — it can never authorize an
      order at that same candle's price.
    - With no cancel rule fired and no buy condition satisfied, the plan
      simply does not authorize a buy (no block, no order).
    """
    # 1. Cancel rules first: they veto everything else.
    for cond in conditions:
        if cond.action != "CANCEL_SETUP":
            continue
        if cond.basis == "open":
            if open_price is None:
                return EntryConditionVerdict(
                    status="PENDING", reason_code="OPEN_NOT_OBSERVED",
                    authorizes_buy=False, blocks_decision=False,
                    reason="open not yet observed; cancel rule cannot be evaluated",
                )
            if _condition_holds(cond, open_price):
                return EntryConditionVerdict(
                    status=INVALID_ENTRY_CONDITION,
                    reason_code=INVALID_ENTRY_CONDITION,
                    authorizes_buy=False, blocks_decision=True,
                    reason=(
                        f"cancel rule fired: open {open_price} satisfies "
                        f"'{cond.description}'; the setup is cancelled and "
                        "no buy may be authorized"
                    ),
                )

    # 2. Buy conditions: only on observed bases.
    for cond in conditions:
        if cond.action != "BUY":
            continue
        if cond.basis == "close":
            if not close_observed or close_price is None:
                return EntryConditionVerdict(
                    status="PENDING",
                    reason_code="CLOSE_TRIGGER_NOT_YET_EVALUABLE",
                    authorizes_buy=False, blocks_decision=False,
                    reason=(
                        f"condition '{cond.description}' is on the close, "
                        "which has not been observed yet; it cannot "
                        "authorize an order at this candle's price"
                    ),
                )
            if _condition_holds(cond, close_price):
                return EntryConditionVerdict(
                    status="AUTHORIZED", reason_code="OK",
                    authorizes_buy=True, blocks_decision=False,
                    reason=f"close {close_price} satisfies '{cond.description}'",
                )
            continue
        # open/price basis
        value = open_price if cond.basis == "open" else (open_price or close_price)
        if value is None:
            return EntryConditionVerdict(
                status="PENDING", reason_code="PRICE_NOT_OBSERVED",
                authorizes_buy=False, blocks_decision=False,
                reason="price not yet observed",
            )
        if _condition_holds(cond, value):
            return EntryConditionVerdict(
                status="AUTHORIZED", reason_code="OK",
                authorizes_buy=True, blocks_decision=False,
                reason=f"price {value} satisfies '{cond.description}'",
            )

    return EntryConditionVerdict(
        status="NO_SIGNAL", reason_code="OK",
        authorizes_buy=False, blocks_decision=False,
        reason="no cancel rule fired and no buy condition satisfied",
    )

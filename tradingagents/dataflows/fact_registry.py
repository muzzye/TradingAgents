"""Cross-agent fact registry: canonical facts, conflicts, source priority.

The BRK-B report of 2026-11-08 carried a contradiction no single agent
could see: News and Sentiment reported a Q3 buyback of about 9.6 billion
USD while Fundamentals said "no buyback reported". Each statement was
plausible alone; the conflict lived between them. This module is the
deterministic reader that registers the run's material facts from every
agent, detects when two agents assert incompatible values for the same
fact, and refuses to let an absence of vendor data become a negation.

Rules (contract §3.3):

- **Missing ≠ zero.** A vendor that says nothing about a fact has not
  asserted it is zero; "no buyback reported" from an absent field is
  ``NOT_REPORTED``, never a refutation of a sourced amount.
- **Source priority.** A primary source (company release, SEC filing)
  verified beats a secondary one (vendor, news). A conflict is recorded,
  never silently overwritten; the primary's value wins only when verified.
- **Segment dimension.** Facts carry a ``segment`` so consolidated revenue
  is never attributed to a subsidiary (BNSF, GEICO): the same fact key
  under different segments are different facts, not a conflict.
- **Interpretation is free, facts are not.** Two agents may disagree on
  BUY/HOLD; they may not assert two different values for the same fact
  without a period, unit, currency or segment difference explaining it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

# Source tiers: lower number wins a conflict when verified.
PRIMARY_SOURCES = frozenset({"company_release", "sec_filing"})
SECONDARY_SOURCES = frozenset({"vendor_api", "news_article", "prediction_market"})


class FactStatus(StrEnum):
    """The registry's status for one fact key."""

    SINGLE_SOURCE = "SINGLE_SOURCE"       # one agent asserted it, no conflict
    CORROBORATED = "CORROBORATED"         # agents agree on the same value
    CONFLICTING = "CONFLICTING"           # agents assert incompatible values
    NOT_REPORTED = "NOT_REPORTED"         # absence of data, never a zero
    UNRESOLVED = "UNRESOLVED"             # conflict no source priority settles


class ConflictReason(StrEnum):
    """Why a conflict was (or was not) resolved."""

    NONE = "NONE"
    PRIMARY_SOURCE_WINS = "PRIMARY_SOURCE_WINS"
    NO_VERIFIED_PRIMARY = "NO_VERIFIED_PRIMARY"
    DIFFERENT_PERIOD = "DIFFERENT_PERIOD"
    DIFFERENT_SEGMENT = "DIFFERENT_SEGMENT"


@dataclass(frozen=True)
class AgentFact:
    """One material fact as one agent asserted it.

    ``value`` is the asserted amount (None when the agent only said the
    fact is absent); ``unit``/``currency`` make values comparable;
    ``period_end`` and ``segment`` scope the fact so different scopes are
    never conflated; ``verified`` is whether a primary source backs it.
    """

    fact_key: str                     # e.g. "buyback_amount", "revenue"
    agent: str                        # "news", "sentiment", "fundamentals"…
    value: float | None
    unit: str = ""                    # "USD_million", "USD", "ratio"…
    currency: str | None = None
    period: str | None = None         # "2026-Q3"
    period_end: str | None = None     # YYYY-MM-DD
    segment: str | None = None        # "company", "BNSF", "GEICO"…
    source_type: str = "unknown"
    source_uri: str | None = None
    verified: bool = False            # a primary source backs this value
    statement: str = ""               # the agent's own wording, for the audit

    @property
    def is_primary(self) -> bool:
        return self.source_type in PRIMARY_SOURCES

    def comparable_key(self) -> tuple:
        """The scope a value must match to be compared with another's."""
        return (self.fact_key, self.period, self.period_end, self.segment,
                self.unit, self.currency)


@dataclass
class FactConflict:
    """Two agents' incompatible assertions about the same scoped fact."""

    fact_key: str
    period: str | None
    segment: str | None
    unit: str
    currency: str | None
    assertions: list[AgentFact] = field(default_factory=list)
    winner: AgentFact | None = None
    reason: ConflictReason = ConflictReason.NONE

    @property
    def resolved(self) -> bool:
        return self.winner is not None

    def summary(self) -> dict:
        return {
            "fact_key": self.fact_key,
            "period": self.period,
            "segment": self.segment,
            "unit": self.unit,
            "currency": self.currency,
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
            "winner": self.winner.agent if self.winner else None,
            "reason": self.reason.value,
            "resolved": self.resolved,
        }


@dataclass
class FactRegistryReport:
    """All registered facts and conflicts for one run, decision-ready."""

    facts: list[AgentFact] = field(default_factory=list)
    conflicts: list[FactConflict] = field(default_factory=list)
    not_reported: list[str] = field(default_factory=list)

    def add_fact(self, fact: AgentFact) -> None:
        self.facts.append(fact)

    def add_not_reported(self, fact_key: str) -> None:
        """Record an absence as an absence: it never becomes a zero."""
        if fact_key not in self.not_reported:
            self.not_reported.append(fact_key)

    @property
    def unresolved_conflicts(self) -> list[FactConflict]:
        return [c for c in self.conflicts if not c.resolved]

    @property
    def blocks_decision(self) -> bool:
        """An unresolved material conflict must not feed an approval."""
        return bool(self.unresolved_conflicts)

    def summary(self) -> dict:
        return {
            "total_facts": len(self.facts),
            "conflicts": [c.summary() for c in self.conflicts],
            "unresolved": [c.fact_key for c in self.unresolved_conflicts],
            "not_reported": list(self.not_reported),
        }


def _values_agree(a: float, b: float, *, tolerance_pct: float = 1.0) -> bool:
    """Whether two asserted amounts agree within tolerance."""
    if b == 0:
        return a == 0
    return abs(a - b) / abs(b) * 100 <= tolerance_pct


def register_facts(facts: list[AgentFact], *, tolerance_pct: float = 1.0) -> FactRegistryReport:
    """Register every agent's facts and detect the conflicts among them.

    Facts with different scopes (period, segment, unit, currency) are never
    compared. Facts with the same scope are grouped: agreement within
    tolerance corroborates, disagreement conflicts. A conflict is resolved
    by a verified primary source; without one it stays unresolved and the
    caller must block rather than pick a side.
    """
    report = FactRegistryReport()
    report.facts.extend(facts)

    # Group by comparable scope.
    groups: dict[tuple, list[AgentFact]] = {}
    for fact in facts:
        groups.setdefault(fact.comparable_key(), []).append(fact)

    for key, group in sorted(groups.items(), key=lambda kv: str(kv[0])):
        fact_key, period, _period_end, segment, unit, currency = key
        valued = [f for f in group if f.value is not None]
        if len(valued) < 2:
            continue
        reference = valued[0].value
        agreeing = [f for f in valued[1:] if _values_agree(
            f.value, reference, tolerance_pct=tolerance_pct)]
        if len(agreeing) == len(valued) - 1:
            continue  # all agree: corroborated, no conflict to record

        conflict = FactConflict(
            fact_key=fact_key, period=period, segment=segment,
            unit=unit, currency=currency, assertions=list(valued),
        )
        # Source priority: a verified primary source settles the conflict.
        verified_primary = [f for f in valued if f.verified and f.is_primary]
        if verified_primary:
            conflict.winner = verified_primary[0]
            conflict.reason = ConflictReason.PRIMARY_SOURCE_WINS
        else:
            conflict.reason = ConflictReason.NO_VERIFIED_PRIMARY
        report.conflicts.append(conflict)

    return report


def check_negation_against_registry(
    fact_key: str,
    negating_agent: str,
    registry: FactRegistryReport,
    *,
    period: str | None = None,
    segment: str | None = None,
) -> FactConflict | None:
    """Whether a "no X reported" claim contradicts another agent's sourced X.

    Absence of information does not authorize a negation: when another
    agent asserted a value for the same scoped fact, the negation is a
    conflict — resolved only by a verified primary source. Returns the
    conflict (unresolved unless a verified primary backs the negation's
    absence, which no absence ever is), or None when nothing contradicts.
    """
    contradicting = [
        f for f in registry.facts
        if f.fact_key == fact_key and f.agent != negating_agent
        and f.value is not None and f.period == period and f.segment == segment
    ]
    if not contradicting:
        return None
    conflict = FactConflict(
        fact_key=fact_key, period=period, segment=segment,
        unit=contradicting[0].unit, currency=contradicting[0].currency,
        assertions=[
            *contradicting,
            AgentFact(
                fact_key=fact_key, agent=negating_agent, value=None,
                period=period, segment=segment, verified=False,
                statement="no value reported",
            ),
        ],
    )
    # An absence is never a verified primary: the negation cannot win.
    conflict.reason = ConflictReason.NO_VERIFIED_PRIMARY
    return conflict

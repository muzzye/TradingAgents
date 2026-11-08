"""P0 FACT-002 — cross-agent fact registry and conflict detection.

Regression: the BRK-B report's News/Sentiment reported a Q3 buyback of
about 9.6 billion USD while Fundamentals said "no buyback reported". An
absence of vendor data must never become a negation; a conflict must be
recorded and resolved only by a verified primary source. Pure logic, no
network, no LLM.
"""

from __future__ import annotations

from tradingagents.dataflows.fact_registry import (
    AgentFact,
    ConflictReason,
    check_negation_against_registry,
    register_facts,
)


def _news_buyback(**overrides) -> AgentFact:
    base = {
        "fact_key": "buyback_amount", "agent": "news", "value": 9600.0,
        "unit": "USD_million", "currency": "USD", "period": "2026-Q3",
        "period_end": "2026-09-30", "segment": "company",
        "source_type": "company_release", "verified": True,
        "statement": "Q3 buyback of approximately $9.6 billion",
    }
    base.update(overrides)
    return AgentFact(**base)


def _fundamentals_no_buyback(**overrides) -> AgentFact:
    base = {
        "fact_key": "buyback_amount", "agent": "fundamentals", "value": None,
        "unit": "USD_million", "currency": "USD", "period": "2026-Q3",
        "period_end": "2026-09-30", "segment": "company",
        "source_type": "vendor_api", "verified": False,
        "statement": "no buyback reported",
    }
    base.update(overrides)
    return AgentFact(**base)


class TestRegistration:
    def test_agreeing_facts_corroborate(self):
        report = register_facts([
            _news_buyback(),
            AgentFact(fact_key="buyback_amount", agent="sentiment", value=9550.0,
                      unit="USD_million", period="2026-Q3", period_end="2026-09-30",
                      segment="company", source_type="news_article"),
        ])
        assert not report.blocks_decision
        assert report.conflicts == []

    def test_conflicting_values_are_recorded_not_overwritten(self):
        report = register_facts([
            _news_buyback(),
            AgentFact(fact_key="buyback_amount", agent="fundamentals", value=100.0,
                      unit="USD_million", currency="USD", period="2026-Q3",
                      period_end="2026-09-30", segment="company",
                      source_type="vendor_api"),
        ])
        # The conflict is recorded with both assertions, whatever its outcome.
        conflict = report.conflicts[0]
        assert conflict.fact_key == "buyback_amount"
        assert {a.agent for a in conflict.assertions} == {"news", "fundamentals"}
        assert {a.value for a in conflict.assertions} == {9600.0, 100.0}

    def test_a_verified_primary_source_resolves_the_conflict(self):
        report = register_facts([
            _news_buyback(),   # company_release, verified
            AgentFact(fact_key="buyback_amount", agent="fundamentals", value=100.0,
                      unit="USD_million", currency="USD", period="2026-Q3",
                      period_end="2026-09-30", segment="company",
                      source_type="vendor_api"),
        ])
        conflict = report.conflicts[0]
        assert conflict.resolved
        assert conflict.winner.agent == "news"
        assert conflict.reason == ConflictReason.PRIMARY_SOURCE_WINS
        # Resolved conflicts do not block: the primary's value wins.
        assert not report.blocks_decision

    def test_a_conflict_without_a_verified_primary_stays_unresolved(self):
        report = register_facts([
            _news_buyback(verified=False, source_type="news_article"),
            AgentFact(fact_key="buyback_amount", agent="fundamentals", value=100.0,
                      unit="USD_million", currency="USD", period="2026-Q3",
                      period_end="2026-09-30", segment="company",
                      source_type="vendor_api"),
        ])
        conflict = report.conflicts[0]
        assert not conflict.resolved
        assert conflict.reason == ConflictReason.NO_VERIFIED_PRIMARY
        assert report.blocks_decision

    def test_different_periods_are_never_a_conflict(self):
        """Q2 and Q3 revenue are different facts, not disagreement."""
        report = register_facts([
            _news_buyback(period="2026-Q2", period_end="2026-06-30", value=100.0),
            _news_buyback(period="2026-Q3", period_end="2026-09-30", value=9600.0),
        ])
        assert report.conflicts == []

    def test_different_segments_are_never_a_conflict(self):
        """Consolidated revenue is not BNSF revenue: the segment dimension holds."""
        report = register_facts([
            _news_buyback(fact_key="revenue", segment="company", value=100_000.0),
            _news_buyback(fact_key="revenue", segment="BNSF", value=6_000.0),
        ])
        assert report.conflicts == []

    def test_different_units_are_never_a_conflict(self):
        report = register_facts([
            _news_buyback(unit="USD_million", value=9600.0),
            _news_buyback(unit="USD", value=9_600_000_000.0),
        ])
        assert report.conflicts == []

    def test_summary_is_machine_readable(self):
        report = register_facts([
            _news_buyback(),
            AgentFact(fact_key="buyback_amount", agent="fundamentals", value=100.0,
                      unit="USD_million", currency="USD", period="2026-Q3",
                      period_end="2026-09-30", segment="company",
                      source_type="vendor_api"),
        ])
        data = report.summary()
        assert data["total_facts"] == 2
        assert data["conflicts"][0]["fact_key"] == "buyback_amount"
        assert data["conflicts"][0]["winner"] == "news"


class TestNegation:
    def test_absence_does_not_refute_a_sourced_amount(self):
        """'No buyback reported' vs a sourced 9.6B: the negation cannot win."""
        registry = register_facts([_news_buyback()])
        conflict = check_negation_against_registry(
            "buyback_amount", "fundamentals", registry,
            period="2026-Q3", segment="company",
        )
        assert conflict is not None
        assert not conflict.resolved
        assert conflict.reason == ConflictReason.NO_VERIFIED_PRIMARY
        # The negating side is recorded as asserting nothing, not zero.
        negation = next(a for a in conflict.assertions if a.agent == "fundamentals")
        assert negation.value is None

    def test_no_contradiction_when_nobody_else_asserted(self):
        registry = register_facts([])
        assert check_negation_against_registry(
            "buyback_amount", "fundamentals", registry, period="2026-Q3"
        ) is None

    def test_negation_in_a_different_period_is_not_a_contradiction(self):
        registry = register_facts([_news_buyback(period="2026-Q2",
                                                 period_end="2026-06-30")])
        assert check_negation_against_registry(
            "buyback_amount", "fundamentals", registry, period="2026-Q3"
        ) is None

    def test_not_reported_is_recorded_as_an_absence(self):
        registry = register_facts([])
        registry.add_not_reported("dividend_amount")
        assert registry.not_reported == ["dividend_amount"]
        # And it never appears as a zero-valued fact.
        assert all(f.value is not None for f in registry.facts)

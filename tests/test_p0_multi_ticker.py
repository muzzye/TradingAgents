"""P0 end-to-end: the same rules across AAPL, NVDA and ENI.MI.

The contract's ticker-independence requirement: the freshness gate, the
fact registry and the arithmetic checks behave identically for a US
mega-cap, a second US mega-cap and a Milan-listed utility — different
currencies, calendars and sectors, one rule set. All data is frozen; no
network, no LLM, no live prices.
"""

from __future__ import annotations

import pytest

from tradingagents.dataflows.fact_registry import AgentFact, register_facts
from tradingagents.dataflows.fundamentals_freshness import (
    FilingRecord,
    check_fundamentals_freshness,
)
from tradingagents.dataflows.signal_validator import (
    check_stated_move,
    close_to_close_return,
    open_to_close_return,
    validate_stop_side,
)

# Frozen per-ticker filing timelines: each has a published quarter and an
# older one, so the same staleness rule must fire on all three.
AAPL_Q2 = FilingRecord("2026-Q2", "2026-06-28", filing_date="2026-08-01", form="10-Q")
AAPL_Q3 = FilingRecord("2026-Q3", "2026-09-27", filing_date="2026-10-30", form="10-Q")
NVDA_Q2 = FilingRecord("2026-Q2", "2026-07-26", filing_date="2026-08-26", form="10-Q")
NVDA_Q3 = FilingRecord("2026-Q3", "2026-10-25", filing_date="2026-11-20", form="10-Q")
ENI_Q2 = FilingRecord("2026-Q2", "2026-06-30", published_date="2026-07-25", form=None)
ENI_Q3 = FilingRecord("2026-Q3", "2026-09-30", published_date="2026-10-24", form=None)


@pytest.mark.unit
class TestFreshnessAcrossTickers:
    def test_aapl_stale_q2_is_caught(self):
        verdict = check_fundamentals_freshness(
            "2026-06-28", [AAPL_Q2, AAPL_Q3], "2026-11-08", used_period="2026-Q2"
        )
        assert verdict.status == "STALE_CRITICAL"
        assert verdict.latest_known_period == "2026-Q3"

    def test_aapl_current_q3_passes(self):
        verdict = check_fundamentals_freshness(
            "2026-09-27", [AAPL_Q2, AAPL_Q3], "2026-11-08", used_period="2026-Q3"
        )
        assert verdict.status == "CURRENT"

    def test_nvda_q3_not_yet_filed_so_q2_is_current(self):
        """NVDA's Q3 files on 2026-11-20: at 2026-11-08 Q2 is the freshest known."""
        verdict = check_fundamentals_freshness(
            "2026-07-26", [NVDA_Q2, NVDA_Q3], "2026-11-08", used_period="2026-Q2"
        )
        assert verdict.status == "CURRENT"

    def test_nvda_q2_becomes_stale_after_the_q3_filing(self):
        verdict = check_fundamentals_freshness(
            "2026-07-26", [NVDA_Q2, NVDA_Q3], "2026-11-21", used_period="2026-Q2"
        )
        assert verdict.status == "STALE_CRITICAL"

    def test_eni_mi_eur_filing_timeline_follows_the_same_rule(self):
        stale = check_fundamentals_freshness(
            "2026-06-30", [ENI_Q2, ENI_Q3], "2026-11-08", used_period="2026-Q2"
        )
        assert stale.status == "STALE_CRITICAL"
        current = check_fundamentals_freshness(
            "2026-09-30", [ENI_Q2, ENI_Q3], "2026-11-08", used_period="2026-Q3"
        )
        assert current.status == "CURRENT"


@pytest.mark.unit
class TestFactRegistryAcrossTickers:
    def test_the_same_conflict_is_detected_for_each_ticker(self):
        for ticker, unit in (("AAPL", "USD_million"), ("NVDA", "USD_million"),
                             ("ENI.MI", "EUR_million")):
            report = register_facts([
                AgentFact(fact_key="buyback_amount", agent="news", value=900.0,
                          unit=unit, period="2026-Q3", segment="company",
                          source_type="news_article", verified=False),
                AgentFact(fact_key="buyback_amount", agent="fundamentals", value=10.0,
                          unit=unit, period="2026-Q3", segment="company",
                          source_type="vendor_api", verified=False),
            ])
            assert report.blocks_decision, ticker

    def test_currency_scoping_keeps_eni_eur_apart_from_usd(self):
        """ENI's EUR figures and a USD figure are different scopes, not a conflict."""
        report = register_facts([
            AgentFact(fact_key="revenue", agent="news", value=10_000.0,
                      unit="million", currency="EUR", period="2026-Q3",
                      segment="company", source_type="news_article"),
            AgentFact(fact_key="revenue", agent="fundamentals", value=11_000.0,
                      unit="million", currency="USD", period="2026-Q3",
                      segment="company", source_type="vendor_api"),
        ])
        assert report.conflicts == []


@pytest.mark.unit
class TestArithmeticAcrossTickers:
    def test_the_same_candle_math_applies_to_every_quote_currency(self):
        # A USD candle and an EUR candle: identical arithmetic, no per-ticker rule.
        for o, c, prev in ((584.23, 572.33, 579.09), (14.20, 13.95, 14.05)):
            o2c = open_to_close_return(o, c)
            c2c = close_to_close_return(prev, c)
            assert o2c != c2c
            result = check_stated_move(o2c, o, c, prev)
            assert not result.has_errors

    def test_stop_side_rule_is_direction_based_not_ticker_based(self):
        assert not validate_stop_side(100.0, 95.0, "LONG").has_errors
        assert validate_stop_side(100.0, 95.0, "SHORT").has_errors
        assert not validate_stop_side(14.20, 13.50, "LONG").has_errors

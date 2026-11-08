"""P0 integration: the data-quality gates wired into the decision point.

The contract's fail-closed promise at the wiring level: stale fundamentals,
an unresolved cross-agent conflict, or a wrong-side stop must BLOCK the
decision — never pass as an approved plan. The BRK-B 2026-11-08 case is the
regression fixture; the rules are ticker-agnostic.
"""

from __future__ import annotations

import json

from tradingagents.dataflows.fact_registry import AgentFact, register_facts
from tradingagents.dataflows.fundamentals_freshness import FilingRecord
from tradingagents.dataflows.gate_integration import (
    evaluate_decision,
    write_audit_record,
)

Q2 = FilingRecord("2026-Q2", "2026-06-30", published_date="2026-08-02",
                  filing_date="2026-08-07", form="10-Q")
Q3 = FilingRecord("2026-Q3", "2026-09-30", published_date="2026-11-07",
                  form="8-K")


def _state(rating="Hold", decision_text="**Rating**: Hold\nMaintain."):
    return {
        "company_of_interest": "BRK-B",
        "trade_date": "2026-11-08",
        "market_report": "",
        "sentiment_report": "",
        "news_report": "",
        "fundamentals_report": "",
        "investment_debate_state": {"bull_history": "", "bear_history": "",
                                    "history": "", "current_response": ""},
        "trader_investment_plan": "",
        "risk_debate_state": {"aggressive_history": "", "conservative_history": "",
                              "neutral_history": "", "history": ""},
        "investment_plan": "",
        "final_rating": rating,
        "final_trade_decision": decision_text,
    }


class TestFreshnessGate:
    def test_stale_fundamentals_block_the_decision(self):
        """FRESH-001: Q2 used while Q3 was published — BLOCK."""
        record, decision = evaluate_decision(
            "BRK-B", "2026-11-08", _state(), snapshot_id="s1",
            fundamentals_filings=[Q2, Q3],
            used_fundamentals_period_end="2026-06-30",
            used_fundamentals_period="2026-Q2",
        )
        assert decision.action.value == "BLOCK"
        assert "DATA_QUALITY_BLOCKED" in [r.value for r in decision.reasons]
        assert record.fundamentals_freshness.status == "STALE_CRITICAL"

    def test_current_fundamentals_do_not_block(self):
        record, decision = evaluate_decision(
            "BRK-B", "2026-11-08", _state(), snapshot_id="s1",
            fundamentals_filings=[Q2, Q3],
            used_fundamentals_period_end="2026-09-30",
            used_fundamentals_period="2026-Q3",
        )
        assert record.fundamentals_freshness.status == "CURRENT"
        assert "DATA_QUALITY_BLOCKED" not in [r.value for r in decision.reasons]

    def test_no_known_filing_blocks(self):
        """No authoritative filing known: fundamentals cannot be called current."""
        record, decision = evaluate_decision(
            "BRK-B", "2026-11-08", _state(), snapshot_id="s1",
            fundamentals_filings=[],
            used_fundamentals_period_end="2026-06-30",
        )
        assert decision.action.value == "BLOCK"
        assert record.fundamentals_freshness.status == "UNAVAILABLE"

    def test_freshness_reaches_the_audit_json(self, tmp_path):
        record, _ = evaluate_decision(
            "BRK-B", "2026-11-08", _state(), snapshot_id="s1",
            fundamentals_filings=[Q2, Q3],
            used_fundamentals_period_end="2026-06-30",
        )
        path = write_audit_record(record, str(tmp_path))
        data = json.loads(path.read_text(encoding="utf-8"))
        dq = data["data_quality"]
        assert dq["status"] == "BLOCKED_BY_CLAIMS"
        assert "FUNDAMENTALS_STALE" in dq["blocking_claims"]
        assert dq["fundamentals_freshness"]["latest_known_period"] == "2026-Q3"


class TestFactRegistryGate:
    def test_unresolved_conflict_blocks(self):
        """FACT-002: a conflict no verified primary settles must block."""
        report = register_facts([
            AgentFact(fact_key="buyback_amount", agent="news", value=9600.0,
                      unit="USD_million", currency="USD", period="2026-Q3",
                      period_end="2026-09-30", segment="company",
                      source_type="news_article", verified=False),
            AgentFact(fact_key="buyback_amount", agent="fundamentals", value=100.0,
                      unit="USD_million", currency="USD", period="2026-Q3",
                      period_end="2026-09-30", segment="company",
                      source_type="vendor_api", verified=False),
        ])
        record, decision = evaluate_decision(
            "BRK-B", "2026-11-08", _state(), snapshot_id="s1",
            fact_registry_report=report,
        )
        assert decision.action.value == "BLOCK"
        assert "DATA_QUALITY_BLOCKED" in [r.value for r in decision.reasons]

    def test_resolved_conflict_does_not_block(self):
        report = register_facts([
            AgentFact(fact_key="buyback_amount", agent="news", value=9600.0,
                      unit="USD_million", currency="USD", period="2026-Q3",
                      period_end="2026-09-30", segment="company",
                      source_type="company_release", verified=True),
            AgentFact(fact_key="buyback_amount", agent="fundamentals", value=100.0,
                      unit="USD_million", currency="USD", period="2026-Q3",
                      period_end="2026-09-30", segment="company",
                      source_type="vendor_api", verified=False),
        ])
        record, decision = evaluate_decision(
            "BRK-B", "2026-11-08", _state(), snapshot_id="s1",
            fact_registry_report=report,
        )
        assert "DATA_QUALITY_BLOCKED" not in [r.value for r in decision.reasons]
        data = record.to_dict()["data_quality"]
        assert data["fact_registry"]["conflicts"][0]["winner"] == "news"


class TestStopSideGate:
    def test_wrong_side_stop_blocks(self):
        """A LONG stop at or above the entry protects nothing: BLOCK."""
        record, decision = evaluate_decision(
            "BRK-B", "2026-11-08", _state(), snapshot_id="s1",
            entry=100.0, stop=100.0, atr=5.0, position_direction="LONG",
        )
        assert decision.action.value == "BLOCK"
        assert "RISK_PLAN_BLOCKED" in [r.value for r in decision.reasons]

    def test_correct_side_stop_passes_the_side_check(self):
        record, decision = evaluate_decision(
            "BRK-B", "2026-11-08", _state(), snapshot_id="s1",
            entry=100.0, stop=95.0, atr=5.0, position_direction="LONG",
        )
        assert "RISK_PLAN_BLOCKED" not in [r.value for r in decision.reasons]

    def test_short_side_is_checked_against_its_own_rule(self):
        record, decision = evaluate_decision(
            "BRK-B", "2026-11-08", _state(), snapshot_id="s1",
            entry=100.0, stop=95.0, atr=5.0, position_direction="SHORT",
        )
        assert decision.action.value == "BLOCK"


class TestCombinedFailClosed:
    def test_the_brk_b_case_blocks_on_every_axis(self):
        """The 2026-11-08 report's defects together: stale fundamentals, an
        unresolved buyback conflict, a wrong-side stop — one BLOCK with all
        the reason codes, zero orders."""
        report = register_facts([
            AgentFact(fact_key="buyback_amount", agent="news", value=9600.0,
                      unit="USD_million", currency="USD", period="2026-Q3",
                      period_end="2026-09-30", segment="company",
                      source_type="news_article", verified=False),
            AgentFact(fact_key="buyback_amount", agent="fundamentals", value=None,
                      unit="USD_million", currency="USD", period="2026-Q3",
                      period_end="2026-09-30", segment="company",
                      source_type="vendor_api", verified=False),
        ])
        record, decision = evaluate_decision(
            "BRK-B", "2026-11-08", _state(), snapshot_id="s1",
            fundamentals_filings=[Q2, Q3],
            used_fundamentals_period_end="2026-06-30",
            used_fundamentals_period="2026-Q2",
            fact_registry_report=report,
            entry=543.70, stop=543.50, atr=9.408,
            sessions=["2026-11-04", "2026-11-05", "2026-11-06"],
            event_publication_date="2026-11-07",
        )
        assert decision.action.value == "BLOCK"
        reasons = [r.value for r in decision.reasons]
        assert "DATA_QUALITY_BLOCKED" in reasons
        assert "RISK_PLAN_BLOCKED" in reasons
        assert record.to_dict()["execution"]["market_order_allowed"] is False

    def test_a_clean_run_is_not_blocked_by_the_new_gates(self):
        """A run with current fundamentals and no conflicts passes the P0 gates."""
        record, decision = evaluate_decision(
            "BRK-B", "2026-11-08", _state(), snapshot_id="s1",
            fundamentals_filings=[Q2, Q3],
            used_fundamentals_period_end="2026-09-30",
            used_fundamentals_period="2026-Q3",
            fact_registry_report=register_facts([]),
        )
        reasons = [r.value for r in decision.reasons]
        assert "DATA_QUALITY_BLOCKED" not in reasons
        assert record.to_dict()["data_quality"]["status"] == "OK"

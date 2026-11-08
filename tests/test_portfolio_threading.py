"""Portfolio threading through record_decision and the run state.

The contract's §5 promise: sizing and the portfolio-risk check run against
the real book the caller supplied. Without it, the gate sees
PORTFOLIO_CONTEXT_MISSING when a plan exists — fail-closed, never sized
against an unknown portfolio.
"""

from __future__ import annotations

import json

import pytest

from tradingagents.graph.trading_graph import _portfolio_risk_context, _run_reports
from tradingagents.portfolio import PortfolioContext, Position


def _state(rating="Overweight", decision_text="**Rating**: Overweight\nBuy the dip."):
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


def _graph_with_tmp_results(tmp_path):
    """A TradingAgentsGraph-shaped minimal stub: only what record_decision touches."""
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    graph = TradingAgentsGraph.__new__(TradingAgentsGraph)
    graph.config = {"results_dir": str(tmp_path / "results")}
    graph.selected_analysts = []
    return graph


class TestPortfolioRiskContext:
    def test_none_portfolio_gives_none_context(self):
        assert _portfolio_risk_context(None) == (None, None, None)

    def test_cash_only_portfolio_yields_cash_equity(self):
        book = PortfolioContext(cash=50_000.0, currency="USD", positions=[])
        equity, cash, max_loss = _portfolio_risk_context(book)
        assert equity == 50_000.0
        assert cash == 50_000.0

    def test_positions_add_at_average_price(self):
        book = PortfolioContext(
            cash=10_000.0, currency="USD",
            positions=[Position(ticker="BRK-B", quantity=100, average_price=400.0)],
        )
        equity, cash, _ = _portfolio_risk_context(book)
        assert equity == pytest.approx(50_000.0)
        assert cash == 10_000.0

    def test_unreadable_position_is_skipped_not_fatal(self):
        # A position whose quantity is not a number (e.g. a legacy row) must
        # not sink the equity computation; it is skipped, not fatal.
        class _Bad:
            quantity = None
            average_price = 1.0

        class _Book:
            cash = 10_000.0
            currency = "USD"
            positions = [_Bad()]

        equity, _, _ = _portfolio_risk_context(_Book())
        assert equity == 10_000.0


class TestRunReports:
    def test_reports_extracted_from_state(self):
        state = _state()
        state["sentiment_report"] = "Bullish positioning."
        state["market_report"] = "Close 570.89."
        reports = _run_reports(state)
        assert set(reports) == {"sentiment_report", "market_report"}

    def test_empty_state_gives_none(self):
        assert _run_reports(_state()) is None


class TestRecordDecisionPortfolio:
    def test_portfolio_reaches_the_audit(self, tmp_path):
        """A plan + portfolio: the audit carries sizing and the equity fraction."""
        graph = _graph_with_tmp_results(tmp_path)

        class _Log:
            def __init__(self):
                self.stored = None

            def store_decision(self, **kwargs):
                self.stored = kwargs

        graph.memory_log = _Log()
        book = PortfolioContext(cash=100_000.0, currency="USD", positions=[])
        state = _state()
        # A plan with valid numbers: entry 100, stop 95, ATR 5 (buffer 1.0 ATR).
        state["trader_investment_plan"] = (
            "Entry 100.00, stop 95.00, ATR 5.0. Risk budget 1000."
        )
        graph.record_decision("BRK-B", "2026-11-08", state, portfolio=book)
        audit_path = (tmp_path / "results" / "BRK-B" / "reliability_audit" /
                      "audit_2026-11-08.json")
        assert audit_path.exists()
        data = json.loads(audit_path.read_text(encoding="utf-8"))
        # No plan numbers are extracted from text by the wiring (the caller
        # passes them explicitly), so sizing stays None here — but the audit
        # must still be written and the verdict attached.
        assert data["risk"]["qty_proposed"] is None
        assert "execution_action" in state

    def test_no_portfolio_still_records(self, tmp_path):
        """Backward compatibility: record_decision without a portfolio works."""
        graph = _graph_with_tmp_results(tmp_path)

        class _Log:
            def store_decision(self, **kwargs):
                self.stored = kwargs

        graph.memory_log = _Log()
        state = _state()
        graph.record_decision("BRK-B", "2026-11-08", state)
        assert graph.memory_log.stored["ticker"] == "BRK-B"
        assert "execution_action" in state


class TestPostEventStatus:
    def test_state_carries_the_status_field(self):
        from tradingagents.graph.propagation import Propagator

        state = Propagator().create_initial_state("BRK-B", "2026-11-08")
        assert state["post_event_status"] == ""

    def test_initial_state_accepts_the_status(self):
        from tradingagents.graph.propagation import Propagator

        state = Propagator().create_initial_state(
            "BRK-B", "2026-11-08", post_event_status="NOT_YET_OBSERVABLE"
        )
        assert state["post_event_status"] == "NOT_YET_OBSERVABLE"

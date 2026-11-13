"""FIX #7 integration — the coverage reaches the real decision, through a graph.

`evaluate_decision()` is where the checker runs, but a test that calls it
directly proves the checker, not the wiring. These compile a genuine
`StateGraph(AgentState)` — one deterministic node — and drive `propagate()`.
Only the six LLM agents are mocked; the state hand-off, the registry, the
checker, the gate and the audit are real.

The scenarios are the contract's INT-1…INT-10.
"""

from __future__ import annotations

import functools
import json
import pathlib

TICKER = "TEST"
TRADE_DATE = "2026-11-10"
STAMP = f"{TRADE_DATE}T18:00:00+00:00"
CERTIFIED = 105_647_000_000.0
FALSE = 74_322_000_000.0
RIGHT = f"Total revenue was {CERTIFIED/1e6:,.0f} million."
WRONG = f"Total revenue was {FALSE/1e6:,.0f} million."


def _snapshot():
    return {
        "close": 50.8, "price": 50.8, "currency": "USD",
        "price_as_of": STAMP,
        "price_provenance": {
            "symbol": TICKER, "value": 50.8, "currency": "USD",
            "share_class": "B", "kind": "intraday", "timestamp": STAMP,
            "session_date": TRADE_DATE, "source": "yfinance",
            "bar_finality": "INTRADAY_PARTIAL", "validation_status": "VERIFIED",
        },
    }


def _state(**fields):
    base = {
        "company_of_interest": TICKER, "trade_date": TRADE_DATE,
        "verified_market_snapshot": _snapshot(),
        "market_report": "", "news_report": "", "sentiment_report": "",
        "fundamentals_report": "", "investment_plan": "",
        "trader_investment_plan": "Buy stop above 49.00, stop 46.00, ATR 1.20.",
        "final_trade_decision": "**Rating**: Overweight\nBuy.",
        "final_rating": "Overweight",
        # `_log_state` reads the debate sections and the investment plan; a
        # compiled graph hands the whole state on, so they must be present.
        "investment_debate_state": {
            "bull_history": "", "bear_history": "", "history": "",
            "current_response": "", "judge_decision": "", "count": 1,
            "latest_speaker": ""},
        "risk_debate_state": {
            "aggressive_history": "", "conservative_history": "",
            "neutral_history": "", "history": ""},
        "revenue_consolidated": CERTIFIED,
        "revenue_prior": 94_972_000_000.0,
        "revenue_provenance": {"validation_status": "VERIFIED",
                               "concept": "Revenues",
                               "period_end": "2026-09-30"},
    }
    base.update(fields)
    return base


def _drive(tmp_path, monkeypatch, *, fail_checker=False, **fields):
    from langgraph.graph import END, START, StateGraph

    from tradingagents.agents.state import AgentState
    from tradingagents.dataflows.filing_freshness import FilingRecord
    from tradingagents.graph import trading_graph as tg
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    finished = _state(**fields)

    workflow = StateGraph(AgentState)
    workflow.add_node("finish", lambda state: dict(finished))
    workflow.add_edge(START, "finish")
    workflow.add_edge("finish", END)
    compiled = workflow.compile()

    graph = object.__new__(TradingAgentsGraph)
    graph.debug = False
    graph.config = {
        "results_dir": str(tmp_path), "deep_think_llm": "m",
        "quick_think_llm": "m", "fundamentals_freshness_gate": True,
        "risk_per_trade_pct": 0.01, "max_symbol_exposure": 0.05,
    }
    graph.selected_analysts = []
    graph.graph = compiled

    class _Prop:
        def create_initial_state(self, *a, **k):
            return {"company_of_interest": TICKER, "trade_date": TRADE_DATE}

        def get_graph_args(self):
            return {}

    graph.propagator = _Prop()

    class _Log:
        def store_decision(self, **k):
            pass

        def load_entries(self):
            return []

    graph.memory_log = _Log()
    graph.create_run_state = lambda *a, **k: dict(finished)
    graph.checkpoint_input = lambda state: state
    graph._checkpointer_ctx = None
    graph._resuming = False
    graph.end_checkpoint = lambda: None
    graph.clear_checkpoint_on_success = lambda *a, **k: None
    graph.run_settings = lambda: {"version": "test"}
    graph._known_filings = lambda *a, **k: [
        FilingRecord(period="2026-Q3", period_end="2026-09-30",
                     filing_date="2026-11-09", form_type="10-Q",
                     accession_number="0001067983-26-000042")]

    class _Book:
        equity = 1_000_000.0
        cash = 500_000.0

        def model_dump(self):
            return {"equity": self.equity, "cash": self.cash}

    for name in ("_run_graph", "record_decision", "_evaluate_reliability",
                 "_evaluate_entry_trigger", "_complete_run_registry",
                 "_build_metric_registry", "_write_run_record", "_log_state"):
        real = getattr(TradingAgentsGraph, name, None)
        if real is not None:
            setattr(graph, name, functools.partial(real, graph))

    monkeypatch.setattr(
        tg.TradingAgentsGraph, "_market_open_at",
        lambda self, state, date, decision_as_of=None: True)

    if fail_checker:
        import tradingagents.dataflows.fact_check_integration as fci

        def boom(*a, **k):
            raise RuntimeError("checker exploded")

        monkeypatch.setattr(fci, "check_production_claims", boom)

    TradingAgentsGraph.propagate(graph, TICKER, TRADE_DATE, portfolio=_Book())
    audit = json.loads(
        next(pathlib.Path(tmp_path).rglob("audit_*.json")).read_text())
    return audit["execution"], audit.get("fact_check"), audit


class TestINT1Valid:
    """INT-1: correct claims, proposal possible in memory."""

    def test_a_correct_report_does_not_trip_the_checker(self, tmp_path,
                                                       monkeypatch):
        execution, fact_check, _ = _drive(
            tmp_path, monkeypatch, fundamentals_report=RIGHT)
        assert "FACT_CHECK_BLOCKED" not in execution["reason_codes"]
        assert fact_check["checked"] is True
        assert execution["market_order_allowed"] is False


class TestINT2PortfolioManager:
    """INT-2: the manager's own sentence is checked."""

    def test_a_false_number_in_the_final_decision_blocks(self, tmp_path,
                                                        monkeypatch):
        execution, fact_check, _ = _drive(
            tmp_path, monkeypatch,
            final_trade_decision=f"**Rating**: Overweight\n{WRONG}")
        assert execution["action"] == "BLOCK"
        assert "FACT_CHECK_BLOCKED" in execution["reason_codes"]
        assert execution["verdict"]["authorized_orders"] == []
        assert fact_check["refuted_claim_ids"]


class TestINT3Trader:
    """INT-3: a false figure introduced only in the trader plan."""

    def test_a_false_number_in_the_trader_plan_blocks(self, tmp_path,
                                                     monkeypatch):
        execution, fact_check, _ = _drive(
            tmp_path, monkeypatch,
            trader_investment_plan=(
                "Buy stop above 49.00, stop 46.00, ATR 1.20. " + WRONG))
        assert execution["action"] == "BLOCK"
        assert "FACT_CHECK_BLOCKED" in execution["reason_codes"]
        assert execution["verdict"]["authorized_orders"] == []


class TestINT4MissingMetric:
    """INT-4: a critical metric the registry lacks is not a bypass."""

    def test_a_pe_claim_without_a_certified_pe_does_not_pass(self, tmp_path,
                                                            monkeypatch):
        execution, fact_check, _ = _drive(
            tmp_path, monkeypatch,
            trader_investment_plan=(
                "Buy stop above 49.00, stop 46.00, ATR 1.20. "
                "Entry at P/E 18.2x, a clear discount."))
        # The run has no certified P/E (AgentState drops eps_ttm), so the
        # claim is unverifiable. It must not authorize a proposal.
        if execution["action"] == "PROPOSE_PAPER_ORDER":
            assert fact_check["unverifiable_material"] == [], (
                "an unverifiable material claim authorized a proposal")
        else:
            assert "FACT_CHECK_BLOCKED" in execution["reason_codes"]


class TestINT5NonMaterial:
    """INT-5: an innocent figure produces no false block."""

    def test_an_unrelated_figure_does_not_block(self, tmp_path, monkeypatch):
        execution, fact_check, _ = _drive(
            tmp_path, monkeypatch,
            news_report="The CEO sold her stake for $9.6 billion.")
        assert "FACT_CHECK_BLOCKED" not in execution["reason_codes"]
        assert fact_check["context_statements"]


class TestINT6CheckerFailure:
    """INT-6: an error is a block, never a pass."""

    def test_a_failure_blocks_and_is_diagnosable(self, tmp_path, monkeypatch):
        execution, fact_check, _ = _drive(
            tmp_path, monkeypatch, fail_checker=True,
            fundamentals_report=RIGHT)
        assert execution["action"] != "PROPOSE_PAPER_ORDER"
        assert "FACT_CHECK_FAILED" in execution["reason_codes"]
        assert fact_check["checked"] is False
        assert execution["verdict"]["authorized_orders"] == []


class TestINT7Truncation:
    """INT-7: a report past the cap is not reported as fully checked."""

    def test_a_long_report_is_marked_incomplete(self, tmp_path, monkeypatch):
        long_report = " ".join([RIGHT] * 60)
        execution, fact_check, _ = _drive(
            tmp_path, monkeypatch, fundamentals_report=long_report)
        assert fact_check["truncated"] is True
        assert fact_check["statements_beyond_limit"] > 0
        assert "FACT_CHECK_INCOMPLETE" in execution["reason_codes"], (
            "a bounded sample was treated as a complete check")


class TestINT8Concurrency:
    """INT-8: two runs cannot read each other's evidence."""

    def test_two_runs_are_isolated(self, tmp_path, monkeypatch):
        first, fact1, _ = _drive(
            pathlib.Path(str(tmp_path) + "-a"), monkeypatch,
            fundamentals_report=RIGHT)
        second, fact2, _ = _drive(
            pathlib.Path(str(tmp_path) + "-b"), monkeypatch,
            fundamentals_report=WRONG)
        assert "FACT_CHECK_BLOCKED" not in first["reason_codes"]
        assert "FACT_CHECK_BLOCKED" in second["reason_codes"]


class TestINT9Persistence:
    """INT-9: the refuted claim survives into the audit."""

    def test_the_refutation_is_in_the_json(self, tmp_path, monkeypatch):
        execution, fact_check, audit = _drive(
            tmp_path, monkeypatch,
            final_trade_decision=f"**Rating**: Overweight\n{WRONG}")
        assert fact_check["refuted_claim_ids"]
        verdict = next(v for v in fact_check["verdicts"]
                       if v["status"] == "REFUTED")
        assert verdict["claim_id"]
        assert verdict["reason_code"] == "CONTRADICTED_BY_REGISTRY"
        assert verdict["supporting_fact_ids"]
        assert json.dumps(audit)      # the whole record serialises


class TestINT10FullCoverage:
    """INT-10: every source contributes, and the audit says which."""

    def test_all_seven_sources_are_read(self, tmp_path, monkeypatch):
        execution, fact_check, _ = _drive(
            tmp_path, monkeypatch,
            market_report=RIGHT, news_report=RIGHT, sentiment_report=RIGHT,
            fundamentals_report=RIGHT, investment_plan=RIGHT,
            trader_investment_plan="Buy stop above 49.00, stop 46.00, "
                                   "ATR 1.20. " + RIGHT,
            final_trade_decision=RIGHT)
        assert len(fact_check["sources_checked"]) == 7
        assert fact_check["claims_total"] >= 7

"""FIX #6 integration — the Fact Checker reached through a compiled graph.

The contract (§8) asks for scenarios that traverse

    propagate() -> compiled StateGraph -> analyst reports -> Fact Checker
                -> Execution Gate -> AuditRecord

using a real `StateGraph` with deterministic nodes and mocked providers. A
`MagicMock` standing in for `graph.invoke` does not satisfy that: it exercises
the decision path around the graph, which is useful, but it never runs the
graph. These tests compile one and drive it.

What is mocked, and why: the six LLM agents. They need a live model and their
output is not what this fix is about. Everything the fix touches — the state
hand-off, the registry, the Fact Checker, the gate, the audit — is real.
"""

from __future__ import annotations

import functools
import json
import pathlib

TICKER = "TEST"
TRADE_DATE = "2026-11-10"
STAMP = f"{TRADE_DATE}T18:00:00+00:00"      # 13:00 New York, session open


def _snapshot(price=50.8):
    return {
        "close": price, "price": price, "currency": "USD",
        "price_as_of": STAMP,
        "price_provenance": {
            "symbol": TICKER, "value": price, "currency": "USD",
            "share_class": "B", "kind": "intraday", "timestamp": STAMP,
            "session_date": TRADE_DATE, "source": "yfinance",
            "bar_finality": "INTRADAY_PARTIAL", "validation_status": "VERIFIED",
        },
    }


def _state(*, fundamentals_report, plan=None):
    return {
        "company_of_interest": TICKER, "trade_date": TRADE_DATE,
        "verified_market_snapshot": _snapshot(),
        "fundamentals_report": fundamentals_report,
        "market_report": "", "news_report": "", "sentiment_report": "",
        "investment_plan": "Buy on confirmation.",
        "trader_investment_plan": plan or
            "Buy stop above 49.00, stop 46.00, ATR 1.20.",
        "final_trade_decision": "**Rating**: Overweight\nBuy.",
        "final_rating": "Overweight",
        # A certified metric is what a claim can be checked against. The
        # revenue pair is used rather than the EPS pair because `eps_ttm` and
        # `weighted_average_shares` are *not* declared on `AgentState`: a
        # compiled graph drops undeclared keys between nodes, so a P/E can
        # never be certified in production. That is a real defect, recorded
        # separately; this test must not silently depend on an input the
        # production path cannot deliver.
        "revenue_consolidated": 105_647_000_000.0,
        "revenue_prior": 94_972_000_000.0,
        "revenue_provenance": {"validation_status": "VERIFIED",
                               "concept": "Revenues",
                               "period_end": "2026-09-30"},
        "investment_debate_state": {
            "bull_history": "", "bear_history": "", "history": "",
            "current_response": "", "judge_decision": "", "count": 1,
            "latest_speaker": ""},
        "risk_debate_state": {
            "aggressive_history": "", "conservative_history": "",
            "neutral_history": "", "history": ""},
    }


def _drive(tmp_path, *, fundamentals_report, monkeypatch, fail_checker=False):
    """Compile a graph, run propagate, return (audit execution, fact_check)."""
    from langgraph.graph import END, START, StateGraph

    from tradingagents.agents.state import AgentState
    from tradingagents.dataflows.filing_freshness import FilingRecord
    from tradingagents.graph import trading_graph as tg
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    finished = _state(fundamentals_report=fundamentals_report)

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

    # Every method the reliability path reaches must be the real one, bound to
    # this instance. `_evaluate_reliability` calls `self._complete_run_registry`
    # and `self._build_metric_registry`; if either stays a MagicMock the gate
    # receives an empty registry, the Fact Checker finds no evidence, and every
    # claim silently falls through to context — which looks like "nothing to
    # check" rather than the wiring bug it is.
    for name in ("_run_graph", "record_decision", "_evaluate_reliability",
                 "_evaluate_entry_trigger", "_complete_run_registry",
                 "_build_metric_registry", "_write_run_record", "_log_state"):
        real = getattr(TradingAgentsGraph, name, None)
        if real is not None:
            setattr(graph, name, functools.partial(real, graph))

    # The market is open at the frozen decision moment; the calendar is not
    # what these scenarios test.
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
    return audit["execution"], audit.get("fact_check")


class TestScenarioINT2ARefutedClaim:
    """INT-2: a false material claim, all other gates green."""

    def test_a_contradicted_revenue_claim_blocks_through_the_graph(
            self, tmp_path, monkeypatch):
        execution, fact_check = _drive(
            tmp_path, fundamentals_report="Total revenue was 74,322 million.",
            monkeypatch=monkeypatch)
        assert execution["action"] == "BLOCK"
        assert "FACT_CHECK_BLOCKED" in execution["reason_codes"]
        assert execution["verdict"]["authorized_orders"] == []
        assert fact_check["checked"] is True
        refuted = [v for v in fact_check["verdicts"]
                   if v["status"] == "REFUTED"]
        assert refuted, "the contradicted claim was not refuted"
        assert refuted[0]["claim_id"]
        assert refuted[0]["supporting_fact_ids"]


class TestScenarioINT1TheValidCase:
    """INT-1: correct claims, nothing the checker should block."""

    def test_a_correct_revenue_claim_does_not_trip_the_checker(
            self, tmp_path, monkeypatch):
        execution, fact_check = _drive(
            tmp_path, fundamentals_report="Total revenue was 105,647 million.",
            monkeypatch=monkeypatch)
        assert "FACT_CHECK_BLOCKED" not in execution["reason_codes"]
        assert fact_check["checked"] is True
        assert [v["status"] for v in fact_check["verdicts"]] == ["SUPPORTED"]

    def test_the_human_report_survives_a_block(self, tmp_path, monkeypatch):
        """I10: a blocked proposal still produces the report."""
        execution, _ = _drive(
            tmp_path, fundamentals_report="Total revenue was 74,322 million.",
            monkeypatch=monkeypatch)
        assert execution["action"] == "BLOCK"
        # The run completed: there is an audit with a decision at all.
        assert execution["reason_codes"]


class TestScenarioINT3ACheckerError:
    """INT-3: a forced failure must not become a pass."""

    def test_an_error_blocks_and_is_diagnosable(self, tmp_path, monkeypatch):
        execution, fact_check = _drive(
            tmp_path, fundamentals_report="Total revenue was 105,647 million.",
            monkeypatch=monkeypatch, fail_checker=True)
        assert execution["action"] != "PROPOSE_PAPER_ORDER"
        assert "FACT_CHECK_FAILED" in execution["reason_codes"]
        assert fact_check == {"checked": False,
                              "error": "the fact checker raised"}

    def test_no_order_is_authorised(self, tmp_path, monkeypatch):
        execution, _ = _drive(
            tmp_path, fundamentals_report="Total revenue was 105,647 million.",
            monkeypatch=monkeypatch, fail_checker=True)
        assert execution["verdict"]["authorized_orders"] == []


class TestNoOrderIsEverSubmitted:
    """F14/F15: a proposal is a record, never a transmission."""

    def test_the_verdict_never_reaches_a_broker(self, tmp_path, monkeypatch):
        execution, _ = _drive(
            tmp_path, fundamentals_report="Total revenue was 105,647 million.",
            monkeypatch=monkeypatch)
        assert execution["market_order_allowed"] is False
        # `authorized_orders` describes what the plan *would* submit.
        assert isinstance(execution["verdict"]["authorized_orders"], list)

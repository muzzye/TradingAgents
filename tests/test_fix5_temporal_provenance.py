"""FIX #5 final — temporal provenance of the price the gate acts on.

A. `qualified_price()` certifies an `EOD_CONFIRMED` bar as VERIFIED without
   ever checking the stamp against the exchange calendar. A 13:00 New York
   stamp was accepted as a settled close while the session was still running.
B. Two timestamps describe the same quote — `snapshot["price_as_of"]` and
   `price_provenance["timestamp"]` — and the gate and the trigger read
   different ones. When they disagree, freshness is checked against one stamp
   and the decision acts on another.

Every figure is a frozen constant; no network, no broker, no live price.
"""

from __future__ import annotations

from datetime import UTC, datetime

from tradingagents.dataflows.math.entry_trigger import (
    TriggerOutcome,
    evaluate_entry_trigger,
)
from tradingagents.dataflows.math.price_source import qualified_price

TICKER = "TEST"
TRADE_DATE = "2026-11-10"


def _stamp(hour: int, minute: int = 0, *, day: str = TRADE_DATE) -> str:
    """A New York wall-clock moment as a UTC instant (EST = UTC-5)."""
    return datetime.fromisoformat(
        f"{day}T{hour:02d}:{minute:02d}:00-05:00").astimezone(UTC).isoformat()


def _intraday(price=550.00, *, stamp=None):
    stamp = stamp or _stamp(13, 0)
    provenance = qualified_price(
        TICKER, price, bar_finality="INTRADAY_PARTIAL",
        session_date=TRADE_DATE, observation_timestamp=stamp)
    return {"close": price, "price": price, "currency": "USD",
            "price_as_of": stamp, "price_provenance": provenance}


def _eod(price=550.00, *, stamp=None):
    stamp = stamp or _stamp(16, 0)
    provenance = qualified_price(
        TICKER, price, bar_finality="EOD_CONFIRMED",
        session_date=TRADE_DATE, observation_timestamp=stamp)
    return {"close": price, "price": price, "currency": "USD",
            "price_as_of": stamp, "price_provenance": provenance}


def _registry():
    from tradingagents.dataflows.math.run_registry import (
        RunInputs,
        build_run_registry,
    )

    return build_run_registry(
        RunInputs(price=550.00,
                  price_provenance=qualified_price(
                      TICKER, 550.00, bar_finality="INTRADAY_PARTIAL",
                      session_date=TRADE_DATE,
                      observation_timestamp=_stamp(13, 0)),
                  eps_ttm=2.0, weighted_average_shares=1e9),
        run_id="fix5t")


def _verdict(snapshot, *, entry=540.00, mode="BREAKOUT", direction="LONG"):
    return evaluate_entry_trigger(
        entry=entry, mode=mode, direction=direction,
        snapshot=snapshot, trade_date=TRADE_DATE)


def _decide(plan, *, trigger, snapshot, decision_as_of=None):
    from tradingagents.dataflows.gate_integration import evaluate_decision

    state = {
        "company_of_interest": TICKER, "trade_date": TRADE_DATE,
        "verified_market_snapshot": snapshot,
        "fundamentals_report": "", "final_rating": "Overweight",
        "final_trade_decision": "**Rating**: Overweight\nBuy.",
        "trader_investment_plan": plan,
    }
    kwargs = {}
    if decision_as_of is not None:
        kwargs["decision_as_of"] = decision_as_of
    return evaluate_decision(
        TICKER, TRADE_DATE, state, snapshot_id="fix5t",
        metric_registry=_registry(), entry_trigger=trigger, market_open=True,
        equity=1_000_000.0, cash=500_000.0, risk_budget=1000.0,
        max_symbol_exposure=0.05, **kwargs)


# ===========================================================================
# A — an EOD close cannot be certified before the session has ended
# ===========================================================================
class TestAnEodCloseMustFollowTheClose:
    """`EOD_CONFIRMED` is a claim about time, and the calendar can refute it."""

    def test_an_eod_stamped_at_thirteen_new_york_is_not_verified(self):
        """T1: the session runs to 16:00; a 13:00 close does not exist yet."""
        provenance = qualified_price(
            TICKER, 550.00, bar_finality="EOD_CONFIRMED",
            session_date=TRADE_DATE,
            observation_timestamp=_stamp(13, 0))
        assert provenance["validation_status"] != "VERIFIED", (
            "a 13:00 New York stamp was certified as a settled close while "
            "the session was still running")
        assert provenance["kind"] != "eod_close" or \
            provenance["validation_status"] == "UNVERIFIED"

    def test_an_eod_stamped_at_the_regular_close_is_verified(self):
        """T2: 16:00 New York is the close, and it is a valid settled bar."""
        provenance = qualified_price(
            TICKER, 550.00, bar_finality="EOD_CONFIRMED",
            session_date=TRADE_DATE,
            observation_timestamp=_stamp(16, 0))
        assert provenance["validation_status"] == "VERIFIED"
        assert provenance["kind"] == "eod_close"

    def test_an_eod_stamped_at_an_early_close_is_verified(self):
        """T2: 2026-11-27 closes at 13:00, so 13:00 is a valid EOD there."""
        provenance = qualified_price(
            TICKER, 550.00, bar_finality="EOD_CONFIRMED",
            session_date="2026-11-27",
            observation_timestamp=_stamp(13, 0, day="2026-11-27"))
        assert provenance["validation_status"] == "VERIFIED"

    def test_an_eod_on_a_holiday_is_not_verified(self):
        """T2: no session, so no close to certify."""
        provenance = qualified_price(
            TICKER, 550.00, bar_finality="EOD_CONFIRMED",
            session_date="2026-11-26",
            observation_timestamp=_stamp(16, 0, day="2026-11-26"))
        assert provenance["validation_status"] != "VERIFIED"

    def test_an_eod_without_a_timestamp_is_not_a_contradiction(self):
        """Absence is not disagreement.

        A daily bar is settled by definition for its session and
        `bar_finality` already asserts it; refusing the absent case broke every
        caller that supplies a session date and no intraday instant, which is
        the normal shape for an EOD bar. Only a stamp that *disagrees* with the
        calendar refutes the claim.
        """
        provenance = qualified_price(
            TICKER, 550.00, bar_finality="EOD_CONFIRMED",
            session_date=TRADE_DATE)
        assert provenance["validation_status"] == "VERIFIED"
        assert provenance["kind"] == "eod_close"

    def test_an_eod_stamped_on_another_day_than_its_session_is_refused(self):
        """The stamp must belong to the session the bar claims to close."""
        provenance = qualified_price(
            TICKER, 550.00, bar_finality="EOD_CONFIRMED",
            session_date=TRADE_DATE,
            observation_timestamp=_stamp(16, 0, day="2026-11-09"))
        assert provenance["validation_status"] != "VERIFIED"

    def test_t1_a_false_eod_authorises_no_proposal(self):
        """T1 end to end: the whole gate, not only the provenance layer."""
        snapshot = _eod(550.00, stamp=_stamp(13, 0))
        verdict = _verdict(snapshot)
        record, decision = _decide(
            "Buy stop above 540.00, stop 520.00, ATR 1.20.",
            trigger=verdict, snapshot=snapshot,
            decision_as_of=_stamp(13, 1))
        assert decision.creates_order is False
        assert decision.action.value != "PROPOSE_PAPER_ORDER"
        # The reason must be visible, not an unexplained silence.
        assert record.entry_trigger is not None

    def test_t2_a_real_eod_is_usable_but_is_not_an_intraday_trigger(self):
        """T2: a settled close certifies analysis; it does not open a trade.

        A close stamped 16:00 read at 13:00 the same day is data from the
        future, so the trigger cannot act on it. And a close is not a live
        print: it is dated by its session, which the freshness rule respects.
        """
        snapshot = _eod(550.00, stamp=_stamp(16, 0))
        # Read during the session, before the close existed.
        verdict = _verdict(snapshot)
        record, decision = _decide(
            "Buy stop above 540.00, stop 520.00, ATR 1.20.",
            trigger=verdict, snapshot=snapshot,
            decision_as_of=_stamp(13, 1))
        assert decision.creates_order is False, (
            "a 16:00 close was used to authorize a 13:01 decision")

    def test_a_historical_eod_still_certifies_a_valuation(self):
        """T2: the fix must not break valuations that legitimately use a close."""
        from tradingagents.dataflows.math.run_registry import (
            RunInputs,
            build_run_registry,
        )

        provenance = qualified_price(
            TICKER, 575.25, bar_finality="EOD_CONFIRMED",
            session_date="2026-11-09",
            observation_timestamp=_stamp(16, 0, day="2026-11-09"))
        registry = build_run_registry(
            RunInputs(price=575.25, price_provenance=provenance,
                      eps_ttm=39.76, weighted_average_shares=1_437_000_000.0),
            run_id="fix5t-eod")
        assert registry.get("pe_trailing").is_usable is True, (
            "a correctly certified historical close must still price a metric")


# ===========================================================================
# B — one canonical timestamp for the quote the trigger acts on
# ===========================================================================
class TestTheQuoteHasOneTimestamp:
    """Two stamps describing one observation must agree, or nothing happens."""

    def test_t3_divergent_timestamps_prevent_a_proposal(self):
        """`price_as_of` 13:00, `provenance.timestamp` 18:00, decision 13:01."""
        snapshot = _intraday(550.00, stamp=_stamp(13, 0))
        snapshot["price_provenance"]["timestamp"] = _stamp(18, 0)
        verdict = _verdict(snapshot)
        record, decision = _decide(
            "Buy stop above 540.00, stop 520.00, ATR 1.20.",
            trigger=verdict, snapshot=snapshot,
            decision_as_of=_stamp(13, 1))
        assert decision.creates_order is False, (
            "freshness was checked against 13:00 and the decision acted on a "
            "quote declaring 18:00")

    def test_t3_the_audit_names_the_disagreement(self):
        snapshot = _intraday(550.00, stamp=_stamp(13, 0))
        snapshot["price_provenance"]["timestamp"] = _stamp(18, 0)
        verdict = _verdict(snapshot)
        record, _ = _decide(
            "Buy stop above 540.00, stop 520.00, ATR 1.20.",
            trigger=verdict, snapshot=snapshot,
            decision_as_of=_stamp(13, 1))
        reason = (record.entry_trigger.reason or "").lower()
        # The refusal must name the disagreement, not merely be silent.
        assert any(word in reason for word in
                   ("stamped", "timestamp", "two observations",
                    "contradict")), (
            f"the refusal gives no reason a reader can act on: {reason!r}")
        # Both stamps should be visible, so the reader can see which disagreed.
        payload = record.to_dict()["execution"]["entry_trigger"]
        assert payload["reason"], "the audit dropped the reason"

    def test_t4_an_incoherent_price_prevents_a_proposal(self):
        """`close` 530 with `provenance.value` 550 for the same observation."""
        snapshot = _intraday(530.00, stamp=_stamp(13, 0))
        snapshot["price_provenance"]["value"] = 550.00
        verdict = _verdict(snapshot)
        _, decision = _decide(
            "Buy stop above 540.00, stop 520.00, ATR 1.20.",
            trigger=verdict, snapshot=snapshot,
            decision_as_of=_stamp(13, 1))
        assert decision.creates_order is False, (
            "the snapshot and its provenance disagreed about the price and a "
            "proposal was produced anyway")

    def test_t4_the_trigger_itself_refuses_the_incoherence(self):
        snapshot = _intraday(530.00, stamp=_stamp(13, 0))
        snapshot["price_provenance"]["value"] = 550.00
        verdict = _verdict(snapshot)
        assert verdict.met is False
        assert verdict.outcome is not TriggerOutcome.MET

    def test_t5_a_coherent_intraday_quote_still_proposes(self):
        """The control: same price and stamp everywhere, session open."""
        snapshot = _intraday(550.00, stamp=_stamp(13, 0))
        verdict = _verdict(snapshot)
        assert verdict.met is True
        _, decision = _decide(
            "Buy stop above 540.00, stop 520.00, ATR 1.20.",
            trigger=verdict, snapshot=snapshot,
            decision_as_of=_stamp(13, 1))
        assert decision.action.value == "PROPOSE_PAPER_ORDER"
        assert decision.creates_order is True

    def test_a_naive_timestamp_is_refused(self):
        snapshot = _intraday(550.00, stamp=_stamp(13, 0))
        snapshot["price_provenance"]["timestamp"] = "2026-11-10T18:00:00"
        verdict = _verdict(snapshot)
        assert verdict.met is False

    def test_a_summer_stamp_is_read_in_the_right_offset(self):
        """DST: 13:00 New York in July is 17:00 UTC, not 18:00."""
        summer = "2026-07-15"
        stamp = datetime.fromisoformat(
            f"{summer}T13:00:00-04:00").astimezone(UTC).isoformat()
        assert stamp.startswith("2026-07-15T17:00")
        snapshot = {
            "close": 550.00, "price": 550.00, "currency": "USD",
            "price_as_of": stamp,
            "price_provenance": qualified_price(
                TICKER, 550.00, bar_finality="INTRADAY_PARTIAL",
                session_date=summer, observation_timestamp=stamp)}
        verdict = evaluate_entry_trigger(
            entry=540.00, mode="BREAKOUT", direction="LONG",
            snapshot=snapshot, trade_date=summer)
        assert verdict.met is True


class TestTheCompiledGraphPath:
    """T-INT: `propagate()` through a real `StateGraph`, frozen clock.

    A single deterministic node writes the finished state and the graph is
    compiled for real — no MagicMock standing in for `invoke`. The point is the
    wiring: does the temporal verdict survive the compiled graph into the
    audit? This is not the multi-agent pipeline (that needs live LLMs); it is
    the state hand-off the original defect hid in.
    """

    def _drive(self, tmp_path, snapshot):
        import functools
        import json
        import pathlib

        from langgraph.graph import END, START, StateGraph

        from tradingagents.agents.state import AgentState

        finished = {
            "company_of_interest": TICKER, "trade_date": TRADE_DATE,
            "verified_market_snapshot": snapshot,
            "trader_investment_plan":
                "Buy stop above 540.00, stop 520.00, ATR 1.20.",
            "investment_plan": "Buy on confirmation.",
            "final_trade_decision": "**Rating**: Overweight\nBuy.",
            "final_rating": "Overweight",
            "fundamentals_report": "Reviewed Q3 2026 results.",
            "market_report": "", "news_report": "", "sentiment_report": "",
            "eps_ttm": 2.0, "weighted_average_shares": 1e9,
            "investment_debate_state": {
                "bull_history": "", "bear_history": "", "history": "",
                "current_response": "", "judge_decision": "", "count": 1,
                "latest_speaker": ""},
            "risk_debate_state": {
                "aggressive_history": "", "conservative_history": "",
                "neutral_history": "", "history": ""},
        }

        workflow = StateGraph(AgentState)
        workflow.add_node("finish", lambda state: dict(finished))
        workflow.add_edge(START, "finish")
        workflow.add_edge("finish", END)
        compiled = workflow.compile()

        from tradingagents.dataflows.filing_freshness import FilingRecord
        from tradingagents.graph.trading_graph import TradingAgentsGraph

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
                     "_write_run_record", "_log_state"):
            real = getattr(TradingAgentsGraph, name, None)
            if real is not None:
                setattr(graph, name, functools.partial(real, graph))

        TradingAgentsGraph.propagate(graph, TICKER, TRADE_DATE,
                                     portfolio=_Book())
        return json.loads(
            next(pathlib.Path(tmp_path).rglob("audit_*.json")).read_text()
        )["execution"]

    def test_t1_a_false_eod_is_refused_through_the_graph(self, tmp_path):
        """The 13:00 `EOD_CONFIRMED` never reaches the trigger as a price."""
        execution = self._drive(
            tmp_path, _eod(550.00, stamp=_stamp(13, 0)))
        assert execution["action"] != "PROPOSE_PAPER_ORDER"
        assert execution["entry_trigger"] is not None, (
            "the temporal verdict must be recorded, not silently dropped")

    def test_t5_a_coherent_intraday_quote_reaches_the_gate(self, tmp_path):
        """The control: a sound mid-session print, carried through the graph."""
        from unittest.mock import patch

        from tradingagents.graph import trading_graph as tg

        with patch.object(tg.TradingAgentsGraph, "_market_open_at",
                          lambda self, s, d, decision_as_of=None: True):
            execution = self._drive(
                tmp_path, _intraday(550.00, stamp=_stamp(13, 0)))
        assert execution["entry_trigger"]["observed_price"] == 550.00
        assert execution["entry_trigger"]["observed_status"] == "VERIFIED"
        assert execution["risk_sizing_price"] == 550.00
        assert execution["trigger_level"] == 540.00

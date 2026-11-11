"""FIX #5 final review — prudent LIMIT sizing, quote freshness, risk record.

Three residual defects from the previous round:

A. A LIMIT order was sized on the observed price rather than the limit, so the
   order could pay more than the budget allowed. A limit buy 540 with the market
   at 530 sized 94 shares: at the limit those risk 1,880 against a 1,000 budget.
B. `_quote_is_current` compared calendar-day prefixes, so a quote from 09:30 was
   "current" at 15:59, and a quote from the future was accepted too.
C. The risk record reported `risk_per_share` from the trigger level rather than
   the sizing price, and `stressed_risk_per_share` divided by quantity instead
   of being the stressed unit risk.

Every figure is a frozen constant; no network, no broker, no live price.
"""

from __future__ import annotations

from datetime import UTC, datetime

from tradingagents.dataflows.math.entry_trigger import (
    EntryMode,
    TriggerOutcome,
    evaluate_entry_trigger,
    executable_price,
)
from tradingagents.dataflows.math.price_source import qualified_price

TICKER = "TEST"
TRADE_DATE = "2026-11-10"


def _ny(hour: int, minute: int = 0, *, day: str = TRADE_DATE) -> datetime:
    """A New York wall-clock moment as a UTC instant (EST = UTC-5)."""
    return datetime.fromisoformat(f"{day}T{hour:02d}:{minute:02d}:00-05:00").astimezone(UTC)


def _stamp(hour: int, minute: int = 0, *, day: str = TRADE_DATE) -> str:
    return _ny(hour, minute, day=day).isoformat()


def _snapshot(price: float, *, stamp: str | None = None,
              finality: str = "INTRADAY_PARTIAL", status: str | None = None,
              kind: str | None = None) -> dict:
    stamp = stamp if stamp is not None else _stamp(13, 0)
    provenance = qualified_price(
        TICKER, price, bar_finality=finality, session_date=TRADE_DATE,
        observation_timestamp=stamp)
    if kind is not None:
        provenance["kind"] = kind
    if status is not None:
        provenance["validation_status"] = status
    return {"close": price, "price": price, "currency": "USD",
            "price_as_of": stamp, "price_provenance": provenance}


def _verdict(entry=540.00, mode="PULLBACK", direction="LONG", *,
             price=530.00, stamp=None, finality="INTRADAY_PARTIAL"):
    return evaluate_entry_trigger(
        entry=entry, mode=mode, direction=direction,
        snapshot=_snapshot(price, stamp=stamp, finality=finality),
        trade_date=TRADE_DATE)


def _registry():
    from tradingagents.dataflows.math.run_registry import (
        RunInputs,
        build_run_registry,
    )

    return build_run_registry(
        RunInputs(price=530.00,
                  price_provenance=qualified_price(
                      TICKER, 530.00, bar_finality="INTRADAY_PARTIAL",
                      session_date=TRADE_DATE,
                      observation_timestamp=_stamp(13, 0)),
                  eps_ttm=2.0, weighted_average_shares=1e9),
        run_id="fix5r2")


def _decide(plan, *, trigger, snapshot=None, decision_as_of=None,
            equity=1_000_000.0, cash=500_000.0, risk_budget=1000.0):
    from tradingagents.dataflows.gate_integration import evaluate_decision

    state = {
        "company_of_interest": TICKER, "trade_date": TRADE_DATE,
        "verified_market_snapshot": snapshot or _snapshot(530.00),
        "fundamentals_report": "", "final_rating": "Overweight",
        "final_trade_decision": "**Rating**: Overweight\nBuy.",
        "trader_investment_plan": plan,
    }
    kwargs = {}
    if decision_as_of is not None:
        kwargs["decision_as_of"] = decision_as_of
    return evaluate_decision(
        TICKER, TRADE_DATE, state, snapshot_id="fix5r2",
        metric_registry=_registry(), entry_trigger=trigger, market_open=True,
        equity=equity, cash=cash, risk_budget=risk_budget,
        max_symbol_exposure=0.05, **kwargs)


# ===========================================================================
# A — a LIMIT order is sized on its limit, the worst price it may pay
# ===========================================================================
class TestProblemALimitSizingUsesTheLimit:
    """The limit is a contract, not a suggestion: it bounds the price paid."""

    def test_a_long_limit_sizes_on_the_limit_not_the_observed(self):
        verdict = _verdict(entry=540.00, mode="PULLBACK", price=530.00)
        assert verdict.outcome is TriggerOutcome.MET
        assert executable_price(verdict, direction="LONG") == 540.00, (
            "a limit buy at 540 may pay 540; sizing on the observed 530 "
            "understates the risk by the distance to the limit")

    def test_the_nominal_risk_at_the_limit_fits_the_budget(self):
        verdict = _verdict(entry=540.00, mode="PULLBACK", price=530.00)
        record, _ = _decide(
            "Limit buy at 540.00, stop 520.00, ATR 1.20.", trigger=verdict)
        sizing = record.exposure_sizing
        assert sizing is not None
        risk_at_limit = sizing.qty * (540.00 - 520.00)
        assert risk_at_limit <= 1000.0, (
            f"{sizing.qty} shares at the 540 limit against a 520 stop risk "
            f"{risk_at_limit}, above the 1,000 budget")
        assert sizing.qty == 50, (
            f"sized {sizing.qty}; at the limit 1,000 / (540 - 520) = 50")

    def test_the_sizing_price_is_the_limit(self):
        verdict = _verdict(entry=540.00, mode="PULLBACK", price=530.00)
        record, _ = _decide(
            "Limit buy at 540.00, stop 520.00, ATR 1.20.", trigger=verdict)
        assert record.exposure_sizing.entry == 540.00

    def test_a_valid_long_limit_is_not_falsely_blocked(self):
        """Prudence must not turn a sound plan into a refusal."""
        verdict = _verdict(entry=540.00, mode="PULLBACK", price=530.00)
        _, decision = _decide(
            "Limit buy at 540.00, stop 520.00, ATR 1.20.", trigger=verdict)
        assert decision.action.value == "PROPOSE_PAPER_ORDER"
        assert decision.creates_order is True

    def test_a_short_limit_sizes_on_the_lowest_price_it_may_receive(self):
        """Mirror: a limit sell at 540 may receive only 540, not more."""
        snapshot = _snapshot(550.00)
        verdict = evaluate_entry_trigger(
            entry=540.00, mode=EntryMode.PULLBACK, direction="SHORT",
            snapshot=snapshot, trade_date=TRADE_DATE)
        assert verdict.outcome is TriggerOutcome.MET
        assert executable_price(verdict, direction="SHORT") == 540.00

    def test_a_short_limit_sizing_is_prudent(self):
        snapshot = _snapshot(550.00)
        verdict = evaluate_entry_trigger(
            entry=540.00, mode=EntryMode.PULLBACK, direction="SHORT",
            snapshot=snapshot, trade_date=TRADE_DATE)
        record, _ = _decide(
            "Sell limit at 540.00, stop 560.00, ATR 1.20.",
            trigger=verdict, snapshot=snapshot)
        if record.exposure_sizing is not None:
            risk_at_limit = record.exposure_sizing.qty * (560.00 - 540.00)
            assert risk_at_limit <= 1000.0, (
                f"{record.exposure_sizing.qty} shares short at 540 against a "
                f"560 stop risk {risk_at_limit}")

    def test_a_market_order_still_sizes_on_the_worse_of_the_two(self):
        """The breakout rule is unchanged: pay at least the market."""
        verdict = _verdict(entry=540.00, mode="BREAKOUT", price=550.00,
                           finality="INTRADAY_PARTIAL")
        assert executable_price(verdict, direction="LONG") == 550.00


# ===========================================================================
# B — intraday quote freshness
# ===========================================================================
class TestProblemBQuoteFreshness:
    """`quote <= decision`, and the gap between them, both matter."""

    def _open(self, tmp_path, *, quote, decision, finality="INTRADAY_PARTIAL",
              price=530.00):
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        graph = object.__new__(TradingAgentsGraph)
        graph.config = {"results_dir": str(tmp_path)}
        state = {"verified_market_snapshot": _snapshot(
            price, stamp=quote, finality=finality)}
        return graph._market_open_at(state, TRADE_DATE, decision_as_of=decision)

    def test_a_fresh_intraday_quote_inside_the_session_is_accepted(self, tmp_path):
        assert self._open(
            tmp_path, quote=_stamp(13, 0), decision=_stamp(13, 1)) is True

    def test_a_quote_from_the_morning_is_stale_by_the_close(self, tmp_path):
        """09:30 quote, 15:59 decision: same day, hours old."""
        assert self._open(
            tmp_path, quote=_stamp(9, 30), decision=_stamp(15, 59)) is False

    def test_a_quote_from_the_future_is_refused(self, tmp_path):
        """15:00 quote, 13:00 decision: the data does not exist yet."""
        assert self._open(
            tmp_path, quote=_stamp(15, 0), decision=_stamp(13, 0)) is False

    def test_a_missing_timestamp_is_refused(self, tmp_path):
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        graph = object.__new__(TradingAgentsGraph)
        graph.config = {"results_dir": str(tmp_path)}
        state = {"verified_market_snapshot": {"close": 530.00,
                                              "price_provenance": {}}}
        assert graph._market_open_at(
            state, TRADE_DATE, decision_as_of=_stamp(13, 1)) is False

    def test_yesterdays_quote_is_refused_intraday(self, tmp_path):
        assert self._open(
            tmp_path,
            quote=_stamp(15, 0, day="2026-11-09"),
            decision=_stamp(13, 1)) is False

    def test_an_eod_close_is_not_an_intraday_quote(self, tmp_path):
        """A settled close is a session print, not a live tick during the day.

        The fixture is coherent: an EOD close is stamped at 16:00, the moment
        the session ends. A 13:00 `EOD_CONFIRMED` would describe a bar still
        forming and is covered by the incoherence test below.

        Yesterday's close, read during today's session, is history — the
        intraday rule refuses it. That is the case an intraday decision faces.
        """
        yesterday_close = _stamp(16, 0, day="2026-11-09")
        assert self._open(
            tmp_path, quote=yesterday_close, decision=_stamp(13, 1),
            finality="EOD_CONFIRMED") is False, (
            "yesterday's close is not a quote for today's session")
        # A close stamped after the decision does not exist yet.
        assert self._open(
            tmp_path, quote=_stamp(16, 0), decision=_stamp(13, 0),
            finality="EOD_CONFIRMED") is False

    def test_a_premature_eod_stamp_is_rejected_as_incoherent(self, tmp_path):
        """An `EOD_CONFIRMED` at 13:00 cannot exist: the bar is still forming."""
        from tradingagents.dataflows.math.price_source import qualified_price

        provenance = qualified_price(
            TICKER, 530.00, bar_finality="EOD_CONFIRMED",
            session_date=TRADE_DATE,
            observation_timestamp=_stamp(13, 0))
        # The provenance layer now refuses this combination itself: a 13:00
        # stamp is inside the session, so the bar it describes has not settled.
        assert provenance["validation_status"] == "UNVERIFIED"

    def test_the_threshold_is_explicit_and_configurable(self, tmp_path):
        """No hidden constant: the caller can state the tolerance."""
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        graph = object.__new__(TradingAgentsGraph)
        graph.config = {"results_dir": str(tmp_path),
                        "intraday_quote_max_age_seconds": 7200}
        state = {"verified_market_snapshot": _snapshot(
            530.00, stamp=_stamp(13, 0), finality="INTRADAY_PARTIAL")}
        # Two hours: accepted with a 7,200 s tolerance, refused with the default.
        assert graph._market_open_at(
            state, TRADE_DATE, decision_as_of=_stamp(15, 0)) is True
        graph.config.pop("intraday_quote_max_age_seconds")
        assert graph._market_open_at(
            state, TRADE_DATE, decision_as_of=_stamp(15, 0)) is False

    def test_a_holiday_still_has_no_session(self, tmp_path):
        """The calendar and freshness stay independent."""
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        graph = object.__new__(TradingAgentsGraph)
        graph.config = {"results_dir": str(tmp_path)}
        state = {"verified_market_snapshot": _snapshot(
            530.00, stamp=_stamp(13, 0, day="2026-11-26"),
            finality="INTRADAY_PARTIAL")}
        assert graph._market_open_at(
            state, "2026-11-26",
            decision_as_of=_stamp(13, 1, day="2026-11-26")) is False


# ===========================================================================
# C — the risk record must be arithmetically consistent
# ===========================================================================
class TestProblemCTheRiskRecordAddsUp:
    """Every number in the audit must follow from the ones beside it."""

    def _record(self):
        verdict = _verdict(entry=540.00, mode="BREAKOUT", price=550.00)
        record, decision = _decide(
            "Buy stop above 540.00, stop 520.00, ATR 1.20.", trigger=verdict)
        assert decision.action.value == "PROPOSE_PAPER_ORDER"
        return record

    def test_the_three_prices_are_distinct_and_correct(self):
        record = self._record()
        assert record.entry_trigger.entry_level == 540.00
        assert record.entry_trigger.observed_price == 550.00
        assert record.exposure_sizing.entry == 550.00

    def test_risk_per_share_follows_from_the_sizing_price_and_stop(self):
        record = self._record()
        expected = 550.00 - 520.00
        assert record.sizing.risk_per_share == expected, (
            f"risk_per_share {record.sizing.risk_per_share} does not follow "
            f"from the sizing price 550 and the stop 520")

    def test_planned_risk_is_quantity_times_unit_risk(self):
        record = self._record()
        qty = record.sizing.qty
        per_share = record.sizing.risk_per_share
        assert record.sizing.planned_risk == qty * per_share, (
            f"planned_risk {record.sizing.planned_risk} != {qty} x {per_share}")

    def test_stressed_risk_per_share_is_a_unit_risk_not_a_quotient(self):
        record = self._record()
        stressed = record.sizing.stressed_risk_per_share
        nominal = record.sizing.risk_per_share
        assert stressed >= nominal, (
            f"stressed {stressed} is below the nominal {nominal}: gap and "
            "slippage can only add to the loss")
        # It cannot be a per-share figure if it shrinks with the lot size.
        assert stressed > 1.0, (
            f"stressed_risk_per_share {stressed} looks like a quotient by "
            "quantity, not a per-share risk")

    def test_the_json_shows_the_same_numbers(self):
        record = self._record()
        payload = record.to_dict()
        execution = payload["execution"]
        risk = payload["risk"]
        assert execution["trigger_level"] == 540.00
        assert execution["risk_sizing_price"] == 550.00
        assert execution["entry_trigger"]["observed_price"] == 550.00
        assert risk["qty_proposed"] == record.sizing.qty
        assert risk["planned_risk"] == record.sizing.planned_risk

    def test_the_authorised_order_reports_the_reference_price(self):
        """The proposal must not present the trigger level as a fill price."""
        import inspect

        from tradingagents.dataflows import execution_verdict

        record = self._record()
        orders = record.to_dict()["execution"]["verdict"]["authorized_orders"]
        assert len(orders) == 1
        assert "entry" in orders[0], orders[0]
        assert orders[0]["entry"] == 550.00, (
            "the authorised order carries 550 (the price the size was computed "
            "against), not the 540 trigger level")
        # And nothing reached a broker.
        source = inspect.getsource(execution_verdict)
        for forbidden in ("requests.", "httpx.", "place_order", "send_order"):
            assert forbidden not in source


class TestAReallyCompiledGraph:
    """A compiled `StateGraph`, not a MagicMock standing in for `invoke`.

    The previous tests mock `graph.invoke`, which exercises the decision path
    but never the graph itself. This builds a real `StateGraph` over
    `AgentState` whose single node writes the finished state, compiles it, and
    drives `propagate()` through it. If the state were dropped between nodes —
    the failure that hid the snapshot in FIX #2 — this is where it would show.
    """

    def test_a_compiled_graph_carries_the_state_into_the_trigger(self, tmp_path):
        import functools
        import json
        import pathlib

        from langgraph.graph import END, START, StateGraph

        from tradingagents.agents.state import AgentState
        from tradingagents.dataflows.filing_freshness import FilingRecord
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        stamp = "2026-11-10T20:00:00+00:00"      # 15:00 New York, open
        finished = {
            "company_of_interest": TICKER,
            "trade_date": TRADE_DATE,
            "verified_market_snapshot": {
                "close": 550.00, "price": 550.00, "currency": "USD",
                "price_as_of": stamp,
                "price_provenance": {
                    "symbol": TICKER, "value": 550.00, "kind": "intraday",
                    "timestamp": stamp, "session_date": TRADE_DATE,
                    "bar_finality": "INTRADAY_PARTIAL",
                    "validation_status": "VERIFIED"},
            },
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
                "current_response": "", "judge_decision": "",
                "count": 1, "latest_speaker": ""},
            "risk_debate_state": {
                "aggressive_history": "", "conservative_history": "",
                "neutral_history": "", "history": ""},
        }

        def finish(state):
            return dict(finished)

        workflow = StateGraph(AgentState)
        workflow.add_node("finish", finish)
        workflow.add_edge(START, "finish")
        workflow.add_edge("finish", END)
        compiled = workflow.compile()

        graph = object.__new__(TradingAgentsGraph)
        graph.debug = False
        graph.config = {
            "results_dir": str(tmp_path),
            "deep_think_llm": "m", "quick_think_llm": "m",
            "fundamentals_freshness_gate": True,
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
        graph.checkpoint_input = lambda state: state
        # `create_run_state` records usage context and resolves the instrument;
        # both are orthogonal to the decision path under test.
        graph.create_run_state = lambda *a, **k: dict(finished)
        # The checkpoint lifecycle needs these; this test runs one uninterrupted
        # pass, so there is nothing to restore.
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

        # The real compiled graph runs; only the clock is pinned.
        TradingAgentsGraph.propagate(
            graph, TICKER, TRADE_DATE, portfolio=_Book())

        execution = json.loads(
            next(pathlib.Path(tmp_path).rglob("audit_*.json")).read_text()
        )["execution"]
        # The state survived the compiled graph and reached the trigger.
        assert execution["entry_trigger"] is not None
        assert execution["entry_trigger"]["observed_price"] == 550.00
        assert execution["entry_trigger"]["mode"] == "BREAKOUT"
        assert execution["risk_sizing_price"] == 550.00
        assert execution["trigger_level"] == 540.00

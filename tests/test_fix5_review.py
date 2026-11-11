"""FIX #5 review — three safety gaps in the production path.

A. A met trigger with an incomplete risk plan could still propose.
B. Sizing used the plan's entry level, not the price the order would fill at.
C. The session was checked at the quote's timestamp, not at the decision's.

Each is a way to authorize an order the risk engine did not actually validate.
The tests are grouped by defect so a failure names the gap.
"""

from __future__ import annotations

from tradingagents.dataflows.math.entry_trigger import evaluate_entry_trigger
from tradingagents.dataflows.math.price_source import qualified_price

TICKER = "TEST"
TRADE_DATE = "2026-11-10"
# 16:00 New York, the close: an EOD_CONFIRMED bar cannot be stamped
# earlier without describing a bar still forming.
OPEN_STAMP = f"{TRADE_DATE}T21:00:00+00:00"      # 16:00 New York


def _snapshot(price=550.00, *, stamp=OPEN_STAMP, kind="eod_close",
              status="VERIFIED"):
    return {
        "close": price, "price": price, "currency": "USD",
        "price_as_of": stamp,
        "price_provenance": {
            "symbol": TICKER, "value": price, "currency": "USD",
            "share_class": "B", "kind": kind, "timestamp": stamp,
            "session_date": TRADE_DATE, "source": "yfinance",
            "bar_finality": "EOD_CONFIRMED", "validation_status": status,
        },
    }


def _intraday_snapshot(price=550.00, *, stamp="2026-11-10T18:00:00+00:00"):
    """A live print: an intraday kind cannot be stamped as a settled close."""
    return {
        "close": price, "price": price, "currency": "USD",
        "price_as_of": stamp,
        "price_provenance": {
            "symbol": TICKER, "value": price, "currency": "USD",
            "share_class": "B", "kind": "intraday", "timestamp": stamp,
            "session_date": TRADE_DATE, "source": "yfinance",
            "bar_finality": "INTRADAY_PARTIAL", "validation_status": "VERIFIED",
        },
    }


def _verdict(entry=540.00, mode="BREAKOUT", direction="LONG", snapshot=None):
    return evaluate_entry_trigger(
        entry=entry, mode=mode, direction=direction,
        snapshot=snapshot if snapshot is not None else _snapshot(),
        trade_date=TRADE_DATE)


def _registry():
    from tradingagents.dataflows.math.run_registry import (
        RunInputs,
        build_run_registry,
    )

    return build_run_registry(
        RunInputs(price=550.00,
                  price_provenance=qualified_price(
                      TICKER, 550.00, bar_finality="EOD_CONFIRMED",
                      session_date=TRADE_DATE),
                  eps_ttm=2.0, weighted_average_shares=1e9),
        run_id="fix5r")


def _decide(plan, *, trigger, snapshot=None, equity=1_000_000.0,
            cash=500_000.0, risk_budget=1000.0, market_open=True,
            decision_as_of=None):
    from tradingagents.dataflows.gate_integration import evaluate_decision

    state = {
        "company_of_interest": TICKER, "trade_date": TRADE_DATE,
        "verified_market_snapshot": snapshot or _snapshot(),
        "fundamentals_report": "", "final_rating": "Overweight",
        "final_trade_decision": "**Rating**: Overweight\nBuy.",
        "trader_investment_plan": plan,
    }
    kwargs = {}
    if decision_as_of is not None:
        kwargs["decision_as_of"] = decision_as_of
    return evaluate_decision(
        TICKER, TRADE_DATE, state, snapshot_id="fix5r",
        metric_registry=_registry(), entry_trigger=trigger,
        market_open=market_open, equity=equity, cash=cash,
        risk_budget=risk_budget, max_symbol_exposure=0.05, **kwargs)


# ===========================================================================
# A — a met trigger is not a complete risk plan
# ===========================================================================
class TestProblemAAnIncompleteRiskPlanCannotPropose:
    """Trading needs a size. A size needs entry, stop and ATR."""

    def test_a_met_trigger_with_no_stop_does_not_propose(self):
        verdict = _verdict()
        assert verdict.met is True
        _, decision = _decide("Buy stop above 540.00, ATR 1.20.", trigger=verdict)
        assert decision.action.value != "PROPOSE_PAPER_ORDER"
        assert decision.creates_order is False
        codes = [r.value for r in decision.reasons]
        assert "RISK_PLAN_INCOMPLETE" in codes, codes

    def test_a_met_trigger_with_no_atr_does_not_propose(self):
        verdict = _verdict()
        _, decision = _decide("Buy stop above 540.00, stop 520.00.", trigger=verdict)
        assert decision.creates_order is False
        codes = [r.value for r in decision.reasons]
        assert "RISK_PLAN_INCOMPLETE" in codes, codes

    def test_a_met_trigger_with_no_entry_does_not_propose(self):
        verdict = _verdict()
        _, decision = _decide("Stop 520.00, ATR 1.20.", trigger=verdict)
        assert decision.creates_order is False
        codes = [r.value for r in decision.reasons]
        assert "RISK_PLAN_INCOMPLETE" in codes, codes

    def test_no_quantity_is_authorised_without_a_validated_size(self):
        verdict = _verdict()
        record, decision = _decide(
            "Buy stop above 540.00, ATR 1.20.", trigger=verdict)
        assert decision.creates_order is False
        assert record.to_dict()["execution"]["verdict"]["authorized_orders"] == []

    def test_a_complete_plan_still_proposes(self):
        """The guard must not block a plan that does carry its numbers."""
        verdict = _verdict()
        _, decision = _decide(
            "Buy stop above 540.00, stop 520.00, ATR 1.20.", trigger=verdict)
        assert decision.action.value == "PROPOSE_PAPER_ORDER"

    def test_an_unmet_trigger_still_waits_rather_than_blocking(self):
        """A gap in the risk plan is a BLOCK; an early plan is still a WAIT."""
        verdict = _verdict(entry=560.00)
        _, decision = _decide("Buy stop above 560.00, ATR 1.20.", trigger=verdict)
        assert decision.creates_order is False


# ===========================================================================
# B — sizing must use a price the order can realistically fill at
# ===========================================================================
class TestProblemBSizingUsesAnExecutablePrice:
    """The trigger level and the fill price are different numbers.

    Sizing on the level when the market is already past it understates the
    risk: 50 shares sized at 540 with a 520 stop risk 1,000, but the same 50
    shares bought at 550 risk 1,500 before gap and slippage.
    """

    def test_a_breakout_sizes_on_the_observed_price_not_the_trigger(self):
        verdict = _verdict(entry=540.00, mode="BREAKOUT")
        assert verdict.observed_price == 550.00
        record, _ = _decide(
            "Buy stop above 540.00, stop 520.00, ATR 1.20.", trigger=verdict)

        sizing = record.exposure_sizing
        assert sizing is not None
        # At the observed price, 1,000 / (550 - 520) = 33 shares, not 50.
        assert sizing.qty == 33, (
            f"sized {sizing.qty} shares; at 550 a 520 stop means the risk of "
            "50 shares is 1,500 against a 1,000 budget")

    def test_the_reported_risk_does_not_exceed_the_budget(self):
        verdict = _verdict(entry=540.00, mode="BREAKOUT")
        record, _ = _decide(
            "Buy stop above 540.00, stop 520.00, ATR 1.20.", trigger=verdict)
        sizing = record.exposure_sizing
        nominal_at_observed = sizing.qty * (550.00 - 520.00)
        assert nominal_at_observed <= 1000.0, (
            f"{sizing.qty} shares at 550 against a 520 stop risk "
            f"{nominal_at_observed}, above the 1,000 budget")

    def test_the_audit_keeps_the_trigger_level_and_the_sizing_price(self):
        verdict = _verdict(entry=540.00, mode="BREAKOUT")
        record, _ = _decide(
            "Buy stop above 540.00, stop 520.00, ATR 1.20.", trigger=verdict)
        sizing_entry = record.exposure_sizing.entry
        assert sizing_entry == 550.00, (
            "the sizing price must be the observed one, recorded separately "
            "from the trigger level")
        assert record.entry_trigger.entry_level == 540.00
        assert record.entry_trigger.observed_price == 550.00

    def test_a_breakdown_short_sizes_on_the_observed_price(self):
        """Mirror of the LONG case: a breakdown fills below the level."""
        snapshot = _snapshot(price=530.00)
        verdict = _verdict(entry=540.00, mode="BREAKOUT",
                           direction="SHORT", snapshot=snapshot)
        assert verdict.met is True
        record, decision = _decide(
            "Sell stop below 540.00, stop 560.00, ATR 1.20.",
            trigger=verdict, snapshot=snapshot)
        # Sized at 530, not 540: 1,000 / 30 = 33, not 1,000 / 20 = 50.
        if decision.action.value == "PROPOSE_PAPER_ORDER":
            assert record.exposure_sizing.qty == 33

    def test_a_limit_buy_is_sized_on_its_limit(self):
        """The limit is the worst price the contract allows, so it bounds the risk.

        Superseded by the final review: sizing on the observed 530 authorized
        94 shares, whose risk at the 540 limit is 1,880 against a 1,000 budget.
        The limit is used for both directions.
        """
        snapshot = _snapshot(price=530.00)
        verdict = _verdict(entry=540.00, mode="PULLBACK", snapshot=snapshot)
        assert verdict.met is True
        record, decision = _decide(
            "Limit buy at 540.00, stop 520.00, ATR 1.20.",
            trigger=verdict, snapshot=snapshot)
        if decision.action.value == "PROPOSE_PAPER_ORDER":
            assert record.exposure_sizing.entry == 540.00

    def test_the_sizing_price_is_never_better_than_the_market(self):
        """A LONG may not be sized on a price below what it would pay.

        Observed 550, trigger 540: using 540 for a market order would be a
        price the buyer cannot get.
        """
        verdict = _verdict(entry=540.00, mode="BREAKOUT")
        record, _ = _decide(
            "Buy stop above 540.00, stop 520.00, ATR 1.20.", trigger=verdict)
        assert record.exposure_sizing.entry >= 550.00


# ===========================================================================
# C — the session must be checked when the decision is made
# ===========================================================================
class TestProblemCTheDecisionMomentDecides:
    """A quote from an open session does not make the decision timely.

    `_market_open_at` reads the quote's stamp. At 16:05 New York the market is
    closed even though the 13:00 quote was taken while it was open.
    """

    def test_a_quote_from_the_morning_does_not_open_the_afternoon(self):
        from datetime import UTC, datetime

        from tradingagents.dataflows.math.session import (
            SessionStatus,
            market_session_status,
        )

        quote = datetime(2026, 11, 10, 18, 0, tzinfo=UTC)    # 13:00 NY
        decision = datetime(2026, 11, 10, 21, 5, tzinfo=UTC)  # 16:05 NY
        assert market_session_status(quote) is SessionStatus.OPEN
        assert market_session_status(decision) is not SessionStatus.OPEN

    def test_the_graph_checks_the_decision_moment(self, tmp_path):
        """The graph must consult the decision clock, not only the quote."""
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        graph = object.__new__(TradingAgentsGraph)
        graph.config = {"results_dir": str(tmp_path)}
        state = {"verified_market_snapshot": _snapshot()}
        # 16:05 New York on a trading day: the market has closed.
        assert graph._market_open_at(
            state, TRADE_DATE,
            decision_as_of="2026-11-10T21:05:00+00:00") is False

    def test_the_graph_accepts_a_decision_inside_the_session(self, tmp_path):
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        graph = object.__new__(TradingAgentsGraph)
        graph.config = {"results_dir": str(tmp_path)}
        state = {"verified_market_snapshot": _snapshot()}
        # The quote is stamped 16:00 NY; a decision a minute later is inside
        # the session and inside the freshness window.
        assert graph._market_open_at(
            state, TRADE_DATE,
            decision_as_of="2026-11-10T21:01:00+00:00") is False, (
            "16:01 New York is after the close")
        # A decision while the session is still running needs a quote from
        # the same session, which the fixture's 16:00 close is not.
        inside = {"verified_market_snapshot": _intraday_snapshot()}
        assert graph._market_open_at(
            inside, TRADE_DATE,
            decision_as_of="2026-11-10T18:01:00+00:00") is True

    def test_a_holiday_has_no_session(self):
        from tradingagents.dataflows.math.session import (
            SessionStatus,
            market_session_status,
        )

        # Thanksgiving 2026-11-26, 13:00 New York.
        assert market_session_status(
            "2026-11-26T18:00:00+00:00") is not SessionStatus.OPEN

    def test_an_early_close_ends_at_thirteen(self):
        from tradingagents.dataflows.math.session import (
            SessionStatus,
            market_session_status,
        )

        # 2026-11-27 closes at 13:00 NY. 18:00 UTC is exactly 13:00 NY.
        assert market_session_status(
            "2026-11-27T19:00:00+00:00") is not SessionStatus.OPEN   # 14:00 NY

    def test_an_uncovered_year_does_not_propose(self, tmp_path):
        """A year the calendar does not describe cannot be certified open."""
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        graph = object.__new__(TradingAgentsGraph)
        graph.config = {"results_dir": str(tmp_path)}
        state = {"verified_market_snapshot": _snapshot(
            stamp="2030-06-10T18:00:00+00:00")}
        assert graph._market_open_at(
            state, "2030-06-10",
            decision_as_of="2030-06-10T18:00:00+00:00") is False

    def test_a_stale_quote_does_not_propose(self, tmp_path):
        """Freshness is a separate question from the session."""
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        graph = object.__new__(TradingAgentsGraph)
        graph.config = {"results_dir": str(tmp_path)}
        stale = _snapshot(stamp="2026-11-07T18:00:00+00:00")   # three days old
        assert graph._market_open_at(
            {"verified_market_snapshot": stale}, TRADE_DATE,
            decision_as_of="2026-11-10T18:00:00+00:00") is False


class TestThePropagateEntryPoint:
    """The `propagate` entry point, with the graph invocation mocked.

    This is **not** a test of the compiled graph. `graph.invoke` is a
    `MagicMock`, so what runs is the decision path around it: initial state →
    record_decision → trigger → gate → audit. That is the wiring the original
    defect hid in, and it is worth testing — but a genuine `StateGraph` run is
    a different question, covered in `test_fix5_final_review.py`
    (`TestAReallyCompiledGraph`). Naming this one "compiled" would overstate it.
    """

    STAMP = "2026-11-10T18:00:00+00:00"

    def _graph(self, tmp_path, *, plan, price=550.00):
        import functools
        from unittest.mock import MagicMock

        from tradingagents.dataflows.filing_freshness import FilingRecord
        from tradingagents.dataflows.math.price_source import qualified_price
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        snapshot = {
            "close": price, "price": price, "currency": "USD",
            "price_as_of": self.STAMP,
            "price_provenance": qualified_price(
                TICKER, price, bar_finality="EOD_CONFIRMED",
                session_date=TRADE_DATE, observation_timestamp=self.STAMP),
        }
        final_state = {
            "company_of_interest": TICKER, "trade_date": TRADE_DATE,
            "verified_market_snapshot": snapshot,
            "trader_investment_plan": plan,
            "final_trade_decision": "**Rating**: Overweight\nBuy.",
            "final_rating": "Overweight",
            "fundamentals_report": "Reviewed Q3 2026 results.",
            "market_report": "", "news_report": "", "sentiment_report": "",
            "eps_ttm": 2.0, "weighted_average_shares": 1e9,
            "investment_plan": "Buy on confirmation.",
            "investment_debate_state": {
                "bull_history": "", "bear_history": "", "history": "",
                "current_response": "", "judge_decision": "",
                "count": 1, "latest_speaker": ""},
            "risk_debate_state": {
                "aggressive_history": "", "conservative_history": "",
                "neutral_history": "", "history": ""},
        }

        graph = MagicMock()
        graph.debug = False
        graph.config = {
            "results_dir": str(tmp_path),
            "deep_think_llm": "m", "quick_think_llm": "m",
            "fundamentals_freshness_gate": True,
            "risk_per_trade_pct": 0.01, "max_symbol_exposure": 0.05,
        }
        graph.graph.invoke.return_value = final_state
        graph.propagator.create_initial_state.return_value = final_state
        graph.propagator.get_graph_args.return_value = {}

        class _Log:
            def store_decision(self, **kwargs):
                pass

            def load_entries(self):
                return []

        graph.memory_log = _Log()
        governed_filings = [
            FilingRecord(period="2026-Q3", period_end="2026-09-30",
                         filing_date="2026-11-09", form_type="10-Q",
                         accession_number="0001067983-26-000042")]

        class _Book:
            equity = 1_000_000.0
            cash = 500_000.0

            def model_dump(self):
                return {"equity": self.equity, "cash": self.cash}

        graph.portfolio = _Book()
        graph.run_settings = lambda: {"version": "test", "deep": "m",
                                      "quick": "m"}

        for name in ("_run_graph", "record_decision", "_evaluate_reliability",
                     "_evaluate_entry_trigger", "_market_open_at",
                     "_complete_run_registry",
                     "_write_run_record", "_log_state"):
            real = getattr(TradingAgentsGraph, name, None)
            if real is not None:
                setattr(graph, name, functools.partial(real, graph))
        # Installed last so the binding loop above cannot replace it.
        graph._known_filings = lambda *a, **k: governed_filings
        return graph

    def _execution(self, tmp_path):
        import json
        import pathlib

        return json.loads(
            next(pathlib.Path(tmp_path).rglob("audit_*.json")).read_text()
        )["execution"]

    def test_propagate_proposes_when_a_verified_breakout_is_met(self, tmp_path,
                                                               monkeypatch):
        """T1 through the real entry point, clock frozen."""
        from tradingagents.graph import trading_graph as tg

        monkeypatch.setattr(
            tg.TradingAgentsGraph, "_market_open_at",
            lambda self, state, date, decision_as_of=None: True)

        graph = self._graph(
            tmp_path, plan="Buy stop above 540.00, stop 520.00, ATR 1.20.")
        tg.TradingAgentsGraph.propagate(graph, TICKER, TRADE_DATE,
                                        portfolio=graph.portfolio)
        execution = self._execution(tmp_path)
        assert execution["action"] == "PROPOSE_PAPER_ORDER"
        trigger = execution["entry_trigger"]
        assert trigger["met"] is True
        assert trigger["mode"] == "BREAKOUT"
        # Sized on the observed price, not on the trigger level.
        assert execution["risk_sizing_price"] == 550.00
        assert execution["trigger_level"] == 540.00

    def test_propagate_never_proposes_an_incomplete_risk_plan(self, tmp_path,
                                                              monkeypatch):
        """Problem A through the real entry point."""
        from tradingagents.graph import trading_graph as tg

        monkeypatch.setattr(
            tg.TradingAgentsGraph, "_market_open_at",
            lambda self, state, date, decision_as_of=None: True)

        graph = self._graph(tmp_path, plan="Buy stop above 540.00")
        tg.TradingAgentsGraph.propagate(graph, TICKER, TRADE_DATE,
                                        portfolio=graph.portfolio)
        execution = self._execution(tmp_path)
        assert execution["action"] != "PROPOSE_PAPER_ORDER"
        assert "RISK_PLAN_INCOMPLETE" in execution["reason_codes"]

    def test_propagate_sends_no_order(self, tmp_path, monkeypatch):
        """No broker call anywhere in the decision path."""
        from tradingagents.graph import trading_graph as tg

        monkeypatch.setattr(
            tg.TradingAgentsGraph, "_market_open_at",
            lambda self, state, date, decision_as_of=None: True)

        graph = self._graph(
            tmp_path, plan="Buy stop above 540.00, stop 520.00, ATR 1.20.")
        tg.TradingAgentsGraph.propagate(graph, TICKER, TRADE_DATE,
                                        portfolio=graph.portfolio)
        execution = self._execution(tmp_path)
        assert execution["market_order_allowed"] is False
        assert execution["paper_order_allowed_now"] is True
        # `authorized_orders` is a plan description; nothing was transmitted.
        assert isinstance(execution["verdict"]["authorized_orders"], list)

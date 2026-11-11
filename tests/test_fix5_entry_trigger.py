"""FIX #5 — the production path must be able to evaluate an entry trigger.

The Execution Gate has always taken `trigger_met` as a caller's boolean, and the
graph never supplied one. The default `False` meant `PROPOSE_PAPER_ORDER` was
unreachable from production no matter how good the data: every run ended WAIT.

These tests exercise the two halves that were missing — a deterministic
evaluator, and the graph calling it — and they deliberately drive the graph's
own methods rather than `evaluate_decision()` directly, because that shortcut is
what hid the defect from 50 existing tests.

Every figure is a frozen constant. No network, no broker, no live price.
"""

from __future__ import annotations

import pytest

from tradingagents.dataflows.math.entry_trigger import (
    TriggerOutcome,
    evaluate_entry_trigger,
)

TICKER = "TEST"
TRADE_DATE = "2026-11-10"
SESSION_STAMP = f"{TRADE_DATE}T18:00:00+00:00"      # 13:00 New York, session open


def _snapshot(price=550.00, *, kind="market_price", status="VERIFIED",
              stamp=SESSION_STAMP, finality="EOD_CONFIRMED"):
    """A verified snapshot in the shape the Data Quality node writes."""
    return {
        "close": price,
        "price": price,
        "currency": "USD",
        "price_as_of": stamp,
        "price_provenance": {
            "symbol": TICKER,
            "value": price,
            "currency": "USD",
            "share_class": "B",
            "kind": kind,
            "timestamp": stamp,
            "session_date": TRADE_DATE,
            "source": "yfinance",
            "bar_finality": finality,
            "validation_status": status,
        },
    }


def _evaluate(*, entry=540.00, mode="BREAKOUT", direction="LONG",
              snapshot=None, trade_date=TRADE_DATE, valid_until=None):
    return evaluate_entry_trigger(
        entry=entry, mode=mode, direction=direction,
        snapshot=snapshot if snapshot is not None else _snapshot(),
        trade_date=trade_date, valid_until=valid_until,
    )


class TestTheEvaluatorDecidesTheComparison:
    """T1-T4: the four shapes a plan can have, decided without guessing."""

    def test_t1_a_breakout_long_above_the_level_is_met(self):
        """Price 550 against entry 540: a breakout LONG has cleared it."""
        verdict = _evaluate(entry=540.00, mode="BREAKOUT", direction="LONG")
        assert verdict.outcome is TriggerOutcome.MET
        assert verdict.met is True
        assert verdict.observed_price == 550.00
        assert ">=" in verdict.reason

    def test_t2_a_limit_buy_below_the_level_is_not_met(self):
        """Price 550 against a 540 limit buy: you do not pay more than the limit."""
        verdict = _evaluate(entry=540.00, mode="PULLBACK", direction="LONG")
        assert verdict.outcome is TriggerOutcome.NOT_MET
        assert verdict.met is False

    def test_t2b_a_limit_buy_at_or_below_the_limit_is_met(self):
        verdict = _evaluate(entry=560.00, mode="PULLBACK", direction="LONG")
        assert verdict.outcome is TriggerOutcome.MET

    def test_t3_a_breakout_not_yet_reached_is_wait(self):
        verdict = _evaluate(entry=560.00, mode="BREAKOUT", direction="LONG")
        assert verdict.outcome is TriggerOutcome.NOT_MET
        assert verdict.met is False

    def test_t4_an_unstated_mode_is_ambiguous_not_a_guess(self):
        """`Entry 540.00` alone says nothing about the comparison."""
        for mode in (None, "AMBIGUOUS"):
            verdict = _evaluate(entry=540.00, mode=mode)
            assert verdict.outcome is TriggerOutcome.AMBIGUOUS
            assert verdict.met is False
            assert "not stated" in verdict.reason

    def test_a_short_breakout_inverts_the_comparison(self):
        """A breakdown SHORT wants price <= entry."""
        verdict = _evaluate(entry=560.00, mode="BREAKOUT", direction="SHORT")
        assert verdict.outcome is TriggerOutcome.MET
        verdict_far = _evaluate(entry=540.00, mode="BREAKOUT", direction="SHORT")
        assert verdict_far.outcome is TriggerOutcome.NOT_MET

    def test_an_expired_condition_does_not_propose(self):
        verdict = _evaluate(valid_until="2026-11-01")
        assert verdict.outcome is TriggerOutcome.EXPIRED
        assert verdict.met is False


class TestTheEvaluatorRefusesUnsoundInputs:
    """T5-T7: a trigger may only rest on a verified, current market price."""

    def test_t5_no_price_in_the_snapshot(self):
        verdict = _evaluate(snapshot={})
        assert verdict.outcome is TriggerOutcome.NOT_EVALUABLE
        assert verdict.met is False

    def test_t5b_a_snapshot_whose_price_is_none(self):
        verdict = _evaluate(snapshot={"close": None})
        assert verdict.outcome is TriggerOutcome.NOT_EVALUABLE

    def test_t6_an_indicator_is_not_a_price(self):
        """The SMA50 cannot open a position, exactly as it cannot price a P/B."""
        from tradingagents.dataflows.math.price_source import indicator_as_price

        snapshot = {"close": 545.10, "price_provenance": indicator_as_price(
            "sma50", 545.10, TICKER)}
        verdict = _evaluate(entry=540.00, snapshot=snapshot)
        assert verdict.outcome is TriggerOutcome.NOT_EVALUABLE
        assert verdict.met is False
        assert "not a market price" in verdict.reason

    def test_t6b_an_unverified_price_does_not_trigger(self):
        verdict = _evaluate(snapshot=_snapshot(status="UNVERIFIED"))
        assert verdict.outcome is TriggerOutcome.NOT_EVALUABLE
        assert "UNVERIFIED" in verdict.reason

    def test_t7_a_stale_snapshot_is_not_a_current_quote(self):
        stale = _snapshot(stamp="2026-11-07T18:00:00+00:00")
        verdict = _evaluate(entry=540.00, snapshot=stale)
        assert verdict.outcome is TriggerOutcome.NOT_EVALUABLE
        assert "stale" in verdict.reason.lower() or "2026-11-07" in verdict.reason

    def test_t7b_an_intraday_price_without_a_timestamp_is_refused(self):
        """An EOD close is dated by its session; an intraday print needs the instant.

        This is FIX #2's rule applied to the trigger: `qualified_price` already
        marks an intraday price without a timestamp `UNVERIFIED`, and the
        evaluator refuses anything not VERIFIED.
        """
        from tradingagents.dataflows.math.price_source import qualified_price

        provenance = qualified_price("TEST", 550.00,
                                     bar_finality="INTRADAY_PARTIAL",
                                     session_date=TRADE_DATE)
        assert provenance["validation_status"] == "UNVERIFIED"
        snapshot = {"close": 550.00, "price_as_of": None,
                    "price_provenance": provenance}
        verdict = _evaluate(entry=540.00, snapshot=snapshot)
        assert verdict.outcome is TriggerOutcome.NOT_EVALUABLE

    def test_t7c_a_price_with_neither_timestamp_nor_session_is_refused(self):
        snapshot = {"close": 550.00, "price_provenance": {
            "value": 550.00, "kind": "market_price",
            "validation_status": "VERIFIED"}}
        verdict = _evaluate(entry=540.00, snapshot=snapshot)
        assert verdict.outcome is TriggerOutcome.NOT_EVALUABLE
        assert "timestamp" in verdict.reason

    def test_no_entry_level_is_not_evaluable(self):
        verdict = _evaluate(entry=None)
        assert verdict.outcome is TriggerOutcome.NOT_EVALUABLE

    def test_a_provenance_without_its_own_value_falls_back_to_the_snapshot(self):
        """`price_from_snapshot` passes a supplied provenance through unchanged.

        A provenance dict that carries the kind and the stamp but not the
        figure is still usable when the snapshot holds the close. Assuming
        `provenance["value"]` existed raised KeyError inside the evaluator.
        """
        snapshot = {
            "close": 550.00,
            "price_provenance": {
                "kind": "market_price", "validation_status": "VERIFIED",
                "timestamp": SESSION_STAMP, "session_date": TRADE_DATE},
        }
        verdict = _evaluate(entry=540.00, mode="BREAKOUT", snapshot=snapshot)
        assert verdict.outcome is TriggerOutcome.MET
        assert verdict.observed_price == 550.00

    def test_a_non_numeric_price_is_not_evaluable(self):
        snapshot = {"close": "n/a",
                    "price_provenance": {
                        "kind": "market_price",
                        "validation_status": "VERIFIED",
                        "timestamp": SESSION_STAMP}}
        verdict = _evaluate(entry=540.00, snapshot=snapshot)
        assert verdict.outcome is TriggerOutcome.NOT_EVALUABLE


class TestThePlanTextYieldsATypedMode:
    """The mode is extracted, never inferred from a lone number."""

    @pytest.mark.parametrize("text,expected", [
        ("Entry 540.00, stop 520.00", None),
        ("Buy stop above 540.00, stop 520.00", "BREAKOUT"),
        ("Breakout above 540.00", "BREAKOUT"),
        ("Limit buy at 540.00, stop 520.00", "PULLBACK"),
        ("Buy the dip below 540.00", "PULLBACK"),
        ("Breakout above 540 and limit buy below 530", "AMBIGUOUS"),
    ])
    def test_extraction(self, text, expected):
        from tradingagents.dataflows.plan_extraction import extract_plan_numbers

        assert extract_plan_numbers(text).entry_mode == expected


class TestTheGraphEvaluatesIt:
    """T13: through the graph's own methods, not `evaluate_decision` directly."""

    def _graph(self, tmp_path):
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        graph = object.__new__(TradingAgentsGraph)
        graph.config = {
            "results_dir": str(tmp_path),
            "deep_think_llm": "m", "quick_think_llm": "m",
            "fundamentals_freshness_gate": False,
            "risk_per_trade_pct": 0.01, "max_symbol_exposure": 0.05,
        }
        graph.selected_analysts = []
        return graph

    def _state(self, plan, snapshot=None):
        return {
            "company_of_interest": TICKER,
            "trader_investment_plan": plan,
            "verified_market_snapshot": snapshot or _snapshot(),
        }

    def test_the_graph_computes_a_met_verdict(self, tmp_path):
        graph = self._graph(tmp_path)
        verdict = graph._evaluate_entry_trigger(
            self._state("Buy stop above 540.00, stop 520.00"), TRADE_DATE)
        assert verdict is not None
        assert verdict.outcome is TriggerOutcome.MET
        assert verdict.met is True

    def test_the_graph_computes_an_unmet_verdict(self, tmp_path):
        graph = self._graph(tmp_path)
        verdict = graph._evaluate_entry_trigger(
            self._state("Buy stop above 560.00, stop 520.00"), TRADE_DATE)
        assert verdict.outcome is TriggerOutcome.NOT_MET

    def test_the_graph_refuses_an_ambiguous_plan(self, tmp_path):
        graph = self._graph(tmp_path)
        verdict = graph._evaluate_entry_trigger(
            self._state("Entry 540.00, stop 520.00"), TRADE_DATE)
        assert verdict.outcome is TriggerOutcome.AMBIGUOUS
        assert verdict.met is False

    def test_the_market_state_is_derived_not_assumed(self, tmp_path):
        """A stamp outside the session is not an open market."""
        graph = self._graph(tmp_path)
        night = _snapshot(stamp=f"{TRADE_DATE}T02:00:00+00:00")   # 21:00 NY prev day
        assert graph._market_open_at(
            {"verified_market_snapshot": night}, TRADE_DATE) is False

    def test_an_open_session_reads_open(self, tmp_path):
        graph = self._graph(tmp_path)
        assert graph._market_open_at(
            {"verified_market_snapshot": _snapshot()}, TRADE_DATE,
            decision_as_of=SESSION_STAMP) is True

    def test_no_timestamp_is_not_an_open_market(self, tmp_path):
        graph = self._graph(tmp_path)
        assert graph._market_open_at({"verified_market_snapshot": {}},
                                     TRADE_DATE) is False


class TestTheGateConsumesTheVerdict:
    """T1/T3/T8/T9/T10: what the gate does with each outcome."""

    def _registry(self):
        from tradingagents.dataflows.math.price_source import qualified_price
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
            run_id="fix5")

    def _decide(self, plan, *, snapshot=None, trigger=None, market_open=True,
                registry=None, **extra):
        from tradingagents.dataflows.gate_integration import evaluate_decision

        state = {
            "company_of_interest": TICKER, "trade_date": TRADE_DATE,
            "verified_market_snapshot": snapshot or _snapshot(),
            "fundamentals_report": "",
            "final_rating": "Overweight",
            "final_trade_decision": "**Rating**: Overweight\nBuy.",
            "trader_investment_plan": plan,
        }
        kwargs = {"equity": 100_000.0, "cash": 50_000.0,
                  "risk_budget": 1000.0, "max_symbol_exposure": 0.05}
        kwargs.update(extra)
        return evaluate_decision(
            TICKER, TRADE_DATE, state, snapshot_id="fix5",
            metric_registry=registry or self._registry(),
            entry_trigger=trigger, market_open=market_open, **kwargs)

    def test_t1_a_met_verified_trigger_proposes(self):
        verdict = _evaluate(entry=540.00, mode="BREAKOUT")
        record, decision = self._decide(
            "Buy stop above 540.00, stop 520.00, ATR 1.20.", trigger=verdict)
        assert decision.action.value == "PROPOSE_PAPER_ORDER"
        assert decision.creates_order is True

    def test_t3_an_unmet_trigger_waits(self):
        verdict = _evaluate(entry=560.00, mode="BREAKOUT")
        record, decision = self._decide(
            "Buy stop above 560.00, stop 520.00, ATR 1.20.", trigger=verdict)
        assert decision.action.value == "WAIT"
        assert decision.creates_order is False
        assert "ENTRY_TRIGGER_NOT_MET" in [r.value for r in decision.reasons]

    def test_t8_a_closed_market_waits(self):
        verdict = _evaluate(entry=540.00, mode="BREAKOUT")
        record, decision = self._decide(
            "Buy stop above 540.00, stop 520.00, ATR 1.20.",
            trigger=verdict, market_open=False)
        assert decision.creates_order is False
        assert "MARKET_CLOSED" in [r.value for r in decision.reasons]

    def test_t4_an_ambiguous_plan_does_not_propose(self):
        verdict = _evaluate(entry=540.00, mode=None)
        _, decision = self._decide(
            "Entry 540.00, stop 520.00, ATR 1.20.", trigger=verdict)
        assert decision.creates_order is False

    def test_t9_a_bad_stop_blocks_even_with_a_met_trigger(self):
        """Entry 540 with a stop 539.9 away from a 1.20 ATR fails the buffer."""
        verdict = _evaluate(entry=540.00, mode="BREAKOUT")
        _, decision = self._decide(
            "Buy stop above 540.00, stop 539.90, ATR 1.20.", trigger=verdict)
        assert decision.creates_order is False
        assert decision.action.value == "BLOCK"

    def test_t10_a_missing_budget_blocks(self):
        verdict = _evaluate(entry=540.00, mode="BREAKOUT")
        from tradingagents.dataflows.gate_integration import evaluate_decision

        state = {
            "company_of_interest": TICKER, "trade_date": TRADE_DATE,
            "verified_market_snapshot": _snapshot(),
            "fundamentals_report": "", "final_rating": "Overweight",
            "final_trade_decision": "Buy.",
            "trader_investment_plan": "Buy stop above 540.00, stop 520.00, ATR 1.20.",
        }
        _, decision = evaluate_decision(
            TICKER, TRADE_DATE, state, snapshot_id="fix5",
            metric_registry=self._registry(), entry_trigger=verdict,
            market_open=True)
        assert decision.creates_order is False


class TestTheAuditRecordsTheTrigger:
    """T14: what was compared, at which price, with whose provenance."""

    def test_the_verdict_reaches_the_audit(self):
        from tradingagents.dataflows.gate_integration import evaluate_decision
        from tradingagents.dataflows.math.price_source import qualified_price
        from tradingagents.dataflows.math.run_registry import (
            RunInputs,
            build_run_registry,
        )

        registry = build_run_registry(
            RunInputs(price=550.00,
                      price_provenance=qualified_price(
                          TICKER, 550.00, bar_finality="EOD_CONFIRMED",
                          session_date=TRADE_DATE),
                      eps_ttm=2.0, weighted_average_shares=1e9),
            run_id="fix5-audit")
        verdict = _evaluate(entry=540.00, mode="BREAKOUT", direction="LONG")
        state = {
            "company_of_interest": TICKER, "trade_date": TRADE_DATE,
            "verified_market_snapshot": _snapshot(),
            "fundamentals_report": "", "final_rating": "Overweight",
            "final_trade_decision": "Buy.",
            "trader_investment_plan": "Buy stop above 540.00, stop 520.00, ATR 1.20.",
        }
        record, _ = evaluate_decision(
            TICKER, TRADE_DATE, state, snapshot_id="fix5-audit",
            metric_registry=registry, entry_trigger=verdict, market_open=True,
            equity=100_000.0, cash=50_000.0, risk_budget=1000.0,
            max_symbol_exposure=0.05)

        payload = record.to_dict()["execution"]["entry_trigger"]
        assert payload["met"] is True
        assert payload["mode"] == "BREAKOUT"
        assert payload["entry_level"] == 540.00
        assert payload["observed_price"] == 550.00
        assert payload["observed_status"] == "VERIFIED"
        assert payload["observed_timestamp"] == SESSION_STAMP
        assert payload["reason"]

    def test_an_unmet_verdict_is_also_recorded(self):
        from tradingagents.dataflows.gate_integration import evaluate_decision
        from tradingagents.dataflows.math.price_source import qualified_price
        from tradingagents.dataflows.math.run_registry import (
            RunInputs,
            build_run_registry,
        )

        registry = build_run_registry(
            RunInputs(price=550.00,
                      price_provenance=qualified_price(
                          TICKER, 550.00, bar_finality="EOD_CONFIRMED",
                          session_date=TRADE_DATE),
                      eps_ttm=2.0, weighted_average_shares=1e9),
            run_id="fix5-audit2")
        verdict = _evaluate(entry=560.00, mode="BREAKOUT")
        state = {
            "company_of_interest": TICKER, "trade_date": TRADE_DATE,
            "verified_market_snapshot": _snapshot(),
            "fundamentals_report": "", "final_rating": "Overweight",
            "final_trade_decision": "Buy.",
            "trader_investment_plan": "Buy stop above 560.00, stop 520.00, ATR 1.20.",
        }
        record, _ = evaluate_decision(
            TICKER, TRADE_DATE, state, snapshot_id="fix5-audit2",
            metric_registry=registry, entry_trigger=verdict, market_open=True,
            equity=100_000.0, cash=50_000.0, risk_budget=1000.0,
            max_symbol_exposure=0.05)
        payload = record.to_dict()["execution"]["entry_trigger"]
        assert payload["met"] is False
        assert payload["outcome"] == "NOT_MET"


class TestNoRegression:
    """T12: FIX #1-#4 keep behaving exactly as reviewed."""

    def test_fix2_verified_accounting_still_certifies_a_pb(self):
        from tests.fixtures import verified_accounting
        from tradingagents.dataflows.math.run_registry import (
            RunInputs,
            build_run_registry,
        )

        registry = build_run_registry(
            RunInputs(price=575.25,
                      price_provenance=_snapshot(575.25)["price_provenance"],
                      book_value_per_share=342.1879,
                      equity=761_368_000_000.0, equity_period_end="2026-09-30",
                      shares_class_a=550_000.0, shares_class_b=1_400_000_000.0,
                      class_a_to_b_ratio=1500.0,
                      equity_provenance=verified_accounting()),
            run_id="fix5-pb")
        assert registry.get("price_to_book").is_usable is True

    def test_fix3_an_indicator_certifies_no_price_derived_metric(self):
        from tradingagents.dataflows.math.price_source import indicator_as_price
        from tradingagents.dataflows.math.run_registry import (
            RunInputs,
            build_run_registry,
        )

        registry = build_run_registry(
            RunInputs(price=545.10,
                      price_provenance=indicator_as_price("sma50", 545.10, TICKER),
                      eps_ttm=2.0, weighted_average_shares=1e9),
            run_id="fix5-ind")
        for name in ("pe_trailing", "session_return", "distance_from_sma50"):
            assert registry.get(name).is_usable is False

    def test_fix4_a_wrong_trailing_pe_still_blocks(self):
        from tradingagents.dataflows.math.price_source import qualified_price
        from tradingagents.dataflows.math.report_validation import (
            validate_analyst_report,
        )
        from tradingagents.dataflows.math.run_registry import (
            RunInputs,
            build_run_registry,
        )

        registry = build_run_registry(
            RunInputs(price=50.8,
                      price_provenance=qualified_price(
                          TICKER, 50.8, bar_finality="EOD_CONFIRMED"),
                      eps_ttm=2.0, weighted_average_shares=1e9),
            run_id="fix5-pe")
        validation = validate_analyst_report(
            "Trailing P/E 18.2x", agent="f", registry=registry)
        assert validation.blocks_decision is True

    def test_fix1_sec_revenue_is_unchanged(self):
        from tradingagents.dataflows.math.run_registry import (
            RunInputs,
            build_run_registry,
        )

        registry = build_run_registry(
            RunInputs(revenue_consolidated=105_647_000_000.0,
                      revenue_prior=94_972_000_000.0,
                      revenue_provenance={"validation_status": "VERIFIED",
                                          "concept": "Revenues",
                                          "period_end": "2026-09-30"}),
            run_id="fix5-rev")
        assert registry.get("consolidated_revenue").is_usable is True
        assert float(registry.get("revenue_growth").value) == pytest.approx(
            11.24, abs=0.02)


class TestTheWholePathThroughTheGraph:
    """T13/T15: `_evaluate_reliability` end to end, frozen fixtures, no broker.

    This is the test the earlier 50 gate tests did not have: it drives the
    graph's own reliability path, so the wiring itself is under test. A unit
    test of `evaluate_decision` cannot notice that nobody ever called it with
    a trigger.
    """

    STAMP = "2026-11-10T18:00:00+00:00"

    def _snapshot(self, price=550.00):
        from tradingagents.dataflows.math.price_source import qualified_price

        return {
            "close": price, "price": price, "currency": "USD",
            "price_as_of": self.STAMP,
            "price_provenance": qualified_price(
                TICKER, price, bar_finality="EOD_CONFIRMED",
                session_date=TRADE_DATE, observation_timestamp=self.STAMP),
        }

    def _run(self, tmp_path, plan, price=550.00, filings=True):
        import json
        import pathlib

        from tradingagents.dataflows.filing_freshness import FilingRecord
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        graph = object.__new__(TradingAgentsGraph)
        graph.config = {
            "results_dir": str(tmp_path),
            "deep_think_llm": "m", "quick_think_llm": "m",
            "fundamentals_freshness_gate": True,
            "risk_per_trade_pct": 0.01, "max_symbol_exposure": 0.05,
        }
        graph.selected_analysts = []

        class _Log:
            def store_decision(self, **kwargs):
                pass

        graph.memory_log = _Log()
        filing = FilingRecord(period="2026-Q3", period_end="2026-09-30",
                              filing_date="2026-11-09", form_type="10-Q",
                              accession_number="0001067983-26-000042")
        graph._known_filings = (lambda *a, **k: [filing]) if filings \
            else (lambda *a, **k: [])

        class _Book:
            equity = 100_000.0
            cash = 50_000.0

            def model_dump(self):
                return {"equity": self.equity, "cash": self.cash}

        state = {
            "company_of_interest": TICKER, "trade_date": TRADE_DATE,
            "verified_market_snapshot": self._snapshot(price),
            "trader_investment_plan": plan,
            "final_trade_decision": "Buy.", "final_rating": "Overweight",
            "fundamentals_report": "Reviewed Q3 2026 results.",
            "market_report": "", "news_report": "", "sentiment_report": "",
            "eps_ttm": 2.0, "weighted_average_shares": 1e9,
        }
        graph._evaluate_reliability(
            TICKER, TRADE_DATE, state, portfolio=_Book(),
            decision_as_of=self.STAMP)
        audit = json.loads(next(pathlib.Path(tmp_path).rglob("audit_*.json")).read_text())
        return audit["execution"]

    def test_t1_the_graph_proposes_when_a_verified_breakout_is_met(self, tmp_path):
        execution = self._run(
            tmp_path, "Buy stop above 540.00, stop 520.00, ATR 1.20.")
        assert execution["action"] == "PROPOSE_PAPER_ORDER"
        trigger = execution["entry_trigger"]
        assert trigger["met"] is True
        assert trigger["mode"] == "BREAKOUT"
        assert trigger["entry_level"] == 540.00
        assert trigger["observed_price"] == 550.00
        assert trigger["observed_status"] == "VERIFIED"

    def test_t3_the_graph_waits_when_the_breakout_is_not_reached(self, tmp_path):
        execution = self._run(
            tmp_path, "Buy stop above 560.00, stop 540.00, ATR 1.20.")
        assert execution["action"] == "WAIT"
        assert "ENTRY_TRIGGER_NOT_MET" in execution["reason_codes"]
        assert execution["entry_trigger"]["met"] is False

    def test_t4_an_ambiguous_plan_never_proposes(self, tmp_path):
        execution = self._run(tmp_path, "Entry 540.00, stop 520.00, ATR 1.20.")
        assert execution["action"] == "WAIT"
        assert execution["entry_trigger"]["mode"] == "AMBIGUOUS"
        assert execution["entry_trigger"]["met"] is False

    def test_t2_a_limit_buy_above_its_limit_waits(self, tmp_path):
        execution = self._run(
            tmp_path, "Limit buy at 540.00, stop 520.00, ATR 1.20.")
        assert execution["action"] == "WAIT"
        assert execution["entry_trigger"]["mode"] == "PULLBACK"

    def test_t8_a_closed_session_waits(self, tmp_path):
        """A stamp outside the regular session is not an open market."""
        import json
        import pathlib

        from tradingagents.dataflows.filing_freshness import FilingRecord
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        graph = object.__new__(TradingAgentsGraph)
        graph.config = {
            "results_dir": str(tmp_path), "deep_think_llm": "m",
            "quick_think_llm": "m", "fundamentals_freshness_gate": True,
            "risk_per_trade_pct": 0.01, "max_symbol_exposure": 0.05,
        }
        graph.selected_analysts = []

        class _Log:
            def store_decision(self, **kwargs):
                pass

        graph.memory_log = _Log()
        graph._known_filings = lambda *a, **k: [
            FilingRecord(period="2026-Q3", period_end="2026-09-30",
                         filing_date="2026-11-09", form_type="10-Q",
                         accession_number="0001067983-26-000042")]

        class _Book:
            equity = 100_000.0
            cash = 50_000.0

            def model_dump(self):
                return {"equity": self.equity, "cash": self.cash}

        night = self._snapshot()
        night["price_as_of"] = f"{TRADE_DATE}T02:00:00+00:00"
        state = {
            "company_of_interest": TICKER, "trade_date": TRADE_DATE,
            "verified_market_snapshot": night,
            "trader_investment_plan": "Buy stop above 540.00, stop 520.00, ATR 1.20.",
            "final_trade_decision": "Buy.", "final_rating": "Overweight",
            "fundamentals_report": "Reviewed Q3 2026 results.",
            "market_report": "", "news_report": "", "sentiment_report": "",
            "eps_ttm": 2.0, "weighted_average_shares": 1e9,
        }
        graph._evaluate_reliability(
            TICKER, TRADE_DATE, state, portfolio=_Book(),
            decision_as_of=f"{TRADE_DATE}T02:00:00+00:00")
        execution = json.loads(
            next(pathlib.Path(tmp_path).rglob("audit_*.json")).read_text())["execution"]
        assert execution["action"] == "WAIT"
        assert "MARKET_CLOSED" in execution["reason_codes"]

    def test_a_proposal_stays_in_memory_and_is_never_sent(self, tmp_path):
        """T15: the authorised order is a plan record, not a transmission.

        `authorized_orders` describes what the plan *would* submit — the same
        distinction §4 draws between an intent and an order. Nothing in the
        verdict module reaches a broker: there is no adapter, no client and no
        transmission call, and `market_order_allowed` is false by construction.
        """
        import inspect

        from tradingagents.dataflows import execution_verdict

        execution = self._run(
            tmp_path, "Buy stop above 540.00, stop 520.00, ATR 1.20.")
        verdict = execution["verdict"]
        # The plan is described, and it is described correctly.
        assert execution["action"] == "PROPOSE_PAPER_ORDER"
        assert len(verdict["authorized_orders"]) == 1
        order = verdict["authorized_orders"][0]
        assert order["quantity"] > 0
        assert order["entry"] == 540.0
        # And nothing was transmitted: no broker surface exists to transmit to.
        assert execution["market_order_allowed"] is False
        module_source = inspect.getsource(execution_verdict)
        for forbidden in ("requests.", "httpx.", "urlopen", "broker.submit",
                          "place_order", "send_order"):
            assert forbidden not in module_source, (
                f"{forbidden!r} in execution_verdict: an order could be sent")

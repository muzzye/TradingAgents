"""P0-C wiring: ``aggregate_tranche_risk`` actually prices the real flow.

The audit found the aggregate risk engine *present but uncalled*: it existed
and was tested in isolation, yet ``record_decision`` never invoked it, so a
multi-leg plan was never priced as a whole and the per-symbol budget was
never enforced across legs.

This suite pins the wiring, in three parts:

1. **Extraction** — a multi-leg plan is read from its own labelled lines
   (``Tranche 1: entry ... stop ...``). A plan that states a single entry and
   stop is one tranche, not a failure: the single-leg case is the degenerate
   case of the same code path. Bare numbers in prose are never guessed.
2. **Aggregation in the gate** — ``evaluate_decision`` prices every leg and
   blocks when the aggregate budget cannot be met or a leg's geometry is
   invalid.
3. **The real flow** — ``record_decision`` derives the portfolio numbers from
   the caller's book and writes the aggregate result to the audit, so the
   engine is no longer dead code.

Nothing here is ticker-specific: BRK-B appears only as a fixture of a plan
shape that happens to be the regression case.
"""

from __future__ import annotations

import json

import pytest

from tradingagents.dataflows.plan_extraction import (
    extract_tranche_plans,
)
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.portfolio import PortfolioContext


class TestTrancheExtraction:
    def test_two_labelled_tranches_are_read_separately(self):
        """Each leg keeps its own entry and stop — the audit's core point."""
        plan = (
            "Plan:\n"
            "Tranche 1: entry 555.00, stop 542.50\n"
            "Tranche 2: entry 560.00, stop 547.00\n"
            "ATR 9.408\n"
        )
        legs = extract_tranche_plans(plan, default_atr=9.408)
        assert len(legs) == 2
        assert legs[0].entry == pytest.approx(555.0)
        assert legs[0].stop == pytest.approx(542.50)
        assert legs[1].entry == pytest.approx(560.0)
        assert legs[1].stop == pytest.approx(547.0)

    def test_a_single_plan_is_one_tranche(self):
        """The single-leg case is the same path, not a special case."""
        legs = extract_tranche_plans("Entry 556.00, stop 542.50, ATR 9.408.")
        assert len(legs) == 1
        assert legs[0].entry == pytest.approx(556.0)
        assert legs[0].stop == pytest.approx(542.50)
        assert legs[0].atr == pytest.approx(9.408)

    def test_a_plan_without_labelled_numbers_yields_no_tranche(self):
        """Prose prices are never guessed into a plan."""
        legs = extract_tranche_plans("The stock traded between 557 and 562.")
        assert legs == []

    def test_none_and_empty_yield_no_tranche(self):
        assert extract_tranche_plans(None) == []
        assert extract_tranche_plans("") == []

    def test_a_tranche_without_a_stop_is_dropped(self):
        """A leg with no protective stop cannot be priced, so it is not one."""
        plan = "Tranche 1: entry 555.00\nTranche 2: entry 560.00, stop 547.00\n"
        legs = extract_tranche_plans(plan)
        assert len(legs) == 1
        assert legs[0].entry == pytest.approx(560.0)

    def test_a_shared_atr_line_applies_to_every_leg(self):
        plan = (
            "Tranche 1: entry 555.00, stop 542.50\n"
            "Tranche 2: entry 560.00, stop 547.00\n"
            "ATR(14): 9.408\n"
        )
        legs = extract_tranche_plans(plan)
        assert [leg.atr for leg in legs] == pytest.approx([9.408, 9.408])

    def test_short_direction_applies_to_the_legs(self):
        plan = (
            "Short setup.\n"
            "Tranche 1: entry 556.00, stop 568.00\n"
            "Tranche 2: entry 552.00, stop 562.00\n"
        )
        legs = extract_tranche_plans(plan)
        assert len(legs) == 2
        assert {leg.direction for leg in legs} == {"SHORT"}

    def test_italian_plan_wording_is_not_guessed(self):
        """The regression report's prose ("una tranche pari a ~25-30%") states
        no price per leg, so no leg is invented from it."""
        plan = (
            "3. Trigger di accumulo: pullback ordinato in area 554-560 "
            "(10-EMA 557,90) → aggiungere una tranche pari a ~25-30% della "
            "posizione obiettivo. 5. Invalidazione: chiusura sotto 542,5-543,7."
        )
        assert extract_tranche_plans(plan) == []


class TestAggregateInTheGate:
    def _state(self, plan: str, **extra):
        state = {
            "final_rating": "Overweight",
            "trader_investment_plan": plan,
            "final_trade_decision": "Buy on the trigger.",
        }
        state.update(extra)
        return state

    def test_two_legs_are_priced_and_report_both_quantities(self):
        from tradingagents.dataflows.gate_integration import evaluate_decision

        record, _ = evaluate_decision(
            "BRK-B", "2026-11-08",
            self._state(
                "Tranche 1: entry 555.00, stop 542.50\n"
                "Tranche 2: entry 560.00, stop 547.00\nATR 9.408\n"
            ),
            snapshot_id="s1",
            trigger_met=True,
            equity=100_000.0,
            cash=100_000.0,
            risk_budget=1_000.0,
            max_symbol_exposure=0.10,
        )
        aggregate = record.aggregate_risk
        assert aggregate is not None
        assert len(aggregate.tranches) == 2
        # Each leg's risk per share comes from its own geometry.
        per_share = [round(t.risk_per_share, 4) for t in aggregate.tranches]
        assert per_share == [12.5, 13.0]
        assert aggregate.total_risk <= 1_000.0

    def test_a_wrong_side_leg_blocks_the_whole_plan(self):
        from tradingagents.dataflows.execution_gate import ExecutionAction
        from tradingagents.dataflows.gate_integration import evaluate_decision

        record, decision = evaluate_decision(
            "BRK-B", "2026-11-08",
            self._state(
                "Tranche 1: entry 555.00, stop 542.50\n"
                "Tranche 2: entry 560.00, stop 570.00\nATR 9.408\n"
            ),
            snapshot_id="s2",
            trigger_met=True,
            equity=100_000.0,
            cash=100_000.0,
            risk_budget=1_000.0,
            max_symbol_exposure=0.10,
        )
        assert record.aggregate_risk.blocked
        assert "INVALID_STOP_SIDE" in record.aggregate_risk.reasons
        assert decision.action is ExecutionAction.BLOCK

    def test_without_portfolio_context_no_size_is_invented(self):
        """No equity: the engine refuses rather than guessing a book."""
        from tradingagents.dataflows.gate_integration import evaluate_decision

        record, _ = evaluate_decision(
            "BRK-B", "2026-11-08",
            self._state("Tranche 1: entry 555.00, stop 542.50\nATR 9.408\n"),
            snapshot_id="s3",
            trigger_met=True,
            equity=None,
            risk_budget=None,
        )
        aggregate = record.aggregate_risk
        assert aggregate is not None
        assert aggregate.blocked
        assert aggregate.total_qty == 0


class TestTheRealFlowCallsIt:
    """The audit's finding: the engine existed but ``record_decision`` never
    reached it. These tests pin the call, not the algorithm."""

    def _graph(self, tmp_path, **overrides):
        graph = object.__new__(TradingAgentsGraph)
        graph.config = {
            "results_dir": str(tmp_path / "results"),
            "deep_think_llm": "glm-5.3:cloud",
            "quick_think_llm": "deepseek-v4.1-flash:cloud",
            "fundamentals_freshness_gate": False,
            "risk_per_trade_pct": 0.01,
            "max_symbol_exposure": 0.05,
        }
        graph.config.update(overrides)
        graph.selected_analysts = []

        class _Log:
            def __init__(self):
                self.stored = None

            def store_decision(self, **kwargs):
                self.stored = kwargs

        graph.memory_log = _Log()
        return graph

    def _book(self):
        return PortfolioContext.model_validate({
            "cash": 50_000.0,
            "currency": "USD",
            "equity": 100_000.0,
            "positions": [],
        })

    def _state(self):
        return {
            "company_of_interest": "BRK-B",
            "trade_date": "2026-11-08",
            "market_report": "",
            "sentiment_report": "",
            "news_report": "",
            "fundamentals_report": "",
            "investment_debate_state": {"bull_history": "", "bear_history": "",
                                        "history": "", "current_response": ""},
            "trader_investment_plan": (
                "Tranche 1: entry 555.00, stop 542.50\n"
                "Tranche 2: entry 560.00, stop 547.00\nATR 9.408\n"
            ),
            "risk_debate_state": {"aggressive_history": "", "conservative_history": "",
                                  "neutral_history": "", "history": ""},
            "investment_plan": "",
            "final_rating": "Overweight",
            "final_trade_decision": "**Rating**: Overweight\nBuy on the trigger.",
        }

    def test_the_book_drives_the_risk_budget(self, tmp_path):
        """1% of a 100k account is a 1,000 budget — from the book, not a constant."""
        graph = self._graph(tmp_path)
        graph.record_decision("BRK-B", "2026-11-08", self._state(),
                              portfolio=self._book())
        audit = json.loads(
            (tmp_path / "results" / "BRK-B" / "reliability_audit" /
             "audit_2026-11-08.json").read_text()
        )
        risk = audit["risk"]
        assert risk["aggregate_total_risk"] <= 1_000.0
        assert len(risk["aggregate_tranches"]) == 2

    def test_each_leg_is_priced_on_its_own_stop(self, tmp_path):
        graph = self._graph(tmp_path)
        graph.record_decision("BRK-B", "2026-11-08", self._state(),
                              portfolio=self._book())
        audit = json.loads(
            (tmp_path / "results" / "BRK-B" / "reliability_audit" /
             "audit_2026-11-08.json").read_text()
        )
        per_share = [t["risk_per_share"] for t in audit["risk"]["aggregate_tranches"]]
        # 12.50 and 13.00: the second leg does not inherit the first's risk.
        assert per_share == pytest.approx([12.50, 13.00])

    def test_no_book_means_no_invented_size(self, tmp_path):
        """An absent portfolio is unknown, not flat: nothing is sized."""
        graph = self._graph(tmp_path)
        graph.record_decision("BRK-B", "2026-11-08", self._state())
        audit = json.loads(
            (tmp_path / "results" / "BRK-B" / "reliability_audit" /
             "audit_2026-11-08.json").read_text()
        )
        risk = audit["risk"]
        assert risk["aggregate_blocked"] is True
        assert risk["aggregate_total_qty"] == 0
        assert risk["aggregate_reasons"]

    def test_a_book_without_equity_is_still_not_flat(self, tmp_path):
        """Cash alone cannot serve as the equity base for an exposure cap."""
        graph = self._graph(tmp_path)
        book = PortfolioContext.model_validate({"cash": 50_000.0, "positions": []})
        graph.record_decision("BRK-B", "2026-11-08", self._state(), portfolio=book)
        audit = json.loads(
            (tmp_path / "results" / "BRK-B" / "reliability_audit" /
             "audit_2026-11-08.json").read_text()
        )
        assert audit["risk"]["aggregate_blocked"] is True

    def test_an_open_position_counts_against_the_aggregate_budget(self, tmp_path):
        """What is already held consumes the per-symbol cap, so two new legs
        cannot both be sized as if the book were empty."""
        flat = self._graph(tmp_path / "flat")
        flat.record_decision("BRK-B", "2026-11-08", self._state(),
                             portfolio=self._book())
        held = self._graph(tmp_path / "held")
        held_book = PortfolioContext.model_validate({
            "cash": 50_000.0, "currency": "USD", "equity": 100_000.0,
            "positions": [{"ticker": "BRK-B", "quantity": 8,
                           "average_price": 550.0}],
        })
        held.record_decision("BRK-B", "2026-11-08", self._state(),
                             portfolio=held_book)

        def read(root):
            return json.loads(
                (root / "results" / "BRK-B" / "reliability_audit" /
                 "audit_2026-11-08.json").read_text()
            )["risk"]

        # The open 8-share position consumes the cap, so fewer NEW shares can
        # be sized. Asserting on new quantity — not on total notional — because
        # the total includes the existing position by construction and the two
        # figures can coincide numerically.
        assert read(tmp_path / "held")["aggregate_total_qty"] < \
            read(tmp_path / "flat")["aggregate_total_qty"]

    def test_the_existing_position_is_counted_in_the_aggregate_notional(self, tmp_path):
        """The aggregate budget is about total exposure, so the open position
        is added, not ignored: a run that already holds the symbol cannot size
        as if the book were empty."""
        graph = self._graph(tmp_path)
        book = PortfolioContext.model_validate({
            "cash": 50_000.0, "currency": "USD", "equity": 100_000.0,
            "positions": [{"ticker": "BRK-B", "quantity": 8,
                           "average_price": 550.0}],
        })
        graph.record_decision("BRK-B", "2026-11-08", self._state(), portfolio=book)
        risk = json.loads(
            (tmp_path / "results" / "BRK-B" / "reliability_audit" /
             "audit_2026-11-08.json").read_text()
        )["risk"]
        new_notional = sum(t["notional"] for t in risk["aggregate_tranches"])
        assert risk["aggregate_total_notional"] > new_notional

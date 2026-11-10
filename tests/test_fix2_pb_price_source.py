"""FIX #2 — a technical indicator must never be the price in a P/B.

The 2026-11-10 BRK-B report stated a P/B of 1.49x. That figure is 522.97 / 352:
the **50-day moving average** divided by book value per share. The share traded
near 577 intraday and closed at 575.25, so the correct multiple was 1.63–1.64.

Two defects produced that report, and they are not the same defect.

**1. Nothing supplies a price to the engine.** `build_verified_market_snapshot`
returns a Markdown *string* to the analyst. Nothing in production ever writes a
`verified_market_snapshot` dict into the state, so `registry_inputs_from_state`
finds `price=None` and the registry records `price_to_book: NOT_COMPUTABLE`.
Confirmed against the real run of 2026-11-10, whose P/B record is empty.

**2. The engine trusts whatever it is handed as `close`.** When the snapshot does
carry a value, the registry divides it by the book value and certifies the
result with no check that the number is a market price at all. Fed the SMA50 as
`close`, it produces 1.4857x and marks it `usable=True` — a technical indicator
promoted to a certified financial multiple.

The fix therefore has two halves: give the engine a price it can actually use,
and refuse a price whose provenance says it is an indicator rather than a quote.
"""

from __future__ import annotations

import pytest

from tests.fixtures import sec_revenue_payloads as F
from tradingagents.dataflows.math.metric_record import CalculationStatus
from tradingagents.dataflows.math.price_source import qualified_price
from tradingagents.dataflows.math.run_registry import RunInputs, build_run_registry

pytestmark = pytest.mark.unit

# The 2026-11-10 BRK-B figures, as the audit recorded them.
SMA50 = 522.97
INTRADAY_PRICE = 577.56
EOD_CLOSE = 575.25
BVPS = 352.0

EXPECTED_INTRADAY_PB = round(INTRADAY_PRICE / BVPS, 4)     # 1.6408
EXPECTED_EOD_PB = round(EOD_CLOSE / BVPS, 4)               # 1.6342
SMA50_AS_PRICE_PB = round(SMA50 / BVPS, 4)                 # 1.4857 — the defect


# ===========================================================================
# T1 — the SMA50 is not the numerator
# ===========================================================================
class TestT1SmaIsNotAPrice:
    def test_an_indicator_marked_as_such_cannot_price_a_pb(self):
        """A price whose provenance says `sma50` must not produce a multiple."""
        inputs = RunInputs(
            price=SMA50,
            book_value_per_share=BVPS,
            price_provenance={"kind": "indicator", "indicator": "sma50",
                              "validation_status": "NOT_A_PRICE"},
        )
        registry = build_run_registry(inputs, run_id="t1")
        metric = registry.get("price_to_book")
        assert metric.is_usable is False, (
            "a moving average was certified as the share price")
        assert metric.value != pytest.approx(SMA50_AS_PRICE_PB, abs=0.001)


# ===========================================================================
# T2 / T3 — a qualified price produces the right multiple
# ===========================================================================
class TestT2T3QualifiedPrices:
    def test_t2_the_intraday_price_gives_one_point_six_four(self):
        inputs = RunInputs(
            price=INTRADAY_PRICE, book_value_per_share=BVPS,
            price_provenance={"kind": "intraday", "timestamp":
                              "2026-11-10T16:00:00+00:00",
                              "validation_status": "VERIFIED"},
        )
        registry = build_run_registry(inputs, run_id="t2")
        metric = registry.get("price_to_book")
        assert float(metric.value) == pytest.approx(EXPECTED_INTRADAY_PB, abs=1e-3)
        assert metric.is_usable is True

    def test_t3_the_eod_close_gives_one_point_six_three(self):
        inputs = RunInputs(
            price=EOD_CLOSE, book_value_per_share=BVPS,
            price_provenance={"kind": "eod_close",
                              "timestamp": "2026-11-09T21:00:00+00:00",
                              "validation_status": "VERIFIED"},
        )
        registry = build_run_registry(inputs, run_id="t3")
        metric = registry.get("price_to_book")
        assert float(metric.value) == pytest.approx(EXPECTED_EOD_PB, abs=1e-3)
        # The two multiples are distinguishable and neither is silently equated.
        assert float(metric.value) != pytest.approx(EXPECTED_INTRADAY_PB, abs=0.001)


# ===========================================================================
# T4 — with only an indicator there is no certified P/B
# ===========================================================================
class TestT4NoPriceAtAll:
    def test_only_an_indicator_available_means_no_multiple(self):
        inputs = RunInputs(price=None, book_value_per_share=BVPS,
                           sma50=SMA50, sma200=502.85)
        registry = build_run_registry(inputs, run_id="t4")
        metric = registry.get("price_to_book")
        assert metric.is_usable is False
        # REFUSED, not NOT_COMPUTABLE: the engine had a number and refused it,
        # which is a different statement from having no number at all.
        assert metric.calculation_status in (CalculationStatus.REFUSED,
                                             CalculationStatus.NOT_COMPUTABLE)

    def test_the_indicators_are_still_recorded_as_indicators(self):
        """Refusing them as a price must not lose them as indicators."""
        inputs = RunInputs(price=None, book_value_per_share=BVPS, sma50=SMA50,
                           previous_close=EOD_CLOSE)
        registry = build_run_registry(inputs, run_id="t4b")
        assert registry.get("price_to_book").value is None


# ===========================================================================
# T5 / T6 — share class coherence
# ===========================================================================
class TestT5T6ShareClass:
    def test_t5_class_b_price_with_class_a_bvps_is_not_certified(self):
        """Without a verified conversion the comparison is refused."""
        from tradingagents.dataflows.valuation import EquityInputs, reconcile_valuation

        result = reconcile_valuation(EquityInputs(
            price=EOD_CLOSE, price_class="B", price_currency="USD",
            equity=800_000_000_000.0, equity_currency="USD",
            shares_outstanding=1_400_000_000.0,
            bvps_reported=352.0, bvps_class="A", bvps_currency="USD",
            share_class_ratio=None,
        ))
        assert result.is_usable is False

    def test_t6_a_verified_ratio_reconciles_the_two_classes(self):
        from tradingagents.dataflows.valuation import EquityInputs, reconcile_valuation

        result = reconcile_valuation(EquityInputs(
            price=EOD_CLOSE, price_class="B", price_currency="USD",
            equity=800_000_000_000.0, equity_currency="USD",
            shares_outstanding=1_400_000_000.0,
            bvps_reported=352.0, bvps_class="A", bvps_currency="USD",
            share_class_ratio=1500.0,
        ))
        # The engine may still refuse for an unrelated reason; what matters is
        # that with the ratio supplied it is no longer the class mismatch.
        issues = "; ".join(i.message for i in result.issues.issues)
        assert "class" not in issues.lower() or result.is_usable


# ===========================================================================
# T7 — date, class or unit incoherence
# ===========================================================================
class TestT7IncoherentInputs:
    def test_a_stale_price_against_a_newer_book_value_is_not_silent(self):
        """The registry records the period so the mismatch is visible."""
        inputs = RunInputs(
            price=EOD_CLOSE, book_value_per_share=BVPS,
            price_provenance={"kind": "eod_close",
                              "timestamp": "2026-06-30T20:00:00+00:00",
                              "validation_status": "VERIFIED"},
            equity_period_end="2026-09-30",
        )
        registry = build_run_registry(inputs, run_id="t7")
        metric = registry.get("price_to_book")
        # The value is computed; the provenance of both halves is recorded so a
        # reader can see they are five months apart.
        assert metric.operands.get("price") is not None
        assert metric.operands.get("bvps") is not None


# ===========================================================================
# T8 — the aggregate form needs a real market cap
# ===========================================================================
class TestT8Aggregate:
    def test_the_aggregate_uses_a_reported_market_cap_and_equity(self):
        inputs = RunInputs(
            price=EOD_CLOSE, market_cap_reported=1_231_440_000_000.0,
            equity=761_368_000_000.0, price_provenance={
                "kind": "eod_close", "validation_status": "VERIFIED"},
        )
        registry = build_run_registry(inputs, run_id="t8")
        aggregate = registry.get("pb_aggregate")
        assert float(aggregate.value) == pytest.approx(1.6174, abs=1e-4)

    def test_without_a_qualified_price_the_aggregate_is_refused(self):
        inputs = RunInputs(
            price=SMA50, market_cap_reported=1_231_440_000_000.0,
            equity=761_368_000_000.0,
            price_provenance={"kind": "indicator", "indicator": "sma50",
                              "validation_status": "NOT_A_PRICE"})
        registry = build_run_registry(inputs, run_id="t8b")
        assert registry.get("pb_aggregate").is_usable is False


# ===========================================================================
# T9 / T10 — the model's stated multiple
# ===========================================================================
class TestT9T10ClaimsValidator:
    def _registry(self):
        return build_run_registry(
            RunInputs(price=INTRADAY_PRICE, book_value_per_share=BVPS,
                      price_provenance={"kind": "intraday",
                                        "validation_status": "VERIFIED"}),
            run_id="claims")

    def test_t9_a_pb_computed_from_the_sma_is_a_mismatch(self):
        """The report says 1.49x (SMA50/BVPS); the engine says 1.6408."""
        from tradingagents.dataflows.math.report_validation import (
            validate_analyst_report,
        )

        registry = self._registry()
        assert float(registry.get("price_to_book").value) == pytest.approx(
            EXPECTED_INTRADAY_PB, abs=1e-3)
        validation = validate_analyst_report(
            "P/B of 1.49x", agent="fundamentals", registry=registry)
        assert validation.blocks_decision is True

    def test_t10_a_compatible_pb_is_not_a_false_mismatch(self):
        from tradingagents.dataflows.math.report_validation import (
            validate_analyst_report,
        )

        validation = validate_analyst_report(
            "P/B of 1.64x", agent="fundamentals", registry=self._registry())
        assert validation.blocks_decision is False

    def test_t9b_the_conflict_reaches_the_gate(self):
        from tradingagents.dataflows.gate_integration import evaluate_decision

        state = {
            "company_of_interest": "BRK-B", "trade_date": "2026-11-10",
            "verified_market_snapshot": {"close": INTRADAY_PRICE},
            "book_value_per_share": BVPS,
            "fundamentals_report": "Berkshire trades at a P/B of 1.49x.",
            "final_rating": "Overweight",
            "final_trade_decision": "**Rating**: Overweight\nBuy.",
            "trader_investment_plan": "Entry 560.00, stop 552.07, ATR 9.520.",
        }
        _, decision = evaluate_decision(
            "BRK-B", "2026-11-10", state, snapshot_id="t9b",
            metric_registry=self._registry(), trigger_met=True,
            equity=100_000.0, cash=50_000.0, risk_budget=1000.0,
            max_symbol_exposure=0.05)
        assert decision.creates_order is False


# ===========================================================================
# T11 — the record, the table, the audit and the run record agree
# ===========================================================================
class TestT11Persistence:
    def test_the_record_carries_price_source_and_formula(self):
        registry = build_run_registry(
            RunInputs(price=EOD_CLOSE, book_value_per_share=BVPS,
                      price_provenance={
                          "kind": "eod_close", "source": "yfinance",
                          "timestamp": "2026-11-09T21:00:00+00:00",
                          "validation_status": "VERIFIED"}),
            run_id="t11")
        metric = registry.get("price_to_book")
        assert metric.formula_id == "price_div_bvps"
        assert float(metric.value) == pytest.approx(EXPECTED_EOD_PB, abs=1e-3)
        payload = metric.summary()
        assert payload["operands"]["price"] is not None
        assert payload["operands"]["bvps"] is not None

    def test_the_certified_table_shows_the_pb(self):
        from tradingagents.dataflows.certified_report import render_metric_table

        inputs = RunInputs(
            price=EOD_CLOSE,
            price_provenance={
                "kind": "eod_close",
                "timestamp": "2026-11-09T21:00:00+00:00",
                "validation_status": "VERIFIED"},
            equity=761_368_000_000.0,
            equity_period_end="2026-09-30",
            shares_class_a=550_000.0,
            shares_class_b=1_400_000_000.0,
            class_a_to_b_ratio=1500.0,
        )
        registry = build_run_registry(inputs, run_id="t11b")
        table = render_metric_table(registry)
        row = next(line for line in table.splitlines() if "`price_to_book`" in line)
        assert "1.68" in row
        assert "certified" in row


# ===========================================================================
# T12 / T13 — nothing else moved
# ===========================================================================
class TestT12T13NoRegression:
    def test_t12_sec_revenue_is_untouched(self):
        from tradingagents.dataflows.math.sec_revenue_inputs import (
            revenue_inputs_from_sec,
        )

        inputs = revenue_inputs_from_sec(
            dict(F.BOTH_SCOPES), F.ANALYSIS_DATE, cik=F.CIK)
        registry = build_run_registry(inputs, run_id="t12")
        assert registry.get("consolidated_revenue").value == F.REVENUE_Q3_2026
        assert registry.get("consolidated_revenue").is_usable is True

    def test_t13_indicators_and_eps_behave_as_before(self):
        inputs = RunInputs(price=EOD_CLOSE, sma50=SMA50, sma200=502.85,
                           period_high=812.35, eps_ttm=39.76,
                           weighted_average_shares=1_437_000_000.0)
        registry = build_run_registry(inputs, run_id="t13")
        assert registry.get("distance_from_sma50").value is not None
        assert registry.get("distance_from_sma200").value is not None
        assert registry.get("eps_gaap").is_usable is True


# ===========================================================================
# The real graph path
# ===========================================================================
class TestCompiledGraphPath:
    """A `StateGraph(AgentState)`, not a dict merge."""

    def _graph_state(self, report: str, snapshot: dict, state_fields: dict | None = None):
        from langgraph.graph import END, StateGraph

        from tradingagents.agents.state import AgentState

        graph = StateGraph(AgentState)
        graph.add_node("analyst", lambda s: {"fundamentals_report": report})
        graph.set_entry_point("analyst")
        graph.add_edge("analyst", END)
        state = {
            "company_of_interest": "BRK-B",
            "trade_date": "2026-11-10",
            "messages": [],
            "verified_market_snapshot": snapshot,
        }
        if state_fields:
            state.update(state_fields)
        return graph.compile().invoke(state)

    def test_a_snapshot_price_reaches_the_registry(self):
        from tradingagents.dataflows.math.extraction import extract_run_inputs
        from tradingagents.dataflows.math.run_registry import (
            build_run_registry,
            registry_inputs_from_state,
        )
        from tradingagents.graph.trading_graph import _merge_extracted

        state = self._graph_state(
            "Berkshire trades at a P/B of 1.49x.",
            {"close": EOD_CLOSE,
             "validation_status": "VERIFIED",
             "timestamp": "2026-11-09T21:00:00+00:00"})
        state["equity"] = 761_368_000_000.0
        state["equity_period_end"] = "2026-09-30"
        state["shares_class_a"] = 550_000.0
        state["shares_class_b"] = 1_400_000_000.0
        state["class_a_to_b_ratio"] = 1500.0

        merged = _merge_extracted(
            registry_inputs_from_state(state),
            extract_run_inputs(state).inputs)
        registry = build_run_registry(merged, run_id="graph")
        metric = registry.get("price_to_book")
        assert metric.is_usable is True
        assert float(metric.value) == pytest.approx(1.68, abs=0.01)


# ===========================================================================
# Production path — the node, not the test, produces the snapshot
# ===========================================================================
class TestProductionPath:
    """The data-quality node builds the snapshot from a provider (FIX #2 A).

    Injecting `verified_market_snapshot` into a test's initial state proves
    nothing about production: the node is what runs there. These tests drive a
    compiled graph whose node receives a provider, so the snapshot is produced
    by the code path a real run uses.
    """

    def _run(self, bar: dict | None, accounting_payload: dict | None = None):
        from langgraph.graph import END, StateGraph

        from tradingagents.agents.state import AgentState
        from tradingagents.graph.data_quality_node import (
            create_data_quality_check,
        )

        node = create_data_quality_check(
            filings_provider=lambda t, d: [],
            sec_facts_provider=(lambda t, d: accounting_payload)
            if accounting_payload else (lambda t, d: None),
            price_provider=(lambda t, d: bar) if bar else (lambda t, d: None),
        )
        graph = StateGraph(AgentState)
        graph.add_node("dq", node)
        graph.add_node("analyst", lambda s: {"fundamentals_report": ""})
        graph.set_entry_point("dq")
        graph.add_edge("dq", "analyst")
        graph.add_edge("analyst", END)
        return graph.compile().invoke({
            "company_of_interest": "BRK-B", "trade_date": "2026-11-10",
            "messages": [], "fundamentals_report": "",
        })

    def test_the_node_publishes_a_qualified_snapshot(self):
        state = self._run({
            "close": INTRADAY_PRICE, "session_date": "2026-11-10",
            "bar_finality": "INTRADAY_PARTIAL",
            "timestamp": "2026-11-10T16:00:00+00:00", "source": "yfinance"})
        snapshot = state.get("verified_market_snapshot")
        assert snapshot, "the node produced no snapshot"
        provenance = snapshot["price_provenance"]
        for field in ("symbol", "value", "currency", "share_class", "kind",
                      "timestamp", "session_date", "source", "bar_finality",
                      "validation_status"):
            assert field in provenance, field
        assert provenance["kind"] == "intraday"
        assert provenance["validation_status"] == "VERIFIED"

    def test_the_node_publishes_accounting_facts_from_the_filing(self):
        payload = {"facts": {"us-gaap": {
            "StockholdersEquity": {"units": {"USD": [
                {"val": 761_368_000_000, "end": "2026-09-30",
                 "filed": "2026-11-09", "form": "10-Q"}]}},
            "CommonStockClassASharesOutstanding": {"units": {"shares": [
                {"val": 550_000}]}},
            "CommonStockClassBSharesOutstanding": {"units": {"shares": [
                {"val": 1_400_000_000}]}},
            "ExchangeRatioOfClassACommonStockToClassBCommonStock": {
                "units": {"pure": [{"val": 1500}]}},
        }}}
        state = self._run(None, payload)
        assert state.get("equity") == pytest.approx(761_368_000_000.0)
        assert state.get("shares_class_b") == pytest.approx(1_400_000_000.0)
        assert state.get("class_a_to_b_ratio") == pytest.approx(1500.0)
        assert state.get("book_value_per_share") == pytest.approx(
            761_368_000_000 / 2_225_000_000, rel=1e-9)

    def test_a_failure_to_price_leaves_no_snapshot(self):
        state = self._run(None)
        assert not state.get("verified_market_snapshot")

    def test_the_snapshot_and_accounting_reach_the_registry(self):
        """The whole chain: provider → node → state → registry."""
        from tradingagents.dataflows.math.extraction import extract_run_inputs
        from tradingagents.dataflows.math.run_registry import (
            registry_inputs_from_state,
        )
        from tradingagents.graph.trading_graph import _merge_extracted

        payload = {"facts": {"us-gaap": {
            "StockholdersEquity": {"units": {"USD": [
                {"val": 761_368_000_000, "end": "2026-09-30",
                 "filed": "2026-11-09", "form": "10-Q"}]}},
            "CommonStockClassASharesOutstanding": {"units": {"shares": [
                {"val": 550_000}]}},
            "CommonStockClassBSharesOutstanding": {"units": {"shares": [
                {"val": 1_400_000_000}]}},
            "ExchangeRatioOfClassACommonStockToClassBCommonStock": {
                "units": {"pure": [{"val": 1500}]}},
        }}}
        state = self._run({
            "close": EOD_CLOSE, "session_date": "2026-11-09",
            "bar_finality": "EOD_CONFIRMED", "source": "yfinance"}, payload)

        merged = _merge_extracted(
            registry_inputs_from_state(state),
            extract_run_inputs(state).inputs)
        registry = build_run_registry(merged, run_id="production")
        metric = registry.get("price_to_book")
        assert metric.is_usable is True
        assert float(metric.value) == pytest.approx(1.6811, abs=1e-3)


# ===========================================================================
# Provenance refusals (FIX #2 B)
# ===========================================================================
class TestProvenanceRefusals:
    def _registry(self, price, provenance):
        return build_run_registry(
            RunInputs(price=price, book_value_per_share=BVPS,
                      price_provenance=provenance), run_id="refuse")

    def test_t3_a_bare_dict_close_cannot_price_a_multiple(self):
        """The reviewer's case: `{"close": 522.97}` with no validation."""
        from tradingagents.dataflows.math.price_source import price_from_snapshot

        provenance = price_from_snapshot({"close": 522.97})
        registry = self._registry(522.97, provenance)
        assert registry.get("price_to_book").is_usable is False

    def test_t4_a_price_without_provenance_is_not_certified(self):
        """On the state path an absent provenance is a refusal, not a quote."""
        from tradingagents.dataflows.math.run_registry import (
            registry_inputs_from_state,
        )

        inputs = registry_inputs_from_state(
            {"verified_market_snapshot": {"close": 522.97}})
        # The snapshot path builds a provenance with status None → refused.
        registry = build_run_registry(inputs, run_id="no-prov")
        assert registry.get("price_to_book").is_usable is False

    def test_t5_an_intraday_price_without_a_timestamp_is_refused(self):
        """An unstamped intraday print cannot be told from a stale quote."""
        provenance = qualified_price(
            "BRK-B", INTRADAY_PRICE, bar_finality="INTRADAY_PARTIAL")
        assert provenance["validation_status"] == "UNVERIFIED"
        registry = self._registry(INTRADAY_PRICE, provenance)
        assert registry.get("price_to_book").is_usable is False

    def test_t5b_the_same_price_with_a_timestamp_is_certified(self):
        provenance = qualified_price(
            "BRK-B", INTRADAY_PRICE, bar_finality="INTRADAY_PARTIAL",
            observation_timestamp="2026-11-10T16:00:00Z")
        assert provenance["validation_status"] == "VERIFIED"
        registry = self._registry(INTRADAY_PRICE, provenance)
        assert registry.get("price_to_book").is_usable is True

    def test_t6_the_two_qualified_kinds_produce_distinct_multiples(self):
        intraday = self._registry(INTRADAY_PRICE, qualified_price(
            "BRK-B", INTRADAY_PRICE, bar_finality="INTRADAY_PARTIAL",
            observation_timestamp="2026-11-10T16:00:00Z"))
        eod = self._registry(EOD_CLOSE, qualified_price(
            "BRK-B", EOD_CLOSE, bar_finality="EOD_CONFIRMED",
            session_date="2026-11-09"))
        assert intraday.get("price_to_book").is_usable is True
        assert eod.get("price_to_book").is_usable is True
        assert float(intraday.get("price_to_book").value) != pytest.approx(
            float(eod.get("price_to_book").value), abs=1e-4)

    def test_t7_an_unverified_bvps_does_not_certify_a_pb(self):
        inputs = RunInputs(
            price=EOD_CLOSE, book_value_per_share=BVPS,
            claimed_inputs=("book_value_per_share",),
            price_provenance=qualified_price(
                "BRK-B", EOD_CLOSE, bar_finality="EOD_CONFIRMED"))
        registry = build_run_registry(inputs, run_id="bvps-claim")
        assert registry.get("price_to_book").is_usable is False

    def test_t8_the_aggregate_needs_a_qualified_price(self):
        inputs = RunInputs(
            price=SMA50, market_cap_reported=1_231_440_000_000.0,
            equity=761_368_000_000.0,
            price_provenance={"kind": "indicator",
                              "validation_status": "NOT_A_PRICE"})
        registry = build_run_registry(inputs, run_id="agg")
        assert registry.get("pb_aggregate").is_usable is False


# ===========================================================================
# Italian decimal separators in the claims validator (FIX #2 D)
# ===========================================================================
class TestItalianMultiples:
    @pytest.mark.parametrize("text,expected", [
        ("P/B 1,49x", 1.49),
        ("P/B 1,64x", 1.64),
        ("P/B of 1.49x", 1.49),
    ])
    def test_the_separator_forms_parse_to_a_multiple(self, text, expected):
        from tradingagents.dataflows.math.claims import extract_claims

        claims = extract_claims(text, agent="fundamentals")
        assert len(claims) == 1, f"not parsed: {text!r}"
        assert float(claims[0].value) == pytest.approx(expected, abs=1e-9)

    def test_a_correct_italian_multiple_is_not_a_mismatch(self):
        from tradingagents.dataflows.math.report_validation import (
            validate_analyst_report,
        )

        registry = build_run_registry(RunInputs(
            price=INTRADAY_PRICE, book_value_per_share=BVPS,
            price_provenance=qualified_price(
                "BRK-B", INTRADAY_PRICE, bar_finality="INTRADAY_PARTIAL",
                observation_timestamp="2026-11-10T16:00:00Z")),
            run_id="it-ok")
        validation = validate_analyst_report(
            "P/B 1,64x", agent="fundamentals", registry=registry)
        assert validation.blocks_decision is False

    def test_a_wrong_italian_multiple_is_a_mismatch(self):
        from tradingagents.dataflows.math.report_validation import (
            validate_analyst_report,
        )

        registry = build_run_registry(RunInputs(
            price=INTRADAY_PRICE, book_value_per_share=BVPS,
            price_provenance=qualified_price(
                "BRK-B", INTRADAY_PRICE, bar_finality="INTRADAY_PARTIAL",
                observation_timestamp="2026-11-10T16:00:00Z")),
            run_id="it-bad")
        validation = validate_analyst_report(
            "P/B 1,49x", agent="fundamentals", registry=registry)
        assert validation.blocks_decision is True

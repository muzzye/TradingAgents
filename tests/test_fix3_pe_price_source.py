"""FIX #3 — a trailing P/E may not price an indicator either.

FIX #2 established that a valuation multiple needs a *market price* as its
numerator, and enforced it in ``_register_dual_class_valuation``. The trailing
P/E is a valuation multiple built from the same ``inputs.price`` but lives
outside that path, so the guard never applied to it.

The result was a registry that contradicted itself in a single table: the P/B
was refused because "the numerator is not a market price" while the P/E, from
the identical numerator, came out `certified`. An analyst could then quote the
SMA50-derived P/E and the validator would agree with it, because the certified
metric it compares against was itself built on the indicator.

The figures here are synthetic. They are small, round and chosen only to make
the arithmetic checkable — none of them is a claim about a real security.
"""

from __future__ import annotations

import pytest

from tests.fixtures import verified_price
from tradingagents.dataflows.math.metric_record import (
    CalculationStatus,
    SourceStatus,
)

# A single-class filer: no equity, no share counts, so the dual-class path is
# never entered and the P/E is the only valuation multiple this run can build.
# That is what makes the defect visible in isolation rather than masked by a
# refused P/B that blocks the gate anyway.
INDICATOR_PRICE = 522.97          # a 50-period moving average offered as the price
EPS_TTM = 6.75
WEIGHTED_SHARES = 15_000_000_000.0
EXPECTED_PE = INDICATOR_PRICE / EPS_TTM      # 77.4770…


def _build(provenance, *, price=INDICATOR_PRICE, eps=EPS_TTM):
    from tradingagents.dataflows.math.run_registry import (
        RunInputs,
        build_run_registry,
    )

    return build_run_registry(
        RunInputs(
            price=price, price_provenance=provenance,
            eps_ttm=eps, weighted_average_shares=WEIGHTED_SHARES,
        ),
        run_id="fix3",
    )


class TestPeRefusesANonPrice:
    """The numerator rule must hold for every valuation multiple, not one."""

    def test_an_indicator_is_not_a_pe_numerator(self):
        """An indicator's provenance refuses the P/E, as it refuses the P/B."""
        from tradingagents.dataflows.math.price_source import indicator_as_price

        registry = _build(indicator_as_price("sma50", INDICATOR_PRICE, "AAPL"))
        record = registry.get("pe_trailing")
        assert record is not None
        assert record.is_usable is False
        assert record.calculation_status is CalculationStatus.REFUSED
        assert record.value is None

    def test_a_price_with_no_provenance_is_not_a_pe_numerator(self):
        """Absence of provenance is a refusal, for the same reason it is for P/B."""
        registry = _build(None)
        record = registry.get("pe_trailing")
        assert record.is_usable is False
        assert record.calculation_status is CalculationStatus.REFUSED

    def test_a_bare_close_dictionary_is_not_a_pe_numerator(self):
        """`{"close": 522.97}` states no kind and no status, so it prices nothing."""
        registry = _build({"close": INDICATOR_PRICE})
        record = registry.get("pe_trailing")
        assert record.is_usable is False
        assert record.calculation_status is CalculationStatus.REFUSED

    def test_a_kindless_provenance_is_not_a_pe_numerator(self):
        """A VERIFIED status with no `kind` still does not say it is a price."""
        registry = _build({"value": INDICATOR_PRICE, "validation_status": "VERIFIED"})
        record = registry.get("pe_trailing")
        assert record.is_usable is False
        assert record.calculation_status is CalculationStatus.REFUSED

    def test_the_refusal_names_the_numerator(self):
        """The reason must say *why*, not merely that the metric is absent."""
        from tradingagents.dataflows.math.price_source import indicator_as_price

        registry = _build(indicator_as_price("sma50", INDICATOR_PRICE, "AAPL"))
        reason = registry.get("pe_trailing").reason
        assert "price" in reason.lower()
        assert "indicator" in reason.lower() or "market price" in reason.lower()


class TestTheQualifiedPathStillWorks:
    """The guard must not break a P/E built from a real quote."""

    def test_a_qualified_close_certifies_the_pe(self):
        registry = _build(verified_price(INDICATOR_PRICE))
        record = registry.get("pe_trailing")
        assert record.is_usable is True
        assert record.calculation_status is CalculationStatus.VERIFIED
        assert record.source_status is SourceStatus.VERIFIED
        assert float(record.value) == pytest.approx(EXPECTED_PE, abs=1e-3)

    def test_the_formula_and_operands_are_recorded(self):
        registry = _build(verified_price(INDICATOR_PRICE))
        record = registry.get("pe_trailing")
        assert record.formula_id == "price_div_eps_trailing_gaap"
        assert float(record.operands["price"]) == pytest.approx(INDICATOR_PRICE)
        assert float(record.operands["weighted_average_shares"]) == pytest.approx(
            WEIGHTED_SHARES)

    def test_an_unreconciled_eps_still_halts_the_pe(self):
        """§4 of P3 is untouched: no weighted-average denominator, no P/E."""
        from tradingagents.dataflows.math.run_registry import (
            RunInputs,
            build_run_registry,
        )

        registry = build_run_registry(
            RunInputs(price=INDICATOR_PRICE,
                      price_provenance=verified_price(INDICATOR_PRICE),
                      eps_ttm=EPS_TTM),
            run_id="fix3-noeps")
        assert registry.get("pe_trailing").is_usable is False


class TestTheRegistryNoLongerContradictsItself:
    """One table, one rule: the same numerator cannot be both refused and certified."""

    def test_pb_and_pe_agree_about_the_numerator(self):
        from tradingagents.dataflows.math.price_source import indicator_as_price
        from tradingagents.dataflows.math.run_registry import (
            RunInputs,
            build_run_registry,
        )

        registry = build_run_registry(
            RunInputs(
                price=INDICATOR_PRICE,
                price_provenance=indicator_as_price("sma50", INDICATOR_PRICE, "BRK-B"),
                book_value_per_share=352.0,
                eps_ttm=EPS_TTM, weighted_average_shares=WEIGHTED_SHARES,
            ),
            run_id="fix3-both",
        )
        pb = registry.get("price_to_book")
        pe = registry.get("pe_trailing")
        assert pb.is_usable is False, "P/B already refused the indicator"
        assert pe.is_usable is False, "the P/E must refuse it too"

    def test_the_certified_table_shows_no_price_derived_multiple(self):
        """The human-readable table is where the contradiction was visible."""
        from tradingagents.dataflows.certified_report import render_metric_table
        from tradingagents.dataflows.math.price_source import indicator_as_price

        registry = _build(indicator_as_price("sma50", INDICATOR_PRICE, "AAPL"))
        table = render_metric_table(registry)
        pe_row = next(line for line in table.splitlines()
                      if line.startswith("| `pe_trailing`"))
        assert "certified" not in pe_row
        assert "refused" in pe_row

    def test_the_report_has_no_certified_multiple_to_quote(self):
        """With the P/E refused, neither valuation multiple is quotable.

        The report validator extracts P/B claims but not P/E ones, so this fix
        does not by itself make an analyst's P/E claim block — that extraction
        gap is separate and is not claimed as solved here. What this fix
        guarantees is the narrower, checkable thing: there is no certified P/E
        for a report to rest on, so the number is not a fact in the audit.
        """
        from tradingagents.dataflows.math.price_source import indicator_as_price

        registry = _build(indicator_as_price("sma50", INDICATOR_PRICE, "AAPL"))
        certified = [r.metric for r in registry._records.values()  # noqa: SLF001
                     if r.is_usable and r.unit == "multiple"]
        assert certified == [], (
            f"a multiple was certified from an indicator: {certified}")


class TestNoCollateralDamage:
    """Metrics that use the price as a level, not as a valuation, are untouched."""

    def test_session_return_still_computes_from_any_price(self):
        from tradingagents.dataflows.math.price_source import indicator_as_price
        from tradingagents.dataflows.math.run_registry import (
            RunInputs,
            build_run_registry,
        )

        registry = build_run_registry(
            RunInputs(price=INDICATOR_PRICE,
                      price_provenance=indicator_as_price("sma50", INDICATOR_PRICE, "AAPL"),
                      previous_close=520.0),
            run_id="fix3-return")
        record = registry.get("session_return")
        assert record is not None
        assert record.value is not None, "a return is a level comparison, not a valuation"

    def test_distance_from_a_moving_average_is_still_a_level_comparison(self):
        from tradingagents.dataflows.math.price_source import indicator_as_price
        from tradingagents.dataflows.math.run_registry import (
            RunInputs,
            build_run_registry,
        )

        registry = build_run_registry(
            RunInputs(price=INDICATOR_PRICE, sma200=500.0,
                      price_provenance=indicator_as_price("sma50", INDICATOR_PRICE, "AAPL")),
            run_id="fix3-distance")
        record = registry.get("distance_from_sma200")
        assert record is not None
        assert record.value is not None


class TestFix2AndFix1AreUntouched:
    """The two integrated fixes must behave exactly as before."""

    def test_a_class_b_price_over_a_dividend_discount_model_still_certifies(self):
        """FIX #2's verified path: qualified price, coherent accounting."""
        from tests.fixtures import verified_accounting
        from tradingagents.dataflows.math.run_registry import (
            RunInputs,
            build_run_registry,
        )

        registry = build_run_registry(
            RunInputs(
                price=575.25, price_provenance=verified_price(575.25),
                book_value_per_share=342.1879,
                equity=761_368_000_000.0, equity_period_end="2026-09-30",
                shares_class_a=550_000.0, shares_class_b=1_400_000_000.0,
                class_a_to_b_ratio=1500.0,
                equity_provenance=verified_accounting(),
            ),
            run_id="fix3-pb-ok",
        )
        assert registry.get("price_to_book").is_usable is True

    def test_sec_revenue_is_unaffected_by_the_pe_path(self):
        """FIX #1's revenue path never touches the price or the EPS."""
        from tradingagents.dataflows.math.run_registry import RunInputs, build_run_registry

        registry = build_run_registry(
            RunInputs(revenue_consolidated=105_647_000_000.0,
                      revenue_prior=94_972_000_000.0,
                      revenue_provenance={"validation_status": "VERIFIED",
                                          "concept": "Revenues",
                                          "period_end": "2026-09-30"}),
            run_id="fix3-rev")
        assert registry.get("consolidated_revenue").is_usable is True
        assert float(registry.get("revenue_growth").value) == pytest.approx(11.24, abs=0.02)


class TestTheProductionPathRefusesIt:
    """The real path: a finished run's state, read by the graph's own builder.

    Asserting on `RunInputs` alone would not prove the guard reaches production,
    because the state→inputs mapping is where a provenance can be lost. This
    goes through `registry_inputs_from_state`, the function the graph calls.
    """

    def test_state_carrying_an_indicator_certifies_no_multiple(self):
        from tradingagents.dataflows.math.price_source import indicator_as_price
        from tradingagents.dataflows.math.run_registry import (
            build_run_registry,
            registry_inputs_from_state,
        )

        state = {
            "company_of_interest": "AAPL", "trade_date": "2026-11-10",
            "verified_market_snapshot": {
                "close": INDICATOR_PRICE,
                "price_provenance": indicator_as_price("sma50", INDICATOR_PRICE, "AAPL"),
            },
            "eps_ttm": EPS_TTM,
            "weighted_average_shares": WEIGHTED_SHARES,
        }
        registry = build_run_registry(
            registry_inputs_from_state(state), run_id="fix3-prod")
        pe = registry.get("pe_trailing")
        assert pe.is_usable is False
        assert pe.calculation_status is CalculationStatus.REFUSED

    def test_the_graph_builder_agrees(self, tmp_path):
        """`_build_metric_registry` is what a real run calls."""
        from tradingagents.dataflows.math.price_source import indicator_as_price
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        graph = object.__new__(TradingAgentsGraph)
        graph.config = {
            "results_dir": str(tmp_path),
            "deep_think_llm": "m", "quick_think_llm": "m",
            "fundamentals_freshness_gate": False,
            "risk_per_trade_pct": 0.01, "max_symbol_exposure": 0.05,
        }
        graph.selected_analysts = []

        state = {
            "company_of_interest": "AAPL", "trade_date": "2026-11-10",
            "verified_market_snapshot": {
                "close": INDICATOR_PRICE,
                "price_provenance": indicator_as_price("sma50", INDICATOR_PRICE, "AAPL"),
            },
            "eps_ttm": EPS_TTM,
            "weighted_average_shares": WEIGHTED_SHARES,
        }
        registry = graph._build_metric_registry(  # noqa: SLF001
            "AAPL", "2026-11-10", state)
        assert registry is not None
        assert registry.get("pe_trailing").is_usable is False

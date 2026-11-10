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

    def test_a_reported_pe_claim_is_not_extracted_so_it_cannot_block(self):
        """The limitation, asserted so the code comment cannot drift from it.

        `run_registry.py` documents that this fix does not make a reported P/E
        claim block, because the validator extracts P/B claims but not P/E
        ones. A comment is not evidence; this test is. If claim extraction is
        later extended to P/E, this test fails and the comment must be updated
        with it.
        """
        from tradingagents.dataflows.math.price_source import indicator_as_price
        from tradingagents.dataflows.math.report_validation import (
            validate_analyst_report,
        )

        registry = _build(indicator_as_price("sma50", INDICATOR_PRICE, "AAPL"))
        for text in ("P/E of 77.48x.", "P/E 77,48x.", "trailing P/E 77.48x"):
            validation = validate_analyst_report(
                text, agent="fundamentals", registry=registry)
            extracted = len(validation.claims.results) if validation.claims else 0
            assert extracted == 0, (
                f"{text!r} is now extracted as a P/E claim; the known-limitation "
                "note in run_registry.py and the CHANGELOG are now out of date")
            assert validation.blocks_decision is False


class TestNoCollateralDamage:
    """Indicators stay usable *as indicators*, and unrelated metrics are untouched.

    The moving averages, the ATR and the distance levels remain valid technical
    data — what they must not do is stand in for a spot price. These tests
    assert the second half: the indicator inputs themselves still reach the
    metrics that consume them, so the fix did not amputate the technical layer
    along with the defect.
    """

    def test_the_moving_averages_are_still_available_as_levels(self):
        """SMA50 and SMA200 remain readable inputs, whatever the price is."""
        from tradingagents.dataflows.math.price_source import indicator_as_price
        from tradingagents.dataflows.math.run_registry import (
            RunInputs,
        )

        inputs = RunInputs(
            price=INDICATOR_PRICE, previous_close=520.0,
            sma50=INDICATOR_PRICE, sma200=500.0, period_high=560.0,
            price_provenance=indicator_as_price("sma50", INDICATOR_PRICE, "AAPL"),
        )
        assert inputs.sma50 == INDICATOR_PRICE
        assert inputs.sma200 == 500.0
        assert inputs.period_high == 560.0

    def test_a_qualified_price_restores_every_price_derived_metric(self):
        """The refuse is about the numerator, not about the formula."""
        from tradingagents.dataflows.math.price_source import qualified_price
        from tradingagents.dataflows.math.run_registry import (
            RunInputs,
            build_run_registry,
        )

        registry = build_run_registry(
            RunInputs(
                price=INDICATOR_PRICE, previous_close=520.0,
                sma50=INDICATOR_PRICE, sma200=500.0, period_high=560.0,
                price_provenance=qualified_price(
                    "AAPL", INDICATOR_PRICE, bar_finality="EOD_CONFIRMED"),
            ),
            run_id="fix3-qualified-levels",
        )
        for name in ("session_return", "distance_from_sma50",
                     "distance_from_sma200", "distance_from_high"):
            record = registry.get(name)
            assert record is not None, name
            assert record.is_usable is True, f"{name}: {record.reason}"
            assert record.value is not None, name

    def test_eps_reconciliation_is_independent_of_the_price(self):
        """The GAAP EPS divides net income by shares; no price is involved."""
        from tradingagents.dataflows.math.price_source import indicator_as_price
        from tradingagents.dataflows.math.run_registry import (
            RunInputs,
            build_run_registry,
        )

        registry = build_run_registry(
            RunInputs(
                price=INDICATOR_PRICE,
                price_provenance=indicator_as_price("sma50", INDICATOR_PRICE, "AAPL"),
                eps_ttm=EPS_TTM, weighted_average_shares=WEIGHTED_SHARES,
            ),
            run_id="fix3-eps",
        )
        assert registry.get("eps_gaap").is_usable is True


class TestAPriceDerivedMetricNeedsAMarketPrice:
    """`session_return` and the distances are price-derived, so they need a price.

    A return is the change in a *security's* price between two sessions; a
    distance is how far that price sits from a level. Feeding either an
    indicator produces a number that looks like market information and is not:
    "the stock rose 0.56%" computed from a moving average is a fabricated
    observation. These metrics take the same numerator rule as the multiples.
    """

    @pytest.mark.parametrize("metric", [
        "session_return",
        "distance_from_sma50",
        "distance_from_sma200",
        "distance_from_high",
    ])
    def test_an_indicator_is_not_a_price_for_them(self, metric):
        from tradingagents.dataflows.math.price_source import indicator_as_price
        from tradingagents.dataflows.math.run_registry import (
            RunInputs,
            build_run_registry,
        )

        registry = build_run_registry(
            RunInputs(
                price=INDICATOR_PRICE, previous_close=520.0,
                sma50=INDICATOR_PRICE, sma200=500.0, period_high=560.0,
                price_provenance=indicator_as_price("sma50", INDICATOR_PRICE, "AAPL"),
            ),
            run_id=f"fix3-ind-{metric}",
        )
        record = registry.get(metric)
        assert record is not None
        assert record.value is None, f"{metric} published a number from an indicator"
        assert record.is_usable is False
        assert record.calculation_status is CalculationStatus.REFUSED
        assert "price" in record.reason.lower()

    @pytest.mark.parametrize("metric", [
        "session_return",
        "distance_from_sma50",
        "distance_from_sma200",
        "distance_from_high",
    ])
    def test_a_price_without_provenance_is_refused(self, metric):
        from tradingagents.dataflows.math.run_registry import (
            RunInputs,
            build_run_registry,
        )

        registry = build_run_registry(
            RunInputs(price=INDICATOR_PRICE, previous_close=520.0,
                      sma50=INDICATOR_PRICE, sma200=500.0, period_high=560.0),
            run_id=f"fix3-noprov-{metric}",
        )
        record = registry.get(metric)
        assert record.is_usable is False
        assert record.calculation_status is CalculationStatus.REFUSED

    def test_an_absent_price_is_a_gap_not_a_refusal(self):
        """No price at all stays NOT_COMPUTABLE: nothing was offered to refuse."""
        from tradingagents.dataflows.math.run_registry import (
            RunInputs,
            build_run_registry,
        )

        registry = build_run_registry(
            RunInputs(previous_close=520.0, sma50=545.10, sma200=502.85,
                      period_high=812.35),
            run_id="fix3-absent",
        )
        for name in ("session_return", "distance_from_sma50",
                     "distance_from_sma200", "distance_from_high"):
            record = registry.get(name)
            assert record is not None, name
            assert record.calculation_status is CalculationStatus.NOT_COMPUTABLE, (
                f"{name}: an absent price is a gap, not a contradiction")
            assert record.is_usable is False

    def test_they_join_the_blocking_set(self):
        """A fabricated observation must stop an executable decision."""
        from tradingagents.dataflows.math.price_source import indicator_as_price
        from tradingagents.dataflows.math.run_registry import (
            RunInputs,
            build_run_registry,
        )

        registry = build_run_registry(
            RunInputs(price=INDICATOR_PRICE, previous_close=520.0,
                      sma50=INDICATOR_PRICE, sma200=500.0, period_high=560.0,
                      price_provenance=indicator_as_price(
                          "sma50", INDICATOR_PRICE, "AAPL")),
            run_id="fix3-blocking",
        )
        blocking = {r.metric for r in registry.blocking}
        assert {"session_return", "distance_from_sma50",
                "distance_from_sma200", "distance_from_high"} <= blocking

    def test_the_table_reports_the_refusal_not_the_number(self):
        from tradingagents.dataflows.certified_report import render_metric_table
        from tradingagents.dataflows.math.price_source import indicator_as_price
        from tradingagents.dataflows.math.run_registry import (
            RunInputs,
            build_run_registry,
        )

        registry = build_run_registry(
            RunInputs(price=INDICATOR_PRICE, previous_close=520.0,
                      sma50=INDICATOR_PRICE, sma200=500.0, period_high=560.0,
                      price_provenance=indicator_as_price(
                          "sma50", INDICATOR_PRICE, "AAPL")),
            run_id="fix3-table",
        )
        table = render_metric_table(registry)
        row = next(line for line in table.splitlines()
                   if line.startswith("| `session_return`"))
        assert "refused" in row
        assert "certified" not in row


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

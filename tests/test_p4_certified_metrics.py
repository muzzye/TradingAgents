"""P4: one certified value per metric, and a report built from the records.

The audit found F2 and F3 together: a model authored the figures, the engine
could compute them too, and nothing made the second authoritative. A report
could quote 1.7x while the engine said 1.6041x, and both read as "the P/B".

The fix is not a new engine — `dataflows/math/` already had one. It is three
things this file tests:

1. **Two P/B forms, both registered.** `market_cap / equity` and `price / BVPS`
   are separate metrics because they can legitimately disagree; the registry
   records the disagreement instead of picking one.
2. **The report renders from `MetricRecord`.** The certified table is built from
   the registry's records, never from prose, and shows the refused figures too.
3. **A stated annual rate is checked against its formula.** A quarterly 0.78%
   buyback is 3.12% annualised; "1.2%" with no formula is not the same claim.

Every constant is the task's own fixture, written down independently: equity
761,368bn, market cap 1,231.44bn, aggregate P/B 1.6174x.
"""

from __future__ import annotations

import pytest

from tradingagents.dataflows.certified_report import (
    certified_table_markdown,
    render_metric_table,
)
from tradingagents.dataflows.math.metric_record import CalculationStatus
from tradingagents.dataflows.math.period_labels import (
    AnnualizationVerdict,
    check_annualization,
)
from tradingagents.dataflows.math.run_registry import RunInputs, build_run_registry

# The task's BRK-B fixture, as constants independent of the code under test.
EQUITY = 761_368_000_000.0
MARKET_CAP = 1_231_440_000_000.0
SHARES_A = 550_000.0
SHARES_B = 1_400_000_000.0
A_TO_B = 1500.0
B_EQUIVALENT = SHARES_B + SHARES_A * A_TO_B          # 2,225,000,000
EXPECTED_PB_AGGREGATE = 1.6174
PRICE_IMPLIED = MARKET_CAP / B_EQUIVALENT            # 553.46


def _brkb_inputs(**overrides) -> RunInputs:
    base = {
        "price": PRICE_IMPLIED, "market_cap_reported": MARKET_CAP,
        "equity": EQUITY, "shares_class_a": SHARES_A,
        "shares_class_b": SHARES_B, "class_a_to_b_ratio": A_TO_B,
        "price_class": "B",
    }
    base.update(overrides)
    return RunInputs(**base)


# ===========================================================================
# §2 — the two P/B formulas
# ===========================================================================
class TestPriceToBook:
    def test_the_aggregate_pb_matches_the_fixture(self):
        """market_cap / attributable_equity = 1,231.44 / 761.368."""
        registry = build_run_registry(_brkb_inputs(), run_id="brkb")
        record = registry.get("pb_aggregate")
        assert float(record.value) == pytest.approx(EXPECTED_PB_AGGREGATE, abs=1e-4)
        assert record.formula_id == "market_cap_div_attributable_equity"
        assert record.is_usable is True

    def test_the_per_share_pb_is_its_own_metric(self):
        registry = build_run_registry(_brkb_inputs(), run_id="brkb")
        per_share = registry.get("price_to_book")
        assert per_share.formula_id == "price_div_bvps"
        assert float(per_share.value) == pytest.approx(EXPECTED_PB_AGGREGATE, abs=1e-3)

    def test_the_b_equivalent_share_count_uses_the_1500_ratio(self):
        registry = build_run_registry(_brkb_inputs(), run_id="brkb")
        bvps = registry.get("book_value_per_share_B")
        assert float(bvps.value) == pytest.approx(EQUITY / B_EQUIVALENT, rel=1e-9)
        # A wrong ratio would move this by a factor of thousands, not percent.
        wrong = build_run_registry(_brkb_inputs(class_a_to_b_ratio=1.0), run_id="x")
        assert abs(float(wrong.get("book_value_per_share_B").value)
                   / float(bvps.value) - 1) > 0.5

    def test_the_two_forms_agree_when_the_snapshot_is_consistent(self):
        registry = build_run_registry(_brkb_inputs(), run_id="brkb")
        agreement = registry.get("pb_reconciliation")
        assert agreement.calculation_status is CalculationStatus.VERIFIED
        assert float(agreement.value) < 1

    def test_a_mismatched_snapshot_is_recorded_not_averaged(self):
        """The 1.62x against 1.7x case: the task forbids certifying both."""
        registry = build_run_registry(
            _brkb_inputs(price=576.74, market_cap_reported=MARKET_CAP),
            run_id="mismatch")
        agreement = registry.get("pb_reconciliation")
        assert agreement.calculation_status is CalculationStatus.REFUSED
        assert float(agreement.value) > 1
        assert "do not rest on the same snapshot" in agreement.reason

    def test_the_vendor_figure_is_not_a_certified_pb(self):
        """0,00115x is 1.604/1500 — the A/B ratio applied backwards."""
        registry = build_run_registry(
            _brkb_inputs(price=0.00115, market_cap_reported=None, equity=EQUITY),
            run_id="vendor")
        aggregate = registry.get("pb_aggregate")
        assert abs(float(aggregate.value) / 0.00115 - 1) > 0.5

    def test_the_aggregate_form_survives_a_refused_per_share_form(self):
        """A refused figure is not a reason to hide the other form."""
        registry = build_run_registry(
            _brkb_inputs(price=576.74, book_value_per_share=0.00115,
                         price_class=None),
            run_id="mixed")
        assert registry.get("pb_aggregate") is not None
        assert registry.get("pb_reconciliation") is not None


# ===========================================================================
# §3 — EPS
# ===========================================================================
class TestEps:
    def test_an_eps_with_a_weighted_average_count_is_reconciled(self):
        registry = build_run_registry(
            _brkb_inputs(eps_ttm=39.76, weighted_average_shares=1_437_000_000.0),
            run_id="eps")
        record = registry.get("eps_gaap")
        assert record.is_usable is True
        assert record.value is not None

    def test_an_eps_without_a_denominator_is_unverified(self):
        """39,76 USD cannot be certified because Yahoo said so."""
        registry = build_run_registry(
            _brkb_inputs(eps_ttm=39.76, weighted_average_shares=None),
            run_id="eps-only")
        record = registry.get("eps_gaap")
        assert record.calculation_status is CalculationStatus.UNSUPPORTED
        assert record.is_usable is False
        assert "weighted-average" in record.reason

    def test_a_pe_built_on_an_unverified_eps_is_not_certified(self):
        registry = build_run_registry(
            _brkb_inputs(eps_ttm=39.76, weighted_average_shares=None),
            run_id="pe")
        pe = registry.get("pe_trailing")
        assert pe.is_usable is False
        assert pe.calculation_status is CalculationStatus.UNSUPPORTED

    def test_a_model_authored_eps_is_not_a_filed_fact(self):
        registry = build_run_registry(
            _brkb_inputs(eps_ttm=39.76, weighted_average_shares=1_437_000_000.0,
                         claimed_inputs=("eps_ttm",)),
            run_id="claimed")
        assert registry.get("eps_gaap").is_usable is False


# ===========================================================================
# §4 — the annualisation regression
# ===========================================================================
class TestAnnualization:
    def test_a_quarterly_buyback_annualises_to_3_12(self):
        check = check_annualization(3.12, quarterly_pct=0.78, periods_per_year=4)
        assert check.verdict is AnnualizationVerdict.CONSISTENT
        assert str(check.computed_annual_pct) == "3.1200"

    def test_1_2_percent_without_a_formula_is_not_accepted(self):
        """A rate nobody can reproduce is not a certified figure."""
        check = check_annualization(1.2, quarterly_pct=0.78)
        assert check.verdict is AnnualizationVerdict.FORMULA_MISSING
        assert check.blocks is True

    def test_1_2_percent_with_a_formula_is_contradicted(self):
        check = check_annualization(1.2, quarterly_pct=0.78, periods_per_year=4)
        assert check.verdict is AnnualizationVerdict.MISMATCH
        assert "3.1200" in check.reason

    def test_an_unknown_quarterly_figure_verifies_nothing(self):
        check = check_annualization(3.12, quarterly_pct=None, periods_per_year=4)
        assert check.verdict is AnnualizationVerdict.UNVERIFIED


# ===========================================================================
# §3 — the report is built from records, not prose
# ===========================================================================
class TestCertifiedReport:
    def test_the_table_lists_the_computed_value_from_the_record(self):
        registry = build_run_registry(_brkb_inputs(), run_id="brkb")
        table = render_metric_table(registry)
        assert "pb_aggregate" in table
        assert "1.6174" in table
        assert "market_cap_div_attributable_equity" in table

    def test_the_table_shows_the_refused_figures_too(self):
        """A table of successes only hides the gap a reader needs to see."""
        registry = build_run_registry(
            _brkb_inputs(eps_ttm=39.76, weighted_average_shares=None),
            run_id="eps")
        table = render_metric_table(registry)
        assert "eps_gaap" in table
        assert "unverified" in table or "not computable" in table

    def test_a_run_without_a_registry_says_so_instead_of_a_blank(self):
        table = certified_table_markdown({"metric_registry_status": {
            "note": "no registry was available: nothing was certified"}})
        assert "No certified metrics" in table

    def test_the_state_registry_is_what_the_report_reads(self):
        registry = build_run_registry(_brkb_inputs(), run_id="brkb")
        table = certified_table_markdown({"metric_registry": registry})
        assert "1.6174" in table

    def test_every_record_carries_a_formula_id(self):
        """The task asks for the formula each metric was produced by."""
        registry = build_run_registry(_brkb_inputs(eps_ttm=39.76,
                                                  weighted_average_shares=1.4e9),
                                      run_id="brkb")
        for record in registry._records.values():  # noqa: SLF001
            assert record.formula_id, record.metric


# ===========================================================================
# §5 — the path through the graph, up to the Portfolio Manager
# ===========================================================================
class TestPipelinePropagation:
    def test_the_registry_reaches_the_state_the_pm_reads(self, tmp_path):
        """`record_decision` attaches the registry to `final_state`."""
        from tests.test_p1_runtime_gate_integration import _graph, _state

        graph = _graph(tmp_path)
        state = _state("P/B of 1.6040x.")
        state["verified_market_snapshot"] = {"close": 576.74, "sma200": 502.85,
                                             "previous_close": 570.89,
                                             "price_provenance": {"kind": "intraday", "value": 576.74, "currency": "USD", "share_class": "B", "timestamp": "2026-11-09T17:37:00Z", "session_date": "2026-11-09", "source": "yfinance", "bar_finality": "INTRADAY_PARTIAL", "validation_status": "VERIFIED"}}
        graph.record_decision("BRK-B", "2026-11-09", state)

        # The Portfolio Manager is the last node; its state is what this is.
        assert "metric_registry" in state, (
            "the registry must be on the state the PM's decision is attached to")
        registry = state["metric_registry"]
        assert registry.get("consolidated_revenue") is not None
        assert state["metric_registry_status"]["metrics_total"] >= 1

    def test_an_invalid_metric_blocks_an_executable_decision(self, tmp_path):
        from tests.test_p1_runtime_gate_integration import _graph, _state

        graph = _graph(tmp_path)
        state = _state("P/B of 1.6040x.")
        # No snapshot and no metric fields: nothing is certifiable.
        graph.record_decision("BRK-B", "2026-11-09", state)
        assert state["execution_authorization"] == "BLOCKED"
        assert "NO_CERTIFIED_METRIC" in state.get("execution_reason_codes", [])

    def test_a_valid_run_produces_a_report_with_the_certified_table(self, tmp_path):
        from tests.test_p1_runtime_gate_integration import _graph, _state
        from tradingagents.reporting import write_report_tree

        graph = _graph(tmp_path)
        state = _state("P/B of 1.6040x.")
        state["verified_market_snapshot"] = {"close": 576.74, "sma200": 502.85,
                                             "previous_close": 570.89,
                                             "price_provenance": {"kind": "intraday", "value": 576.74, "currency": "USD", "share_class": "B", "timestamp": "2026-11-09T17:37:00Z", "session_date": "2026-11-09", "source": "yfinance", "bar_finality": "INTRADAY_PARTIAL", "validation_status": "VERIFIED"}}
        graph.record_decision("BRK-B", "2026-11-09", state)

        report = write_report_tree(state, "BRK-B", tmp_path / "report",
                                   html=False).read_text(encoding="utf-8")
        assert "## Certified metrics" in report
        assert "pb_aggregate" in report

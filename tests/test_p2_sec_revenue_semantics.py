"""P2 Task 2: SEC revenue semantics, from the filing to the certified metric.

The data lineage audit found F1: Berkshire's Q3 2026 revenue was reported as
74,322 when the filing also states 105,647 for the same quarter. Both are true —
74,322 is `RevenueFromContractWithCustomerExcludingAssessedTax`, a *component*,
and 105,647 is `Revenues`, the consolidated total.

The resolver already ordered tags by meaning. Two gaps remained, and both are
closed here:

1. **A row could mix scopes across periods.** A filer reporting the consolidated
   total for the latest quarter and only a component for the prior one produced
   a row whose two periods measured different things; the growth across them
   read 76.08% where the consolidated history says 11.24%.
2. **Nothing carried the provenance into the certified metric.** The XBRL
   concept, accession, period, unit, scale and validation status stopped at the
   vendor boundary, so a component could enter the registry looking like a total.

Every expected value is a constant in `tests/fixtures/sec_revenue_payloads.py`,
copied from the shape of the real payload — not computed by the code under test.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from tests.fixtures import sec_revenue_payloads as F
from tradingagents.dataflows.math.run_registry import RunInputs, build_run_registry
from tradingagents.dataflows.vendors import sec_edgar
from tradingagents.dataflows.vendors.concept_resolution import (
    RevenueScope,
    scope_of,
)

SPANS = ((80, 100),)


def _read(facts: dict, tags: tuple[str, ...], as_of: str = F.ANALYSIS_DATE):
    return sec_edgar._as_of_with_provenance(facts, tags, as_of, SPANS)


# ===========================================================================
# §4.1 — the consolidated figure, and the growth computed from it
# ===========================================================================
class TestConsolidatedRevenue:
    def test_both_quarters_are_the_consolidated_total(self):
        values, unit, provenance = _read(F.BOTH_SCOPES, (F.CONTRACT, F.CONSOLIDATED))
        assert values[("2026-09-30", 0)] == F.REVENUE_Q3_2026
        assert values[("2025-09-30", 0)] == F.REVENUE_Q3_2025
        assert unit == "USD"
        for key in values:
            assert provenance[key].concept == F.CONSOLIDATED
            assert provenance[key].scope is RevenueScope.CONSOLIDATED
            assert provenance[key].validation_status == "VERIFIED"

    def test_the_growth_is_11_24_percent(self):
        values, _, _ = _read(F.BOTH_SCOPES, (F.CONTRACT, F.CONSOLIDATED))
        growth = (Decimal(values[("2026-09-30", 0)])
                  / Decimal(values[("2025-09-30", 0)]) - 1) * 100
        assert growth == pytest.approx(Decimal(str(F.EXPECTED_GROWTH_PCT)),
                                       abs=Decimal("0.01"))

    def test_the_tag_order_does_not_decide_the_value(self):
        """F1 itself: listing the component first must change nothing."""
        for order in ((F.CONTRACT, F.CONSOLIDATED), (F.CONSOLIDATED, F.CONTRACT)):
            values, _, provenance = _read(F.BOTH_SCOPES, order)
            assert values[("2026-09-30", 0)] == F.REVENUE_Q3_2026
            assert provenance[("2026-09-30", 0)].scope is RevenueScope.CONSOLIDATED


# ===========================================================================
# §4.2 — the contract figure is preserved, separately
# ===========================================================================
class TestContractRevenuePreserved:
    def test_the_component_keeps_its_identity(self):
        values, _, provenance = _read(F.ONLY_CONTRACT, (F.CONTRACT,))
        record = provenance[("2026-09-30", 0)]
        assert values[("2026-09-30", 0)] == F.REVENUE_Q3_2026_CONTRACT
        assert record.scope is RevenueScope.CONTRACT
        assert record.is_consolidated is False
        assert record.validation_status == "UNVERIFIED"

    def test_the_component_is_never_promoted_to_the_total(self):
        """The header guard: a subset is reported, never certified."""
        _, _, provenance = _read(F.ONLY_CONTRACT, (F.CONTRACT,))
        assert provenance[("2026-09-30", 0)].validation_status == "UNVERIFIED"


# ===========================================================================
# §4.3 — the row does not mix scopes across periods
# ===========================================================================
class TestScopeCoherencePerRow:
    def test_a_component_does_not_fill_a_gap_beside_a_consolidated_period(self):
        """The 76.08% defect: consolidated for the new quarter, component for the old.

        Both tags report Q3 2026 here, so the component is a component of this
        row and may not supply Q3 2025 — even though it is the only tag that has
        a figure for that period.
        """
        values, _, provenance = _read(F.CONSOLIDATED_NEW_QUARTER_ONLY,
                                      (F.CONTRACT, F.CONSOLIDATED))
        assert values == {("2026-09-30", 0): F.REVENUE_Q3_2026}
        assert provenance[("2026-09-30", 0)].scope is RevenueScope.CONSOLIDATED

    def test_a_renamed_line_keeps_its_whole_history(self):
        """Apple-shaped: the scopes never overlap, so both are the row's history.

        `RevenueFromContractWithCustomerExcludingAssessedTax` is Apple's whole top
        line; it is Berkshire's component only because `Revenues` also reports
        the same period. Without an overlap it is the row.
        """
        values, _, _ = _read(F.RENAMED_OVER_TIME, (F.SALES_NET, F.CONSOLIDATED))
        assert values[("2026-09-30", 0)] == F.REVENUE_Q3_2026
        assert values[("2025-09-30", 0)] == F.REVENUE_Q3_2025


# ===========================================================================
# §4.4 — alternative tags and unfamiliar taxonomies
# ===========================================================================
class TestAlternativeTags:
    def test_an_unknown_concept_is_not_presented_as_consolidated(self):
        _, _, provenance = _read(F.UNKNOWN_TAXONOMY,
                                 ("RevenuesFromOperationsAndOtherStuff",))
        record = provenance[("2026-09-30", 0)]
        assert record.scope is RevenueScope.UNKNOWN
        assert record.validation_status == "UNVERIFIED"

    def test_a_segment_fact_is_not_the_entity_total(self):
        _, _, provenance = _read(F.SEGMENT_ONLY, (F.SEGMENT,))
        record = provenance[("2026-09-30", 0)]
        assert record.scope is RevenueScope.SEGMENT
        assert record.validation_status == "UNVERIFIED"


# ===========================================================================
# §4.5 — periods: quarterly against year to date
# ===========================================================================
class TestPeriods:
    def test_the_quarter_is_not_read_as_the_year_to_date(self):
        """One 10-Q states both under one end date; the quarter is index 0."""
        values, _, _ = _read(F.QUARTER_AND_YTD, (F.CONSOLIDATED,))
        assert values[("2026-09-30", 0)] == F.REVENUE_Q3_2026
        assert F.REVENUE_YTD_2026 not in values.values()


# ===========================================================================
# §4.6 — duplicated facts, amendments, staleness, absence
# ===========================================================================
class TestFiledFacts:
    def test_duplicate_facts_of_one_period_yield_one_value(self):
        values, _, _ = _read(F.DUPLICATED_FACTS, (F.CONSOLIDATED,))
        assert values == {("2026-09-30", 0): F.REVENUE_Q3_2026}

    def test_an_amendment_counts_from_the_day_it_was_filed(self):
        before, _, _ = _read(F.AMENDED_LATER, (F.CONSOLIDATED,), as_of="2026-10-30")
        after, _, _ = _read(F.AMENDED_LATER, (F.CONSOLIDATED,), as_of="2026-11-09")
        assert before[("2026-09-30", 0)] == 100_000_000_000.0
        assert after[("2026-09-30", 0)] == F.REVENUE_Q3_2026

    def test_nothing_filed_yields_nothing(self):
        values, _, _ = _read(F.EMPTY, (F.CONSOLIDATED,))
        assert values == {}


# ===========================================================================
# §4.7 — the provenance travels with the figure
# ===========================================================================
class TestProvenanceContract:
    def test_every_field_the_task_asks_for_is_present(self):
        _, _, provenance = _read(F.BOTH_SCOPES, (F.CONSOLIDATED,))
        summary = provenance[("2026-09-30", 0)].summary()
        for field in ("concept", "cik", "accession", "period_start", "period_end",
                      "unit", "scale", "origin", "available_at",
                      "validation_status", "consolidation_perimeter"):
            assert field in summary, field
        assert summary["concept"] == F.CONSOLIDATED
        assert summary["accession"] == F.ACCESSION
        assert summary["period_end"] == "2026-09-30"
        assert summary["unit"] == "USD"
        assert summary["validation_status"] == "VERIFIED"
        assert summary["consolidation_perimeter"] == "consolidated"

    def test_the_cik_is_recorded_when_the_caller_knows_it(self):
        _, _, provenance = sec_edgar._as_of_with_provenance(
            F.BOTH_SCOPES, (F.CONSOLIDATED,), F.ANALYSIS_DATE, SPANS, cik=F.CIK)
        assert provenance[("2026-09-30", 0)].cik == F.CIK

    def test_the_available_at_is_the_filing_date(self):
        _, _, provenance = _read(F.BOTH_SCOPES, (F.CONSOLIDATED,))
        assert provenance[("2026-09-30", 0)].available_at == "2026-10-25"


# ===========================================================================
# §2 — the certified metric, and the refusal of a component
# ===========================================================================
class TestCertifiedMetric:
    def test_a_filed_consolidated_total_is_certified(self):
        registry = build_run_registry(RunInputs(
            revenue_consolidated=F.REVENUE_Q3_2026,
            revenue_prior=F.REVENUE_Q3_2025,
            revenue_provenance={
                "concept": F.CONSOLIDATED, "cik": F.CIK,
                "period_end": "2026-09-30", "validation_status": "VERIFIED"},
        ), run_id="brkb-q3")
        metric = registry.get("consolidated_revenue")
        assert metric.value == F.REVENUE_Q3_2026
        assert metric.is_usable is True
        assert metric.period_end == "2026-09-30"
        assert float(round(registry.get("revenue_growth").value, 2)) == F.EXPECTED_GROWTH_PCT

    def test_an_unverified_component_cannot_be_certified(self):
        """The F1 promotion, refused: 74,322 is recorded and not usable."""
        registry = build_run_registry(RunInputs(
            revenue_consolidated=F.REVENUE_Q3_2026_CONTRACT,
            revenue_prior=F.REVENUE_Q3_2025,
            revenue_provenance={
                "concept": F.CONTRACT, "cik": F.CIK,
                "period_end": "2026-09-30", "validation_status": "UNVERIFIED"},
        ), run_id="brkb-contract")
        metric = registry.get("consolidated_revenue")
        assert metric.value == F.REVENUE_Q3_2026_CONTRACT   # recorded, not hidden
        assert metric.is_usable is False                      # and never certified

    def test_a_model_authored_revenue_is_not_a_filed_fact(self):
        registry = build_run_registry(RunInputs(
            revenue_consolidated=F.REVENUE_Q3_2026,
            claimed_inputs=("revenue_consolidated",),
        ), run_id="prose")
        assert registry.get("consolidated_revenue").is_usable is False


# ===========================================================================
# §5 — from the filing to the decision, with the models mocked out
# ===========================================================================
class TestEndToEnd:
    def test_the_metric_reaches_the_gate_certified(self):
        """SEC facts -> registry -> the gate's own registry. No LLM involved."""
        values, _, provenance = _read(F.BOTH_SCOPES, (F.CONTRACT, F.CONSOLIDATED))
        record = provenance[("2026-09-30", 0)].summary()

        registry = build_run_registry(RunInputs(
            revenue_consolidated=values[("2026-09-30", 0)],
            revenue_prior=values[("2025-09-30", 0)],
            revenue_provenance=record,
        ), run_id="e2e")

        metric = registry.get("consolidated_revenue")
        assert metric.value == F.REVENUE_Q3_2026
        assert metric.is_usable is True
        assert metric.formula_id == "sec_filed_fact"
        assert float(round(registry.get("revenue_growth").value, 2)) == F.EXPECTED_GROWTH_PCT

    def test_the_component_never_becomes_the_certified_revenue(self):
        """The same path with only the component filed: no usable metric."""
        values, _, provenance = _read(F.ONLY_CONTRACT, (F.CONTRACT,))
        registry = build_run_registry(RunInputs(
            revenue_consolidated=values[("2026-09-30", 0)],
            revenue_provenance=provenance[("2026-09-30", 0)].summary(),
        ), run_id="e2e-contract")
        assert registry.get("consolidated_revenue").is_usable is False
        assert registry.get("revenue_growth").is_usable is False

    def test_the_scope_vocabulary_the_task_asks_for(self):
        assert scope_of(F.CONSOLIDATED) is RevenueScope.CONSOLIDATED
        assert scope_of(F.CONTRACT) is RevenueScope.CONTRACT
        assert scope_of(F.SEGMENT) is RevenueScope.SEGMENT
        assert scope_of(F.NONCONTRACT) is RevenueScope.COMPONENT
        assert scope_of("AnUnmappedConcept") is RevenueScope.UNKNOWN

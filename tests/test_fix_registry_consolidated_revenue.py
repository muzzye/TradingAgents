"""FIX #1 — `consolidated_revenue` must come from the SEC filing, not from prose.

The 2026-11-10 16:15 BRK-B report stated Q3 2026 revenue of 105,647 million
against 94,972 million for Q3 2025 (+11.24%), while the Certified Metrics table
showed::

    consolidated_revenue: NOT_COMPUTABLE
    reason: "no consolidated revenue was filed or reported"

Both cannot be true. The root cause is that `RunInputs.revenue_consolidated` was
populated **only** by `math/extraction.py`, which reads a labelled sentence out
of the fundamentals report with a regex. The SEC facts that the Fundamentals
Analyst fetched — the structured `Revenues` fact with its accession, period and
unit — never reached the registry at all.

So the metric exists and is correct whenever the model happens to phrase the
figure as the regex expects, and is NOT_COMPUTABLE otherwise. A figure that a
filing states authoritatively was being made conditional on how a language model
chose to word it.

Every test here is deterministic: frozen SEC payloads, no network, no model.
"""

from __future__ import annotations

import pytest

from tests.fixtures import sec_revenue_payloads as F
from tradingagents.dataflows.math.run_registry import RunInputs, build_run_registry

pytestmark = pytest.mark.unit

# The consolidated figure and the component the defective extractor preferred.
EXPECTED = 105_647_000_000.0
COMPONENT = 74_322_000_000.0


def _sec_facts_for_brkb() -> dict:
    """The companyfacts payload, as the provider returns it for BRK-B Q3 2026."""
    return dict(F.BOTH_SCOPES)


# ===========================================================================
# T1 — the SEC payload yields the consolidated figure
# ===========================================================================
class TestT1SecValue:
    def test_q3_2026_revenue_is_the_consolidated_figure(self):
        from tradingagents.dataflows.vendors import sec_edgar

        values, unit, provenance = sec_edgar._as_of_with_provenance(
            _sec_facts_for_brkb(), (F.CONTRACT, F.CONSOLIDATED),
            F.ANALYSIS_DATE, ((80, 100),), cik=F.CIK)
        assert values[("2026-09-30", 0)] == EXPECTED
        assert unit == "USD"
        assert provenance[("2026-09-30", 0)].concept == F.CONSOLIDATED


# ===========================================================================
# T2 — the normalizer keeps the concept and the period
# ===========================================================================
class TestT2ProvenancePreserved:
    def test_the_concept_and_period_survive_normalisation(self):
        from tradingagents.dataflows.vendors import sec_edgar

        _, _, provenance = sec_edgar._as_of_with_provenance(
            _sec_facts_for_brkb(), (F.CONSOLIDATED,), F.ANALYSIS_DATE,
            ((80, 100),), cik=F.CIK)
        record = provenance[("2026-09-30", 0)]
        assert record.concept == F.CONSOLIDATED
        assert record.period_start == "2026-07-01"
        assert record.period_end == "2026-09-30"
        assert record.unit == "USD"
        assert record.accession == F.ACCESSION
        assert record.cik == F.CIK
        assert record.validation_status == "VERIFIED"


# ===========================================================================
# T3 + T4 — the registry holds the metric, certified, from SEC data
# ===========================================================================
class TestT3T4CertifiedFromSec:
    def test_the_registry_holds_consolidated_revenue_from_the_filing(self):
        """T3: the metric exists and carries the SEC value, not a regex guess."""
        from tradingagents.dataflows.math.sec_revenue_inputs import (
            revenue_inputs_from_sec,
        )

        inputs = revenue_inputs_from_sec(
            _sec_facts_for_brkb(), F.ANALYSIS_DATE, cik=F.CIK)
        assert inputs is not None, "the SEC payload must produce revenue inputs"
        assert inputs.revenue_consolidated == EXPECTED

        registry = build_run_registry(inputs, run_id="t3")
        metric = registry.get("consolidated_revenue")
        assert metric is not None
        assert metric.value == EXPECTED
        assert metric.unit == "USD"
        assert metric.formula_id == "sec_filed_fact"

    def test_the_metric_is_certified_only_from_valid_sec_inputs(self):
        """T4: certified when the filing validates, not merely when present."""
        from tradingagents.dataflows.math.sec_revenue_inputs import (
            revenue_inputs_from_sec,
        )

        inputs = revenue_inputs_from_sec(
            _sec_facts_for_brkb(), F.ANALYSIS_DATE, cik=F.CIK)
        registry = build_run_registry(inputs, run_id="t4")
        metric = registry.get("consolidated_revenue")
        assert metric.is_usable is True
        assert metric.source_status.value == "VERIFIED"
        assert metric.calculation_status.value == "VERIFIED"
        assert metric.period_end == "2026-09-30"

    def test_the_provenance_is_carried_into_the_record(self):
        from tradingagents.dataflows.math.sec_revenue_inputs import (
            revenue_inputs_from_sec,
        )

        inputs = revenue_inputs_from_sec(
            _sec_facts_for_brkb(), F.ANALYSIS_DATE, cik=F.CIK)
        provenance = inputs.revenue_provenance
        assert provenance["concept"] == F.CONSOLIDATED
        assert provenance["cik"] == F.CIK
        assert provenance["accession"] == F.ACCESSION
        assert provenance["validation_status"] == "VERIFIED"
        # The record keeps it too, serialized for the audit JSON.
        registry = build_run_registry(inputs, run_id="prov")
        metric = registry.get("consolidated_revenue")
        assert F.CONSOLIDATED in str(metric.operands["revenue_provenance"])


# ===========================================================================
# T5 — the component cannot stand in for the consolidated total
# ===========================================================================
class TestT5ComponentRefused:
    def test_contract_revenue_alone_does_not_become_the_total(self):
        from tradingagents.dataflows.math.sec_revenue_inputs import (
            revenue_inputs_from_sec,
        )

        # Only the component is filed: no consolidated concept exists.
        inputs = revenue_inputs_from_sec(
            dict(F.ONLY_CONTRACT), F.ANALYSIS_DATE, cik=F.CIK)
        registry = build_run_registry(
            inputs if inputs is not None else RunInputs(), run_id="t5")
        metric = registry.get("consolidated_revenue")
        assert metric.value != COMPONENT
        assert metric.is_usable is False


# ===========================================================================
# T6 — no verifiable SEC data means no certification
# ===========================================================================
class TestT6UnverifiedWithoutSource:
    def test_an_empty_payload_certifies_nothing(self):
        from tradingagents.dataflows.math.sec_revenue_inputs import (
            revenue_inputs_from_sec,
        )

        assert revenue_inputs_from_sec({}, F.ANALYSIS_DATE, cik=F.CIK) is None
        registry = build_run_registry(RunInputs(), run_id="t6")
        metric = registry.get("consolidated_revenue")
        assert metric.is_usable is False
        assert metric.value is None

    def test_a_fact_filed_after_the_analysis_date_is_not_used(self):
        """Point-in-time: a run dated earlier never sees a later filing."""
        from tradingagents.dataflows.math.sec_revenue_inputs import (
            revenue_inputs_from_sec,
        )

        inputs = revenue_inputs_from_sec(
            _sec_facts_for_brkb(), "2026-10-01", cik=F.CIK)  # before the 10-25 filing
        assert inputs is None or inputs.revenue_consolidated is None


# ===========================================================================
# T7 + T8 — the report table matches the registry
# ===========================================================================
class TestT7T8ReportTable:
    def test_the_table_shows_the_value_period_and_status(self):
        """T7: the rendered row agrees with the MetricRecord."""
        from tradingagents.dataflows.certified_report import render_metric_table
        from tradingagents.dataflows.math.sec_revenue_inputs import (
            revenue_inputs_from_sec,
        )

        inputs = revenue_inputs_from_sec(
            _sec_facts_for_brkb(), F.ANALYSIS_DATE, cik=F.CIK)
        registry = build_run_registry(inputs, run_id="t7")
        table = render_metric_table(registry)

        row = next(line for line in table.splitlines()
                   if "consolidated_revenue" in line)
        assert "105647000000" in row or "105,647" in row
        assert "2026-09-30" in row
        assert "certified" in row

    def test_the_certified_count_matches_the_records(self):
        """T8: the summary line counts what the records actually say."""
        from tradingagents.dataflows.certified_report import render_metric_table
        from tradingagents.dataflows.math.sec_revenue_inputs import (
            revenue_inputs_from_sec,
        )

        inputs = revenue_inputs_from_sec(
            _sec_facts_for_brkb(), F.ANALYSIS_DATE, cik=F.CIK)
        registry = build_run_registry(inputs, run_id="t8")
        usable = len([r for r in registry._records.values() if r.is_usable])  # noqa: SLF001
        total = len(registry._records)  # noqa: SLF001
        table = render_metric_table(registry)
        assert f"{usable} of {total} metrics certified" in table


# ===========================================================================
# T9 — the end-to-end path from the SEC payload to the report table
# ===========================================================================
class TestT9EndToEnd:
    def test_sec_payload_to_certified_table_through_the_registry(self):
        from tradingagents.dataflows.certified_report import certified_table_markdown
        from tradingagents.dataflows.math.sec_revenue_inputs import (
            revenue_inputs_from_sec,
        )

        # 1. the provider payload
        payload = _sec_facts_for_brkb()
        # 2. normalised into revenue inputs
        inputs = revenue_inputs_from_sec(payload, F.ANALYSIS_DATE, cik=F.CIK)
        # 3. built into the registry the gate certifies against
        registry = build_run_registry(inputs, run_id="t9")
        # 4. rendered into the report's certified table
        table = certified_table_markdown({"metric_registry": registry})

        row = next(line for line in table.splitlines()
                   if "consolidated_revenue" in line)
        assert "105647000000" in row or "105,647" in row
        assert "sec_filed_fact" in row
        assert "2026-09-30" in row


# ===========================================================================
# T10 — no regression in the neighbouring metrics or the gate
# ===========================================================================
class TestT10NoRegression:
    def test_pb_and_eps_are_untouched_by_the_revenue_path(self):
        from tradingagents.dataflows.math.sec_revenue_inputs import (
            revenue_inputs_from_sec,
        )

        inputs = revenue_inputs_from_sec(
            _sec_facts_for_brkb(), F.ANALYSIS_DATE, cik=F.CIK)
        registry = build_run_registry(inputs, run_id="t10")
        # The revenue path supplies revenue only: P/B has no price here, so it
        # must remain not-computable rather than acquiring a value.
        assert registry.get("price_to_book").value is None
        assert registry.get("eps_gaap").value is None

    def test_revenue_growth_follows_the_consolidated_pair(self):
        from tradingagents.dataflows.math.sec_revenue_inputs import (
            revenue_inputs_from_sec,
        )

        inputs = revenue_inputs_from_sec(
            _sec_facts_for_brkb(), F.ANALYSIS_DATE, cik=F.CIK)
        registry = build_run_registry(inputs, run_id="growth")
        assert float(registry.get("revenue_growth").value) == pytest.approx(
            F.EXPECTED_GROWTH_PCT, abs=0.01)


# ===========================================================================
# T11 — the graph node writes the filing's revenue into the run's state
# ===========================================================================
class TestT11NodeWritesState:
    """The link that was missing: nothing put SEC revenue into the state."""

    def _node(self):
        from tradingagents.graph.data_quality_node import (
            create_data_quality_check,
        )

        return create_data_quality_check(
            filings_provider=lambda t, d: [],
            sec_facts_provider=lambda t, d: dict(F.BOTH_SCOPES),
        )

    def test_the_node_publishes_the_consolidated_revenue(self):
        out = self._node()({
            "company_of_interest": "BRK-B",
            "trade_date": F.ANALYSIS_DATE,
            "fundamentals_report": "Berkshire grew revenue strongly.",
        })
        assert out["revenue_consolidated"] == EXPECTED
        assert out["revenue_provenance"]["concept"] == F.CONSOLIDATED
        assert out["revenue_provenance"]["period_end"] == "2026-09-30"
        assert out["revenue_prior"] == F.REVENUE_Q3_2025

    def test_a_provider_that_fails_leaves_the_metric_uncertified(self):
        from tradingagents.graph.data_quality_node import (
            create_data_quality_check,
        )

        def boom(ticker, date):
            raise RuntimeError("SEC down")

        node = create_data_quality_check(filings_provider=lambda t, d: [],
                                         sec_facts_provider=boom)
        out = node({"company_of_interest": "BRK-B",
                    "trade_date": F.ANALYSIS_DATE,
                    "fundamentals_report": ""})
        assert "revenue_consolidated" not in out

    def test_the_state_reaches_the_registry_and_the_table(self):
        """The full chain, as the graph runs it."""
        from tradingagents.dataflows.certified_report import (
            certified_table_markdown,
        )
        from tradingagents.dataflows.math.run_registry import (
            build_run_registry,
            registry_inputs_from_state,
        )

        state = {"company_of_interest": "BRK-B", "trade_date": F.ANALYSIS_DATE,
                 "fundamentals_report": "Berkshire grew revenue strongly."}
        state.update(self._node()(state))
        registry = build_run_registry(
            registry_inputs_from_state(state), run_id="t11")

        metric = registry.get("consolidated_revenue")
        assert metric.value == EXPECTED
        assert metric.is_usable is True
        assert metric.period_end == "2026-09-30"

        table = certified_table_markdown({"metric_registry": registry})
        row = next(line for line in table.splitlines()
                   if "consolidated_revenue" in line)
        assert "certified" in row
        assert "2026-09-30" in row


# ===========================================================================
# T12 — against Berkshire's real payload, when the cache is present
# ===========================================================================
_BRK_CACHE = "/Users/emiliano.muzzurru/.tradingagents/cache/sec_edgar/CIK0001067983.json"


def _real_payload():
    import json
    import pathlib

    path = pathlib.Path(_BRK_CACHE)
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


@pytest.mark.skipif(_real_payload() is None, reason="no BRK-B SEC cache here")
class TestT12RealPayload:
    def test_the_live_filing_yields_the_consolidated_total(self):
        from tradingagents.dataflows.math.sec_revenue_inputs import (
            revenue_inputs_from_sec,
        )

        inputs = revenue_inputs_from_sec(
            _real_payload(), F.ANALYSIS_DATE, cik=F.CIK)
        assert inputs is not None
        assert inputs.revenue_consolidated == EXPECTED
        assert inputs.revenue_prior == F.REVENUE_Q3_2025
        # The concept is the consolidated one, not the contract component.
        assert inputs.revenue_provenance["concept"] == "Revenues"
        assert inputs.revenue_provenance["period_end"] == "2026-09-30"
        assert inputs.revenue_provenance["validation_status"] == "VERIFIED"

    def test_the_report_shows_the_real_value_as_certified(self):
        from tradingagents.dataflows.certified_report import (
            certified_table_markdown,
        )
        from tradingagents.dataflows.math.sec_revenue_inputs import (
            revenue_inputs_from_sec,
        )

        inputs = revenue_inputs_from_sec(
            _real_payload(), F.ANALYSIS_DATE, cik=F.CIK)
        registry = build_run_registry(inputs, run_id="real")
        row = next(line for line in
                   certified_table_markdown({"metric_registry": registry}).splitlines()
                   if "consolidated_revenue" in line)
        assert "certified" in row
        assert "105647" in row


# ===========================================================================
# FIX #1 revision — Problem A: the compiled graph must keep the state keys
# ===========================================================================
def _compiled_graph(sec_payload=None, report="Q3 2026 total revenue 105,647 million USD."):
    """A real `StateGraph(AgentState)`, not a dict merge.

    The defect Problem A describes is that LangGraph keeps only the keys its
    schema declares, so a node's extra keys vanish between nodes. Only a
    compiled graph can show that: `{**state, **out}` cannot.
    """
    from langgraph.graph import END, StateGraph

    from tradingagents.agents.state import AgentState
    from tradingagents.graph.data_quality_node import create_data_quality_check

    payload = dict(F.BOTH_SCOPES) if sec_payload is None else sec_payload
    node = create_data_quality_check(
        filings_provider=lambda t, d: [],
        sec_facts_provider=lambda t, d: payload,
    )

    graph = StateGraph(AgentState)
    graph.add_node("dq", node)
    graph.add_node("analyst", lambda s: {"fundamentals_report": report})
    graph.set_entry_point("dq")
    graph.add_edge("dq", "analyst")
    graph.add_edge("analyst", END)
    return graph.compile()


def _graph_state(sec_payload=None, report="Q3 2026 total revenue 105,647 million USD."):
    return _compiled_graph(sec_payload, report).invoke({
        "company_of_interest": "BRK-B",
        "trade_date": F.ANALYSIS_DATE,
        "messages": [],
        "fundamentals_report": "",
    })


class TestACompiledGraphKeepsState:
    def test_a_revenue_consolidated_survives_the_graph(self):
        state = _graph_state()
        assert "revenue_consolidated" in state, (
            "the compiled graph dropped it: AgentState must declare the key")
        assert state["revenue_consolidated"] == EXPECTED

    def test_b_prior_and_provenance_survive_the_graph(self):
        state = _graph_state()
        assert state.get("revenue_prior") == F.REVENUE_Q3_2025
        provenance = state.get("revenue_provenance")
        assert isinstance(provenance, dict)
        assert provenance["concept"] == "Revenues"
        assert provenance["period_end"] == "2026-09-30"
        assert provenance["validation_status"] == "VERIFIED"

    def test_c_the_registry_is_built_from_the_graphs_own_state(self):
        from tradingagents.dataflows.math.extraction import extract_run_inputs
        from tradingagents.dataflows.math.run_registry import (
            build_run_registry,
            registry_inputs_from_state,
        )
        from tradingagents.graph.trading_graph import _merge_extracted

        state = _graph_state()
        merged = _merge_extracted(registry_inputs_from_state(state),
                                  extract_run_inputs(state).inputs)
        registry = build_run_registry(merged, run_id="graph")
        metric = registry.get("consolidated_revenue")
        assert metric.value == EXPECTED
        assert metric.period_end == "2026-09-30"
        assert metric.is_usable is True


# ===========================================================================
# Problem B — SEC_VERIFIED outranks LLM_EXTRACTED for consolidated_revenue
# ===========================================================================
def _merge_and_certify(state: dict):
    from tradingagents.dataflows.math.extraction import extract_run_inputs
    from tradingagents.dataflows.math.run_registry import (
        build_run_registry,
        registry_inputs_from_state,
    )
    from tradingagents.graph.trading_graph import _merge_extracted

    merged = _merge_extracted(registry_inputs_from_state(state),
                              extract_run_inputs(state).inputs)
    return merged, build_run_registry(merged, run_id="merge")


def _sec_state(report: str, *, provenance=None, value=EXPECTED) -> dict:
    from tradingagents.dataflows.math.sec_revenue_inputs import (
        revenue_inputs_from_sec,
    )

    sec = revenue_inputs_from_sec(dict(F.BOTH_SCOPES), F.ANALYSIS_DATE,
                                  cik=F.CIK)
    if provenance is None:
        provenance = sec.revenue_provenance
    return {
        "company_of_interest": "BRK-B", "trade_date": F.ANALYSIS_DATE,
        "verified_market_snapshot": {},
        "revenue_consolidated": value,
        "revenue_prior": sec.revenue_prior,
        "revenue_provenance": provenance,
        "fundamentals_report": report,
    }


class TestBPrecedence:
    def test_d_sec_verified_wins_over_a_report_stating_the_same_figure(self):
        """D: the report restates the filing; the filing is the fact."""
        merged, registry = _merge_and_certify(
            _sec_state("Q3 2026 total revenue 105,647 million USD."))
        metric = registry.get("consolidated_revenue")
        assert metric.value == EXPECTED
        assert metric.source_status.value == "VERIFIED"
        assert metric.is_usable is True
        assert "revenue_consolidated" not in merged.claimed_inputs

    def test_e_prose_alone_is_not_certified(self):
        """E: no SEC provenance means no certification."""
        state = {"company_of_interest": "BRK-B", "trade_date": F.ANALYSIS_DATE,
                 "verified_market_snapshot": {},
                 "fundamentals_report": "Q3 2026 total revenue 105,647 million USD."}
        _, registry = _merge_and_certify(state)
        metric = registry.get("consolidated_revenue")
        assert metric.is_usable is False
        assert metric.source_status.value == "LLM_CLAIMED"

    def test_f_an_unavailable_source_certifies_no_text_fallback(self):
        """F: SEC down, the report still states a figure — still not certified."""
        state = _graph_state(sec_payload={})
        assert "revenue_consolidated" not in state
        _, registry = _merge_and_certify(
            {**state, "fundamentals_report": "total revenue 105,647 million",
             "verified_market_snapshot": {}})
        assert registry.get("consolidated_revenue").is_usable is False

    def test_g_contract_revenue_alone_certifies_no_consolidated_total(self):
        """G: the component is filed and the report calls it revenue."""
        state = _graph_state(sec_payload=dict(F.ONLY_CONTRACT))
        merged, registry = _merge_and_certify(
            {**state, "fundamentals_report": "total revenue 74,322 million",
             "verified_market_snapshot": {}})
        metric = registry.get("consolidated_revenue")
        assert metric.value != COMPONENT or metric.is_usable is False
        assert metric.is_usable is False
        assert "revenue_consolidated" in (merged.claimed_inputs or ())

    def test_h_a_conflicting_report_keeps_the_filing_and_records_it(self):
        """H: the report says 74.3bn, the filing says 105,647 — the filing wins."""
        merged, registry = _merge_and_certify(
            _sec_state("Revenue came to 74.3 billion this quarter."))
        metric = registry.get("consolidated_revenue")
        assert metric.value == EXPECTED, "the LLM figure must not replace the fact"
        assert metric.source_status.value == "VERIFIED"
        assert metric.is_usable is True
        # The claim is not silently dropped: the report's own figure is still
        # visible to the claims validator through the reports it reads.
        assert registry.get("revenue_growth").is_usable is True

    def test_an_unverified_provenance_keeps_the_claim_classification(self):
        """The precedence only applies to a *verified* SEC provenance."""
        provenance = {"concept": "RevenueFromContractWithCustomerExcludingAssessedTax",
                      "validation_status": "UNVERIFIED", "period_end": "2026-09-30"}
        merged, registry = _merge_and_certify(
            _sec_state("Q3 2026 total revenue 105,647 million USD.",
                       provenance=provenance, value=COMPONENT))
        metric = registry.get("consolidated_revenue")
        assert metric.is_usable is False
        assert metric.source_status.value == "LLM_CLAIMED"


# ===========================================================================
# I — the rest is untouched
# ===========================================================================
class TestINoRegression:
    def test_other_metrics_keep_their_own_classification(self):
        """The precedence is scoped: a claimed P/B stays claimed."""
        state = _sec_state("Book value per share 359.56 and P/B 1.73x.")
        merged, registry = _merge_and_certify(state)
        # P/B is not in the precedence list, so a prose figure remains a claim.
        assert "book_value_per_share" in (merged.claimed_inputs or ()) or \
               registry.get("price_to_book").is_usable is False

    def test_the_gate_still_blocks_without_a_portfolio(self):
        from tradingagents.dataflows.gate_integration import evaluate_decision

        state = _graph_state()
        _, decision = evaluate_decision(
            "BRK-B", F.ANALYSIS_DATE, state, snapshot_id="i", trigger_met=True)
        assert decision.creates_order is False

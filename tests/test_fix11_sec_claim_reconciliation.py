"""FIX #1.1 — a report that contradicts the certified revenue must be recorded.

FIX #1 made the SEC figure reach the registry as `VERIFIED`. Runtime
verification of `c1d6019` then found requirement #7 unmet: when the Fundamentals
Analyst states a *different* consolidated revenue, the registry keeps the filing's
value — correctly — but the disagreement is recorded nowhere. The value wins
silently and a reader cannot tell whether the report agreed with the filing or
contradicted it.

Root cause has two halves:

1. `_reconcile_sec_concepts` in the Data Quality Check node *could* do this, but
   the node is built as `create_data_quality_check()` with **no provider**, so it
   returns early on every run.
2. Even with a provider, the node runs *in parallel with the analysts*
   (`workflow.add_edge(START, node)` for both), so `fundamentals_report` is still
   empty when it executes. There is nothing to compare against.

So the check has to run where both are present: after the report exists and with
the certified registry in hand — which is exactly where the Execution Gate
evaluates reports.

Every test here is deterministic: frozen SEC payloads, fixed report strings, no
model and no network.
"""

from __future__ import annotations

import json

import pytest

from tests.fixtures import sec_revenue_payloads as F
from tests.test_fix_registry_consolidated_revenue import (
    EXPECTED,
    _graph_state,
    _sec_state,
)
from tradingagents.dataflows.math.run_registry import build_run_registry
from tradingagents.dataflows.math.sec_revenue_inputs import revenue_inputs_from_sec

pytestmark = pytest.mark.unit

COMPONENT = 74_322_000_000.0


def _rated_state(report: str) -> dict:
    """A state the gate can judge: it needs a rating it recognises."""
    state = _sec_state(report)
    state.setdefault("final_rating", "Overweight")
    state.setdefault("final_trade_decision", "**Rating**: Overweight\nBuy.")
    state.setdefault("trader_investment_plan", "Entry 560.00, stop 552.07, ATR 9.520.")
    return state


def _certified_registry():
    """The registry FIX #1 produces: a verified consolidated revenue."""
    inputs = revenue_inputs_from_sec(
        dict(F.BOTH_SCOPES), F.ANALYSIS_DATE, cik=F.CIK)
    return build_run_registry(inputs, run_id="fix11")


def _reconcile(report, registry=None, *, agent="fundamentals"):
    from tradingagents.dataflows.sec_claim_reconciliation import (
        reconcile_revenue_claims,
    )

    return reconcile_revenue_claims(
        registry if registry is not None else _certified_registry(),
        {agent: report},
    )


# ===========================================================================
# T1 — agreement is not a mismatch
# ===========================================================================
class TestT1Agreement:
    def test_the_same_figure_produces_no_finding(self):
        findings = _reconcile("Q3 2026 consolidated revenue was 105,647 million USD.")
        assert findings == []

    def test_a_percentage_restatement_is_not_a_level_claim(self):
        """A growth rate is not a claim about the revenue level."""
        findings = _reconcile("Revenue grew 11.2% year over year to a record.")
        assert findings == []


# ===========================================================================
# T2 — a different figure labelled as consolidated revenue is a mismatch
# ===========================================================================
class TestT2Mismatch:
    def test_the_contract_component_stated_as_total_revenue_is_caught(self):
        findings = _reconcile(
            "Q3 2026 consolidated revenue came to 74,322 million USD.")
        assert len(findings) == 1
        finding = findings[0]
        assert finding.code == "SEC_REVENUE_CLAIM_MISMATCH"
        assert finding.metric_id == "consolidated_revenue"
        assert finding.sec_value == EXPECTED
        assert finding.claimed_value == COMPONENT
        assert finding.period_end == "2026-09-30"
        assert finding.unit == "USD"
        assert finding.agent == "fundamentals"
        assert finding.accession == F.ACCESSION
        assert finding.blocks is True

    def test_the_finding_records_a_human_readable_description(self):
        finding = _reconcile(
            "Q3 2026 total revenue 74,322 million USD.")[0]
        assert "105,647" in finding.description or "105647" in finding.description
        assert "74,322" in finding.description or "74322" in finding.description

    def test_the_finding_serialises_for_the_audit(self):
        finding = _reconcile("total revenue 74,322 million USD.")[0]
        payload = finding.summary()
        for field in ("code", "metric_id", "sec_value", "claimed_value",
                      "period_end", "unit", "agent", "accession",
                      "description", "severity", "blocks"):
            assert field in payload, field
        assert json.dumps(payload)  # must be JSON-able for the audit


# ===========================================================================
# T3 — a correctly labelled component is not a false positive
# ===========================================================================
class TestT3CorrectlyLabelledComponent:
    def test_contract_revenue_labelled_as_such_is_not_a_claim_on_the_total(self):
        findings = _reconcile(
            "Revenue from contracts with customers was 74,322 million USD, "
            "a component of the 105,647 million consolidated total.")
        assert findings == []

    def test_a_segment_figure_is_not_compared_to_the_total(self):
        findings = _reconcile(
            "The insurance segment reported revenue of 74,322 million USD.")
        assert findings == []


# ===========================================================================
# T4 — a different period is not compared
# ===========================================================================
class TestT4Period:
    def test_a_prior_year_figure_is_not_compared_to_the_current_period(self):
        """A historical citation is context, not a claim on this quarter."""
        findings = _reconcile(
            "For reference, Q3 2024 consolidated revenue was 74,322 million USD.")
        assert findings == []


# ===========================================================================
# T5 — no certified metric, no comparison and no certification
# ===========================================================================
class TestT5NoSecMetric:
    def test_without_a_certified_revenue_nothing_is_claimed_a_mismatch(self):
        from tradingagents.dataflows.math.run_registry import RunInputs

        empty = build_run_registry(RunInputs(), run_id="empty")
        findings = _reconcile("total revenue 74,322 million USD.", empty)
        assert findings == [], "no certified figure means nothing to contradict"
        assert empty.get("consolidated_revenue").is_usable is False


# ===========================================================================
# T6 + T7 — the finding reaches the audit and the gate
# ===========================================================================
class TestT6T7ReachesAuditAndGate:
    def test_the_finding_lands_in_the_audit_json(self, tmp_path):
        from tradingagents.dataflows.gate_integration import evaluate_decision

        state = _rated_state("Q3 2026 consolidated revenue came to 74,322 million USD.")
        registry = _certified_registry()
        record, decision = evaluate_decision(
            "BRK-B", F.ANALYSIS_DATE, state, snapshot_id="t6",
            metric_registry=registry, trigger_met=True)
        audit = record.to_dict()
        payload = json.dumps(audit)
        assert "SEC_REVENUE_CLAIM_MISMATCH" in payload, (
            "the conflict must be visible in the audit, not only in memory")

    def test_the_gate_blocks_on_the_unresolved_conflict(self):
        from tradingagents.dataflows.gate_integration import evaluate_decision

        state = _rated_state("Q3 2026 consolidated revenue came to 74,322 million USD.")
        registry = _certified_registry()
        _, decision = evaluate_decision(
            "BRK-B", F.ANALYSIS_DATE, state, snapshot_id="t7",
            metric_registry=registry, trigger_met=True)
        codes = [r.value for r in decision.reasons]
        assert "SEC_REVENUE_CLAIM_MISMATCH" in codes
        assert decision.creates_order is False


# ===========================================================================
# T8 — a blocked order reaches no broker
# ===========================================================================
class TestT8NoBroker:
    def test_a_conflicted_order_is_never_sent(self):
        from tradingagents.dataflows.execution_gate import ExecutionGate
        from tradingagents.dataflows.gate_integration import evaluate_decision

        sent: list[dict] = []
        state = _rated_state("Q3 2026 consolidated revenue came to 74,322 million USD.")
        registry = _certified_registry()
        record, decision = evaluate_decision(
            "BRK-B", F.ANALYSIS_DATE, state, snapshot_id="t8",
            metric_registry=registry, trigger_met=True)

        # The only path to a venue: a decision that creates an order.
        if decision.creates_order:
            sent.append({"action": decision.action.value})

        gate = ExecutionGate(analysis_as_of=F.ANALYSIS_DATE)
        assert decision.creates_order is False
        assert sent == []
        assert gate.orders() == []


# ===========================================================================
# T9 — the certified metric is untouched
# ===========================================================================
class TestT9MetricUntouched:
    def test_the_sec_value_stays_verified_after_the_conflict(self):
        registry = _certified_registry()
        _reconcile("total revenue 74,322 million USD.", registry)
        metric = registry.get("consolidated_revenue")
        assert metric.value == EXPECTED
        assert metric.source_status.value == "VERIFIED"
        assert metric.calculation_status.value == "VERIFIED"
        assert metric.is_usable is True


# ===========================================================================
# T10 — the inspected path is a compiled graph, not a dict
# ===========================================================================
class TestT10CompiledGraph:
    def test_the_conflict_is_found_from_a_graph_produced_state(self):
        """The state comes from a real `StateGraph(AgentState)` run."""
        from tradingagents.dataflows.sec_claim_reconciliation import (
            reconcile_revenue_claims,
        )

        state = _graph_state(
            report="Q3 2026 consolidated revenue came to 74,322 million USD.")
        registry = build_run_registry(
            revenue_inputs_from_sec(dict(F.BOTH_SCOPES), F.ANALYSIS_DATE,
                                    cik=F.CIK), run_id="graph")
        findings = reconcile_revenue_claims(
            registry, {"fundamentals": state.get("fundamentals_report")})
        assert len(findings) == 1
        assert findings[0].claimed_value == COMPONENT


# ===========================================================================
# T6b — the finding reaches the run record, not only the audit
# ===========================================================================
class TestT6bRunRecord:
    def test_the_run_record_carries_the_finding(self, tmp_path):
        from tradingagents.dataflows import run_record as rr
        from tradingagents.dataflows.gate_integration import evaluate_decision

        state = _rated_state(
            "Q3 2026 consolidated revenue came to 74,322 million USD.")
        registry = _certified_registry()
        record, decision = evaluate_decision(
            "BRK-B", F.ANALYSIS_DATE, state, snapshot_id="t6b",
            metric_registry=registry, trigger_met=True)

        run = rr.RunRecord(
            run_id=rr.new_run_id("BRK-B"), ticker="BRK-B",
            analysis_date=F.ANALYSIS_DATE,
            metric_registry=registry.summary(),
            execution_gate=record.to_dict().get("execution", {}),
            audit=record.to_dict(),
        )
        path = rr.write_run(tmp_path, run)
        payload = path.read_text(encoding="utf-8")
        assert "SEC_REVENUE_CLAIM_MISMATCH" in payload, (
            "the conflict must be in the persisted record, not only in memory")

    def test_a_clean_run_records_no_finding(self, tmp_path):
        from tradingagents.dataflows import run_record as rr
        from tradingagents.dataflows.gate_integration import evaluate_decision

        state = _rated_state("Q3 2026 consolidated revenue was 105,647 million USD.")
        registry = _certified_registry()
        record, _ = evaluate_decision(
            "BRK-B", F.ANALYSIS_DATE, state, snapshot_id="clean",
            metric_registry=registry, trigger_met=True)
        run = rr.RunRecord(
            run_id=rr.new_run_id("BRK-B"), ticker="BRK-B",
            analysis_date=F.ANALYSIS_DATE,
            execution_gate=record.to_dict().get("execution", {}),
            audit=record.to_dict(),
        )
        path = rr.write_run(tmp_path, run)
        assert "SEC_REVENUE_CLAIM_MISMATCH" not in path.read_text(encoding="utf-8")


# ===========================================================================
# FIX #1.1 final review — Italian reports and structured persistence
# ===========================================================================
class TestItalianReports:
    """The reports are written in the configured output language.

    An Italian sentence saying "i ricavi consolidati sono 74.322 milioni" is a
    claim on the same quantity as its English counterpart, and the Italian
    convention puts the dot on thousands and the comma on decimals — so
    `74.322` is 74322 and `74,322` is 74.322.
    """

    @pytest.mark.parametrize("text,expected", [
        ("I ricavi consolidati del Q3 sono stati 74.322 milioni di dollari.",
         74_322_000_000.0),
        ("Il fatturato del trimestre è di 74,322 miliardi USD.",
         74_322_000_000.0),
        ("Ricavi: 74.322 mln USD.", 74_322_000_000.0),
        ("| Ricavi | 74.322 | mln |", 74_322_000_000.0),
        ("I ricavi totali si attestano a 74.322 milioni di dollari.",
         74_322_000_000.0),
    ])
    def test_an_italian_claim_is_read_in_base_units(self, text, expected):
        findings = _reconcile(text)
        assert len(findings) == 1, f"not recognised: {text!r}"
        assert findings[0].claimed_value == expected

    @pytest.mark.parametrize("text", [
        "I ricavi consolidati Q3 2026 sono 105.647 milioni.",
        "I ricavi consolidati sono 105,647 miliardi di dollari.",
    ])
    def test_the_consolidated_figure_stated_correctly_is_not_a_mismatch(self, text):
        assert _reconcile(text) == []

    @pytest.mark.parametrize("text", [
        "I ricavi da contratti con clienti sono 74,322 miliardi, un componente.",
        "Il segmento assicurativo ha riportato ricavi per 74.322 milioni.",
        "Per riferimento, nel Q3 2024 i ricavi consolidati erano 74,322 miliardi.",
    ])
    def test_a_component_segment_or_prior_period_is_not_a_claim(self, text):
        assert _reconcile(text) == [], f"false positive on {text!r}"

    def test_the_italian_scale_words_are_equivalent(self):
        """`mln`, `milioni` and `M$` are millions; `mld`, `miliardi` are billions."""
        from tradingagents.dataflows.sec_claim_reconciliation import _scale_value

        assert _scale_value("74.322", "milioni") == 74_322_000_000.0
        assert _scale_value("74.322", "mln") == 74_322_000_000.0
        assert _scale_value("74,322", "miliardi") == 74_322_000_000.0
        assert _scale_value("74,322", "mld") == 74_322_000_000.0
        assert _scale_value("105.647", "milioni") == 105_647_000_000.0
        assert _scale_value("105,647", "miliardi") == 105_647_000_000.0


class TestStructuredPersistence:
    """The finding must survive as data, not only as a reason code."""

    def _run(self):
        from tradingagents.dataflows.gate_integration import evaluate_decision

        state = _rated_state(
            "I ricavi consolidati del Q3 sono stati 74.322 milioni di dollari.")
        registry = _certified_registry()
        return evaluate_decision(
            "BRK-B", F.ANALYSIS_DATE, state, snapshot_id="persist",
            metric_registry=registry, trigger_met=True), registry

    def test_the_audit_carries_the_complete_finding(self):
        (record, decision), _ = self._run()
        findings = record.to_dict().get("revenue_claim_findings")
        assert findings, "the audit must carry the finding itself"
        finding = findings[0]
        for field in ("code", "metric_id", "sec_value", "claimed_value",
                      "period_end", "unit", "agent", "accession",
                      "description", "severity", "blocks"):
            assert field in finding, field
        assert finding["sec_value"] == EXPECTED
        assert finding["claimed_value"] == COMPONENT
        assert finding["blocks"] is True

    def test_the_reason_code_alone_is_not_the_record(self):
        """The gate reason is a code; the audit entry is the evidence."""
        (record, decision), _ = self._run()
        assert "SEC_REVENUE_CLAIM_MISMATCH" in [r.value for r in decision.reasons]
        audit_json = json.dumps(record.to_dict())
        assert '"claimed_value"' in audit_json, (
            "only the code was recorded, not the two figures")

    def test_the_run_record_carries_the_finding(self, tmp_path):
        from tradingagents.dataflows import run_record as rr

        (record, _), registry = self._run()
        run = rr.RunRecord(
            run_id=rr.new_run_id("BRK-B"), ticker="BRK-B",
            analysis_date=F.ANALYSIS_DATE,
            metric_registry=registry.summary(),
            revenue_claim_findings=(
                record.to_dict().get("revenue_claim_findings") or []),
            execution_gate=record.to_dict().get("execution", {}),
            audit=record.to_dict(),
        )
        path = rr.write_run(tmp_path, run)
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["revenue_claim_findings"], "run record lost the finding"
        assert payload["revenue_claim_findings"][0]["claimed_value"] == COMPONENT

    def test_the_certified_metric_is_untouched_by_the_conflict(self):
        (_, _), registry = self._run()
        metric = registry.get("consolidated_revenue")
        assert metric.value == EXPECTED
        assert metric.is_usable is True


# ===========================================================================
# Fiscal period — a prior-year figure is not a contradiction
# ===========================================================================
class TestFiscalPeriod:
    """The certified period decides which figures are comparable.

    The recogniser used to exclude historical figures with a regex that ended
    at 2024, so `Q3 2025: 94.972 milioni` — a *correct* prior-year statement —
    was reported as contradicting the certified Q3 2026 metric. The period is
    now read from the record and compared arithmetically, so no year is ever
    hardcoded and nothing falls off the end of a list.
    """

    def test_a_correct_prior_year_figure_is_not_a_contradiction(self):
        findings = _reconcile("Ricavi consolidati Q3 2025: 94.972 milioni USD")
        assert findings == [], "a prior-year figure must not contradict this year"

    def test_the_same_figure_stated_for_the_certified_period_is_one(self):
        findings = _reconcile("Ricavi consolidati Q3 2026: 74.322 milioni USD")
        assert len(findings) == 1
        assert findings[0].claimed_value == COMPONENT

    def test_an_implicit_period_is_still_checked(self):
        """No period named means the current one — the case the check exists for."""
        findings = _reconcile("Ricavi consolidati: 74.322 milioni USD")
        assert len(findings) == 1

    def test_a_different_quarter_of_the_same_year_is_not_compared(self):
        findings = _reconcile("Ricavi consolidati Q2 2026: 74.322 milioni USD")
        assert findings == []

    @pytest.mark.parametrize("text", [
        "| Ricavi consolidati | Q3 2025 | 94.972 | mln |",
        "Ricavi consolidati 2025-Q3: 94.972 milioni",
        "Ricavi consolidati FY2025: 94.972 milioni",
        "Ricavi consolidati dell'anno precedente: 94.972 milioni",
    ])
    def test_the_period_spellings_the_reports_use_are_understood(self, text):
        assert _reconcile(text) == []

    @pytest.mark.parametrize("text", [
        "| Ricavi consolidati | Q3 2026 | 74.322 | mln |",
        "Ricavi consolidati 2026-Q3: 74.322 milioni",
    ])
    def test_the_certified_period_is_recognised_in_every_spelling(self, text):
        assert len(_reconcile(text)) == 1

    def test_the_period_comes_from_the_record_not_a_list(self):
        """A future year works without touching the code."""
        from tradingagents.dataflows.math.run_registry import RunInputs, build_run_registry

        registry = build_run_registry(
            RunInputs(revenue_consolidated=200_000_000_000.0,
                      revenue_prior=180_000_000_000.0,
                      revenue_provenance={
                          "concept": "Revenues", "validation_status": "VERIFIED",
                          "period_end": "2028-09-30", "accession": "future"}),
            run_id="future")
        # The metric must be usable for the check to run at all.
        if not registry.get("consolidated_revenue").is_usable:
            pytest.skip("the registry refused this synthetic record")

        # 2027 is the prior year for 2028 — excluded without any code change.
        assert _reconcile("Ricavi consolidati Q3 2027: 74.322 milioni",
                          registry) == []
        # 2028 is the certified year — compared.
        assert len(_reconcile("Ricavi consolidati Q3 2028: 74.322 milioni",
                              registry)) == 1

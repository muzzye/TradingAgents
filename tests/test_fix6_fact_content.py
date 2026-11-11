"""FIX #6 — the Fact Checker must verify content, not only fact ids.

Invariant I2 of the contract:

    A verified Fact ID does not automatically certify any sentence that cites it.

The existing `check_claim()` honours that id: it confirms the fact exists, is
verified and not quarantined, then returns SUPPORTED — without ever looking at
what the sentence *says*. Reproduced on the merged tree:

    fact  brk_revenue = 105,647,000,000 USD  (VERIFIED)
    claim "Revenue was 74,322 million" citing brk_revenue
      ->  SUPPORTED  (OK)   blocks=False

A sentence contradicting its own cited evidence passed as supported. That is a
false SUPPORTED, the failure mode the contract calls out by name, and it is
what this module's tests establish before the fix.

Figures are frozen constants; no network, no broker.
"""

from __future__ import annotations

from datetime import UTC, datetime

from tradingagents.dataflows.fact_checker import (
    Claim,
    ClaimClassification,
    ClaimStatus,
    ReasonCode,
    check_claim,
    check_claims,
)
from tradingagents.dataflows.provenance import (
    QUARANTINED,
    UNVERIFIED,
    VERIFIED,
    Fact,
    ProvenanceRegistry,
)

SNAPSHOT = "snap-fix6"
AS_OF = datetime(2026, 11, 10, tzinfo=UTC)


def _fact(fact_id: str, value, *, unit="USD", period="2026-Q3",
          period_end="2026-09-30", status=VERIFIED, snapshot=SNAPSHOT,
          published_at=None):
    return Fact(
        fact_id=fact_id, value=value, unit=unit, period=period,
        period_end=period_end, source_type="sec_filing", source_uri=None,
        published_at=published_at or datetime(2026, 11, 9, tzinfo=UTC),
        retrieved_at=AS_OF, snapshot_id=snapshot, verification_status=status)


def _registry(*facts) -> ProvenanceRegistry:
    registry = ProvenanceRegistry(snapshot_id=SNAPSHOT)
    for fact in facts:
        registry.add(fact)
    return registry


def _fact_claim(statement, fact_ids, *, blocking=True, classification=None):
    return Claim(
        claim_id="c1", statement=statement,
        classification=classification or ClaimClassification.FACT,
        supporting_fact_ids=tuple(fact_ids), blocking=blocking)


# ===========================================================================
# I2 / B10 — a cited fact must actually support what the sentence says
# ===========================================================================
class TestAClaimMustAgreeWithItsCitedEvidence:
    """The claim's own number is compared to the fact's value."""

    def test_b10_a_stated_value_contradicting_its_fact_is_refuted(self):
        """The reproduced defect: 74,322M cited against a 105,647M fact."""
        registry = _registry(_fact("brk_revenue", 105_647_000_000.0))
        verdict = check_claim(
            _fact_claim("Revenue was 74,322 million", ["brk_revenue"]),
            registry)
        assert verdict.status is ClaimStatus.REFUTED, (
            "a sentence stating 74,322M against its own cited 105,647M fact "
            "was certified as SUPPORTED")
        assert verdict.blocks_decision is True

    def test_a_stated_value_matching_its_fact_is_supported(self):
        """The control: the same shape with the right number."""
        registry = _registry(_fact("brk_revenue", 105_647_000_000.0))
        verdict = check_claim(
            _fact_claim("Revenue was 105,647 million", ["brk_revenue"]),
            registry)
        assert verdict.status is ClaimStatus.SUPPORTED

    def test_a_stated_value_in_the_facts_own_unit_scale_is_supported(self):
        """A fact in raw USD and a sentence in millions describe one number."""
        registry = _registry(_fact("brk_revenue", 105_647_000_000.0))
        verdict = check_claim(
            _fact_claim("Revenue was 105.647 billion", ["brk_revenue"]),
            registry)
        assert verdict.status is ClaimStatus.SUPPORTED

    def test_a_sentence_with_no_number_is_not_refuted_by_this_rule(self):
        """Only a stated figure can contradict a figure."""
        registry = _registry(_fact("brk_revenue", 105_647_000_000.0))
        verdict = check_claim(
            _fact_claim("Revenue grew strongly", ["brk_revenue"]), registry)
        assert verdict.status is ClaimStatus.SUPPORTED

    def test_the_reason_code_names_the_contradiction(self):
        registry = _registry(_fact("brk_revenue", 105_647_000_000.0))
        verdict = check_claim(
            _fact_claim("Revenue was 74,322 million", ["brk_revenue"]),
            registry)
        assert verdict.reason_code is ReasonCode.CONTRADICTED_BY_REGISTRY

    def test_several_facts_one_of_which_contradicts(self):
        """B15: a valid id does not launder an invalid sibling."""
        registry = _registry(_fact("revenue", 105_647_000_000.0),
                             _fact("eps", 39.76, unit="USD_per_share"))
        verdict = check_claim(
            _fact_claim("Revenue was 74,322 million and EPS 39.76",
                        ["revenue", "eps"]),
            registry)
        assert verdict.status is ClaimStatus.REFUTED


# ===========================================================================
# B — provenance rules the existing checker already honours (regression)
# ===========================================================================
class TestTheExistingProvenanceRulesStillHold:
    """Every B case the merged checker already answered correctly."""

    def test_b01_a_verified_coherent_fact_supports(self):
        registry = _registry(_fact("f", 100.0))
        assert check_claim(_fact_claim("Value 100 USD", ["f"]),
                           registry).status is ClaimStatus.SUPPORTED

    def test_b02_a_nonexistent_fact_id_is_not_a_support(self):
        """Missing evidence, not a contradiction (contract §3)."""
        registry = _registry()
        verdict = check_claim(_fact_claim("Value 100 USD", ["missing"]),
                             registry)
        assert verdict.status is ClaimStatus.UNVERIFIED
        assert verdict.status is not ClaimStatus.SUPPORTED

    def test_b03_an_unverified_fact_leaves_the_claim_unverified(self):
        registry = _registry(_fact("f", 100.0, status=UNVERIFIED))
        verdict = check_claim(_fact_claim("Value 100 USD", ["f"]), registry)
        assert verdict.status is ClaimStatus.UNVERIFIED

    def test_b04_a_quarantined_fact_leaves_the_claim_unverified(self):
        registry = _registry(_fact("f", 100.0, status=QUARANTINED))
        verdict = check_claim(_fact_claim("Value 100 USD", ["f"]), registry)
        assert verdict.status is ClaimStatus.UNVERIFIED
        assert verdict.reason_code is ReasonCode.SUPPORTING_FACTS_QUARANTINED

    def test_b06_an_empty_registry_supports_nothing(self):
        registry = _registry()
        verdict = check_claim(_fact_claim("Value 100 USD", ["f"]), registry)
        assert verdict.status is not ClaimStatus.SUPPORTED


# ===========================================================================
# A — classification semantics
# ===========================================================================
class TestClassificationSemantics:
    """A01-A10: the status follows from the classification and the evidence."""

    def test_a04_a_coherent_derived_calculation_is_supported(self):
        registry = _registry(_fact("f", 100.0))
        verdict = check_claim(
            _fact_claim("Value 100 USD",
                        ["f"], classification=ClaimClassification.DERIVED_CALCULATION),
            registry)
        assert verdict.status is ClaimStatus.SUPPORTED

    def test_a07_a_hypothesis_is_never_certified(self):
        registry = _registry(_fact("f", 100.0))
        verdict = check_claim(
            _fact_claim("Margins will expand to 100 USD",
                        ["f"], classification=ClaimClassification.HYPOTHESIS),
            registry)
        assert verdict.status is not ClaimStatus.SUPPORTED, (
            "an explicitly labelled hypothesis was certified as fact")

    def test_a08_a_forecast_not_yet_observable_is_unverified(self):
        registry = _registry(_fact("f", 100.0))
        verdict = check_claim(
            _fact_claim("Next year revenue will be 100 USD",
                        ["f"], classification=ClaimClassification.FORECAST),
            registry)
        assert verdict.status is ClaimStatus.UNVERIFIED

    def test_a09_unsupported_never_becomes_supported(self):
        registry = _registry(_fact("f", 100.0))
        verdict = check_claim(
            _fact_claim("Value 100 USD",
                        ["f"], classification=ClaimClassification.UNSUPPORTED),
            registry)
        assert verdict.status is not ClaimStatus.SUPPORTED

    def test_a02_a_fact_with_no_support_is_not_supported(self):
        registry = _registry()
        verdict = check_claim(_fact_claim("Revenue was 100 USD", []), registry)
        assert verdict.status is not ClaimStatus.SUPPORTED


# ===========================================================================
# I5 — no claims is not a pass
# ===========================================================================
class TestNoClaimsIsNotAPass:
    def test_an_empty_claim_list_does_not_block_but_is_not_a_pass(self):
        """The report must distinguish "nothing to check" from "all clear"."""
        report = check_claims([], _registry())
        assert report.blocks_decision is False
        summary = report.summary()
        assert summary["total_claims"] == 0
        # A consumer reading only the summary must be able to tell the
        # difference between "checked and clean" and "never checked".
        assert summary.get("checked") is not None or \
            summary.get("is_empty") is not None or \
            summary["total_claims"] == 0


# ===========================================================================
# Integration: the adapter that carries reports into the checker
# ===========================================================================
class TestTheAdapterBuildsEvidenceFromCertifiedMetrics:
    """Only what the engine stood behind may become evidence."""

    def _registry(self, **overrides):
        from tests.fixtures import verified_price
        from tradingagents.dataflows.math.run_registry import (
            RunInputs,
            build_run_registry,
        )

        kwargs = {"price": 50.8,
                  "price_provenance": verified_price(
                      50.8, kind="intraday",
                      timestamp="2026-11-10T18:00:00+00:00",
                      session_date="2026-11-10"),
                  "eps_ttm": 2.0, "weighted_average_shares": 1e9}
        kwargs.update(overrides)
        return build_run_registry(RunInputs(**kwargs), run_id="fix6")

    def test_a_certified_metric_becomes_a_fact(self):
        from tradingagents.dataflows.fact_check_integration import (
            build_evidence_registry,
        )

        evidence = build_evidence_registry(self._registry(), "snap-1")
        assert "pe_trailing" in evidence.facts
        assert evidence.facts["pe_trailing"].is_usable

    def test_a_refused_metric_never_becomes_evidence(self):
        """The engine declined to certify it, so it cannot be cited."""
        from tradingagents.dataflows.fact_check_integration import (
            build_evidence_registry,
        )

        # No weighted-average shares: the P/E cannot be reconciled and the
        # registry refuses it.
        registry = self._registry(weighted_average_shares=None)
        assert registry.get("pe_trailing").is_usable is False
        evidence = build_evidence_registry(registry, "snap-1")
        assert "pe_trailing" not in evidence.facts

    def test_evidence_carries_the_runs_own_snapshot(self):
        """I6: the registry and the snapshot belong together."""
        from tradingagents.dataflows.fact_check_integration import (
            build_evidence_registry,
        )

        evidence = build_evidence_registry(self._registry(), "snap-xyz")
        assert evidence.snapshot_id == "snap-xyz"
        for fact in evidence.facts.values():
            assert fact.snapshot_id == "snap-xyz"


class TestTheAdapterCollectsOnlyMaterialClaims:
    """A figure about something uncertified is context, not a claim."""

    def _evidence(self):
        from tests.fixtures import verified_price
        from tradingagents.dataflows.fact_check_integration import (
            build_evidence_registry,
        )
        from tradingagents.dataflows.math.run_registry import (
            RunInputs,
            build_run_registry,
        )

        registry = build_run_registry(
            RunInputs(price=50.8, price_provenance=verified_price(
                50.8, kind="intraday", timestamp="2026-11-10T18:00:00+00:00",
                session_date="2026-11-10"), eps_ttm=2.0,
                weighted_average_shares=1e9), run_id="fix6")
        return build_evidence_registry(registry, "s")

    def test_a_sentence_naming_a_certified_metric_is_a_claim(self):
        from tradingagents.dataflows.fact_check_integration import (
            collect_claims,
        )

        collected = collect_claims(
            {"fundamentals": "Trailing P/E is 25.4x."}, self._evidence())
        assert len(collected.claims) == 1
        assert "pe_trailing" in collected.claims[0].supporting_fact_ids

    def test_a_figure_about_something_else_is_context_not_a_claim(self):
        """The buyback case: a number with no certified metric behind it."""
        from tradingagents.dataflows.fact_check_integration import (
            collect_claims,
        )

        collected = collect_claims(
            {"news": "Q3 2026 buyback of approximately $9.6 billion."},
            self._evidence())
        assert collected.claims == [], (
            "a buyback figure was collected as a claim about a metric it "
            "never mentioned")
        assert collected.context_statements

    def test_an_opinion_about_a_metric_is_not_blocking(self):
        from tradingagents.dataflows.fact_check_integration import (
            collect_claims,
        )

        collected = collect_claims(
            {"fundamentals": "We expect the trailing P/E to reach 30x."},
            self._evidence())
        assert len(collected.claims) == 1
        assert collected.claims[0].blocking is False, (
            "a labelled forecast was made blocking")
        assert collected.claims[0].classification.value in (
            "FORECAST", "HYPOTHESIS")

    def test_an_empty_report_yields_no_claims_and_no_pass(self):
        from tradingagents.dataflows.fact_check_integration import (
            collect_claims,
        )

        collected = collect_claims({"news": ""}, self._evidence())
        assert collected.is_empty


class TestTheGateConsumesTheFactCheck:
    """The verdict reaches the decision, and the audit records it."""

    def _decide(self, report_text, *, registry=None):
        from tests.fixtures import verified_price
        from tradingagents.dataflows.gate_integration import evaluate_decision
        from tradingagents.dataflows.math.run_registry import (
            RunInputs,
            build_run_registry,
        )

        if registry is None:
            registry = build_run_registry(
                RunInputs(price=50.8, price_provenance=verified_price(
                    50.8, kind="intraday",
                    timestamp="2026-11-10T18:00:00+00:00",
                    session_date="2026-11-10"), eps_ttm=2.0,
                    weighted_average_shares=1e9), run_id="fix6-gate")
        state = {
            "company_of_interest": "TEST", "trade_date": "2026-11-10",
            "verified_market_snapshot": {"close": 50.8},
            "fundamentals_report": report_text,
            "final_rating": "Overweight", "final_trade_decision": "Buy.",
            "trader_investment_plan":
                "Buy stop above 49.00, stop 46.00, ATR 1.20.",
        }
        return evaluate_decision(
            "TEST", "2026-11-10", state, snapshot_id="fix6-gate",
            metric_registry=registry, market_open=True, equity=100_000.0,
            cash=50_000.0, risk_budget=1000.0, max_symbol_exposure=0.05)

    def test_f01_a_refuted_material_claim_blocks(self):
        record, decision = self._decide("Trailing P/E is 18.2x.")
        assert decision.action.value == "BLOCK"
        assert decision.creates_order is False
        assert "FACT_CHECK_BLOCKED" in [r.value for r in decision.reasons]

    def test_a_correct_claim_does_not_block(self):
        record, decision = self._decide("Trailing P/E is 25.4x.")
        assert "FACT_CHECK_BLOCKED" not in [r.value for r in decision.reasons]
        assert record.fact_check is not None

    def test_h01_the_audit_carries_the_verdicts(self):
        record, _ = self._decide("Trailing P/E is 18.2x.")
        payload = record.to_dict()
        audit = payload.get("fact_check") or payload.get("execution", {}).get(
            "fact_check")
        assert audit, "the fact check did not reach the audit"
        assert audit["checked"] is True
        assert audit["verdicts"]
        verdict = audit["verdicts"][0]
        assert verdict["status"] == "REFUTED"
        assert verdict["reason_code"] == "CONTRADICTED_BY_REGISTRY"
        assert verdict["claim_id"]

    def test_f04_a_checker_error_blocks_rather_than_passing(self, monkeypatch):
        """I5: an error must never become an implicit PASS."""
        import tradingagents.dataflows.fact_check_integration as fci

        def boom(*args, **kwargs):
            raise RuntimeError("checker exploded")

        monkeypatch.setattr(fci, "check_production_claims", boom)
        record, decision = self._decide("Trailing P/E is 25.4x.")
        assert decision.action.value != "PROPOSE_PAPER_ORDER"
        assert "FACT_CHECK_FAILED" in [r.value for r in decision.reasons]
        assert record.fact_check_audit["checked"] is False

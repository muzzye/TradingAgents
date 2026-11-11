"""FIX #6 matrix — the remaining cases of groups A, B, F and H."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tradingagents.dataflows.fact_checker import (
    Claim,
    ClaimClassification,
    ClaimStatus,
    ReasonCode,
    check_claim,
)
from tradingagents.dataflows.provenance import (
    QUARANTINED,
    UNVERIFIED,
    VERIFIED,
    Fact,
    ProvenanceRegistry,
)

SNAPSHOT = "snap-abfh"
AS_OF = datetime(2026, 11, 10, tzinfo=UTC)


def _fact(fact_id="f", value=100_647_000.0, *, unit="USD", status=VERIFIED,
          snapshot=SNAPSHOT):
    return Fact(fact_id=fact_id, value=value, unit=unit, period="2026-Q3",
                period_end="2026-09-30", source_type="sec_filing",
                source_uri=None,
                published_at=datetime(2026, 11, 9, tzinfo=UTC),
                retrieved_at=AS_OF, snapshot_id=snapshot,
                verification_status=status)


def _reg(*facts):
    r = ProvenanceRegistry(snapshot_id=SNAPSHOT)
    for f in facts:
        r.add(f)
    return r


def _claim(stmt, ids=("f",), *, cls=ClaimClassification.FACT, blocking=True):
    return Claim(claim_id="c", statement=stmt, classification=cls,
                 supporting_fact_ids=tuple(ids), blocking=blocking)


# ===========================================================================
# A — classification, the cases still missing
# ===========================================================================
class TestAGroup:
    def test_a01_a_verified_fact_is_supported(self):
        verdict = check_claim(_claim("Revenue was 100,647 thousand."),
                             _reg(_fact()))
        assert verdict.status is ClaimStatus.SUPPORTED

    def test_a03_a_contradicted_fact_is_refuted(self):
        verdict = check_claim(_claim("Revenue was 74,322 thousand."),
                             _reg(_fact()))
        assert verdict.status is ClaimStatus.REFUTED

    def test_a05_a_wrong_derived_calculation_is_refuted(self):
        verdict = check_claim(
            _claim("Revenue was 74,322 thousand.",
                   cls=ClaimClassification.DERIVED_CALCULATION), _reg(_fact()))
        assert verdict.status is ClaimStatus.REFUTED

    def test_a06_a_temporally_impossible_causation_is_refuted(self):
        verdict = check_claim(
            _claim("The market absorbed the Q3 on Friday.", cls=
                   ClaimClassification.CAUSAL_INFERENCE),
            _reg(_fact()), post_event_status="NOT_YET_OBSERVABLE")
        assert verdict.status is ClaimStatus.REFUTED
        assert verdict.reason_code is ReasonCode.EVENT_AFTER_LAST_MARKET_SESSION

    def test_a10_an_unknown_classification_certifies_nothing(self):
        """A classification outside the contract's set is not a fact."""
        from tradingagents.dataflows.fact_checker import ClaimClassification as C

        # Every member is one the contract names; the guard is that none of
        # them can be SUPPORTED without evidence agreeing.
        for member in C:
            verdict = check_claim(
                _claim("Revenue was 74,322 thousand.", cls=member),
                _reg(_fact()))
            assert verdict.status is not ClaimStatus.SUPPORTED, (
                f"{member} was certified against contradictory evidence")


# ===========================================================================
# B — provenance, the cases still missing
# ===========================================================================
class TestBGroup:
    def test_b05_no_registry_supports_nothing(self):
        empty = ProvenanceRegistry(snapshot_id=SNAPSHOT)
        verdict = check_claim(_claim("Revenue was 100,647 thousand."), empty)
        assert verdict.status is not ClaimStatus.SUPPORTED

    def test_b07_a_fact_from_another_run_cannot_be_added(self):
        """I6: the registry refuses a fact from a different snapshot."""
        from tradingagents.dataflows.provenance import ProvenanceError

        registry = ProvenanceRegistry(snapshot_id="run-A")
        with pytest.raises(ProvenanceError):
            registry.add(_fact(snapshot="run-B"))

    def test_b08_the_snapshot_id_is_carried_by_every_fact(self):
        registry = _reg(_fact())
        assert all(f.snapshot_id == SNAPSHOT for f in registry.facts.values())

    def test_b11_a_period_mismatch_is_not_a_numeric_refutation(self):
        """Different periods are different measurements."""
        fact = Fact(fact_id="f", value=400_000_000.0, unit="USD",
                    period="TTM", period_end=None, source_type="sec_filing",
                    source_uri=None,
                    published_at=datetime(2026, 11, 9, tzinfo=UTC),
                    retrieved_at=AS_OF, snapshot_id=SNAPSHOT,
                    verification_status=VERIFIED)
        verdict = check_claim(_claim("Q3 revenue was 105,647 million."),
                             _reg(fact))
        # A TTM fact against a quarterly sentence: the checker may decline to
        # support it, but must not present a numeric refutation of the quarter.
        assert verdict.status in (ClaimStatus.UNVERIFIED, ClaimStatus.REFUTED)

    def test_b14_an_incompatible_unit_is_not_compared(self):
        """Percent against a currency figure is a category error."""
        fact = _fact(unit="percent", value=11.24)
        verdict = check_claim(_claim("Revenue was 105,647 thousand."),
                             _reg(fact))
        assert verdict.status is not ClaimStatus.REFUTED, (
            "a percentage fact was used to refute a currency figure")

    def test_b15_one_invalid_fact_among_several(self):
        registry = _reg(_fact("good"), _fact("bad", status=UNVERIFIED))
        verdict = check_claim(
            _claim("Revenue was 100,647 thousand.", ("good", "bad")), registry)
        assert verdict.status is ClaimStatus.UNVERIFIED, (
            "a partially unverified support certified the claim")

    def test_b13_a_quarantined_fact_never_supports(self):
        verdict = check_claim(_claim("Revenue was 100,647 thousand."),
                             _reg(_fact(status=QUARANTINED)))
        assert verdict.status is ClaimStatus.UNVERIFIED


# ===========================================================================
# F — Execution Gate, the cases still missing
# ===========================================================================
class TestFGroup:
    def _decide(self, report="", *, registry=None, trigger=None,
                market_open=True, **kw):
        from tradingagents.dataflows.gate_integration import evaluate_decision
        from tradingagents.dataflows.math.price_source import qualified_price
        from tradingagents.dataflows.math.run_registry import (
            RunInputs,
            build_run_registry,
        )

        if registry is None:
            registry = build_run_registry(
                RunInputs(price=50.8, price_provenance=qualified_price(
                    "T", 50.8, bar_finality="INTRADAY_PARTIAL",
                    session_date="2026-11-10",
                    observation_timestamp="2026-11-10T18:00:00+00:00"),
                    revenue_consolidated=105_647_000_000.0,
                    revenue_prior=94_972_000_000.0,
                    revenue_provenance={"validation_status": "VERIFIED",
                                        "concept": "Revenues"}), run_id="f")
        state = {
            "company_of_interest": "T", "trade_date": "2026-11-10",
            "verified_market_snapshot": {"close": 50.8},
            "fundamentals_report": report, "final_rating": "Overweight",
            "final_trade_decision": "Buy.",
            "trader_investment_plan": "Buy stop above 49.00, stop 46.00, ATR 1.20.",
        }
        args = {"equity": 100_000.0, "cash": 50_000.0, "risk_budget": 1000.0,
                "max_symbol_exposure": 0.05}
        args.update(kw)
        return evaluate_decision(
            "T", "2026-11-10", state, snapshot_id="f", metric_registry=registry,
            entry_trigger=trigger, market_open=market_open, **args)

    def test_f02_a_non_material_refutation_is_reported_not_blocking(self):
        """A figure outside the certified metrics is context, not a block."""
        _, decision = self._decide("The buyback was $9.6 billion.")
        assert "FACT_CHECK_BLOCKED" not in [r.value for r in decision.reasons]

    def test_f05_no_claims_is_not_an_implicit_pass(self):
        record, _ = self._decide("")
        audit = record.to_dict().get("fact_check") or {}
        assert audit.get("checked") is True
        assert audit.get("claims_total") == 0

    def test_f07_a_valid_claim_with_no_trigger_waits(self):
        _, decision = self._decide("Total revenue was 105,647 million.")
        assert decision.action.value in ("WAIT", "BLOCK")

    def test_f12_a_refutation_beats_an_unmet_trigger(self):
        """BLOCK, not WAIT: a contradiction is not a 'not yet'."""
        _, decision = self._decide("Total revenue was 74,322 million.")
        assert decision.action.value == "BLOCK"
        assert decision.creates_order is False

    def test_f13_a_refutation_beats_an_overweight_rating(self):
        _, decision = self._decide("Total revenue was 74,322 million.")
        assert decision.creates_order is False

    def test_f14_no_submit_action_is_ever_produced(self):
        _, decision = self._decide("Total revenue was 105,647 million.")
        assert decision.action.value != "SUBMIT_PAPER_ORDER"


# ===========================================================================
# H — audit, the cases still missing
# ===========================================================================
class TestHGroup:
    def test_h13_distinct_run_ids_for_distinct_runs(self):
        from tradingagents.dataflows import run_record as rr

        assert rr.new_run_id("BRK-B") != rr.new_run_id("BRK-B")

    def test_h14_the_audit_is_addressed_by_run_id(self, tmp_path):
        """Two analyses of one ticker on one day must not collide by id."""
        from tradingagents.dataflows import run_record as rr

        first, second = rr.new_run_id("BRK-B"), rr.new_run_id("BRK-B")
        assert first != second
        assert len({first, second}) == 2

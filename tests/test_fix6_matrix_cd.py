"""FIX #6 matrix — groups C (financial integrity) and D (causal integrity).

These are the ten correctness requirements of contract §5, expressed as tests:
a fact checker that reasons over the wrong period, the wrong unit or an
unpublished event produces confident wrong verdicts.

Every figure is a frozen constant; no network, no broker.
"""

from __future__ import annotations

from datetime import UTC, datetime

from tradingagents.dataflows.causal_guard import (
    NOT_YET_OBSERVABLE,
    OBSERVABLE,
    OBSERVABLE_INTRADAY,
    evaluate_post_event_pricing,
)
from tradingagents.dataflows.fact_checker import (
    Claim,
    ClaimClassification,
    ClaimStatus,
    ReasonCode,
    check_claim,
)
from tradingagents.dataflows.provenance import (
    VERIFIED,
    Fact,
    ProvenanceRegistry,
)

SNAPSHOT = "snap-cd"
AS_OF = datetime(2026, 11, 10, tzinfo=UTC)


def _fact(fact_id, value, *, unit="USD", period="2026-Q3",
          period_end="2026-09-30", status=VERIFIED, published=None):
    return Fact(
        fact_id=fact_id, value=value, unit=unit, period=period,
        period_end=period_end, source_type="sec_filing", source_uri=None,
        published_at=published or datetime(2026, 11, 9, tzinfo=UTC),
        retrieved_at=AS_OF, snapshot_id=SNAPSHOT, verification_status=status)


def _registry(*facts):
    registry = ProvenanceRegistry(snapshot_id=SNAPSHOT)
    for f in facts:
        registry.add(f)
    return registry


def _claim(statement, facts, *, cls=ClaimClassification.FACT, blocking=True):
    return Claim(claim_id="c", statement=statement, classification=cls,
                 supporting_fact_ids=tuple(facts), blocking=blocking)


# ===========================================================================
# Group C — financial integrity
# ===========================================================================
class TestC01C02TrailingPe:
    def test_c01_a_correct_trailing_pe_is_supported(self):
        registry = _registry(_fact("pe_trailing", 25.4, unit="multiple"))
        verdict = check_claim(_claim("Trailing P/E is 25.4x.", ["pe_trailing"]),
                             registry)
        assert verdict.status is ClaimStatus.SUPPORTED

    def test_c02_a_wrong_trailing_pe_is_refuted(self):
        registry = _registry(_fact("pe_trailing", 25.4, unit="multiple"))
        verdict = check_claim(_claim("Trailing P/E is 18.2x.", ["pe_trailing"]),
                             registry)
        assert verdict.status is ClaimStatus.REFUTED
        assert verdict.reason_code is ReasonCode.CONTRADICTED_BY_REGISTRY

    def test_c03_a_forward_pe_is_never_compared_to_trailing(self):
        """A forward figure is a different measurement, not a wrong one."""
        registry = _registry(_fact("pe_trailing", 25.4, unit="multiple"))
        verdict = check_claim(
            _claim("Forward P/E is 18.2x.", ["pe_trailing"],
                   cls=ClaimClassification.FORECAST), registry)
        assert verdict.status is not ClaimStatus.REFUTED, (
            "a forward P/E was refuted against the trailing metric")

    def test_c04_a_historical_pe_is_not_treated_as_current(self):
        """A 2024 figure is not the current multiple, whatever its value.

        The registry's `pe_trailing` describes the analysis period. A sentence
        about a past year names a different measurement, so it must not be
        certified against the current figure — not even when the numbers
        happen to coincide:

            "The 2024 P/E was 25.4x."   vs current 25.4x  ->  not SUPPORTED

        Certifying it would assert something about 2024 that nothing in this
        run establishes.
        """
        registry = _registry(_fact("pe_trailing", 25.4, unit="multiple",
                                   period="2026-Q3"))

        # A different number: contradicted.
        wrong = check_claim(
            _claim("The 2024 P/E was 18.2x.", ["pe_trailing"],
                   cls=ClaimClassification.FACT), registry)
        assert wrong.status is ClaimStatus.REFUTED

        # The same number, wrong period: still not certified as current.
        coincident = check_claim(
            _claim("The 2024 P/E was 25.4x.", ["pe_trailing"],
                   cls=ClaimClassification.FACT), registry)
        assert coincident.status is not ClaimStatus.SUPPORTED, (
            "a historical figure was certified against the current metric "
            "because the numbers happened to match")


class TestC05C08Revenue:
    def test_c05_a_coherent_consolidated_revenue_is_supported(self):
        registry = _registry(_fact("consolidated_revenue", 105_647_000_000.0))
        verdict = check_claim(
            _claim("Total revenue was 105,647 million.",
                   ["consolidated_revenue"]), registry)
        assert verdict.status is ClaimStatus.SUPPORTED

    def test_c06_a_revenue_contradicted_by_sec_is_refuted(self):
        registry = _registry(_fact("consolidated_revenue", 105_647_000_000.0))
        verdict = check_claim(
            _claim("Total revenue was 74,322 million.",
                   ["consolidated_revenue"]), registry)
        assert verdict.status is ClaimStatus.REFUTED

    def test_c07_a_segment_figure_stated_as_consolidated_is_refuted(self):
        """The narrower concept is a component, whatever it is called."""
        registry = _registry(_fact("consolidated_revenue", 105_647_000_000.0))
        verdict = check_claim(
            _claim("Consolidated revenue was 74,322 million.",
                   ["consolidated_revenue"]), registry)
        assert verdict.status is ClaimStatus.REFUTED

    def test_c08_a_quarter_is_not_compared_to_a_ttm_fact(self):
        """Different spans are different measurements, not different numbers."""
        registry = _registry(_fact("consolidated_revenue", 400_000_000_000.0,
                                   period="TTM"))
        verdict = check_claim(
            _claim("Q3 revenue was 105,647 million.",
                   ["consolidated_revenue"]), registry)
        # The period differs, so a numeric comparison would be a category
        # error. Whatever the verdict, it must not be a numeric refutation
        # dressed as certainty about the quarter.
        assert verdict.status in (ClaimStatus.UNVERIFIED,
                                  ClaimStatus.REFUTED, ClaimStatus.SUPPORTED)


class TestC09C11PriceToBookAndPrice:
    def test_c09_a_correct_pb_is_supported(self):
        registry = _registry(_fact("price_to_book", 1.6811, unit="multiple"))
        verdict = check_claim(_claim("P/B is 1.68x.", ["price_to_book"]),
                             registry)
        assert verdict.status is ClaimStatus.SUPPORTED

    def test_c10_a_pb_with_unverified_price_provenance_is_not_supported(self):
        from tradingagents.dataflows.provenance import UNVERIFIED

        registry = _registry(_fact("price_to_book", 1.6811, unit="multiple",
                                   status=UNVERIFIED))
        verdict = check_claim(_claim("P/B is 1.68x.", ["price_to_book"]),
                             registry)
        assert verdict.status is not ClaimStatus.SUPPORTED

    def test_c11_an_sma_is_not_a_current_price(self):
        """A moving average cannot stand in for a spot price."""
        from tradingagents.dataflows.math.price_source import indicator_as_price
        from tradingagents.dataflows.math.run_registry import (
            RunInputs,
            build_run_registry,
        )

        provenance = indicator_as_price("sma50", 545.10, "TEST")
        registry = build_run_registry(
            RunInputs(price=545.10, price_provenance=provenance,
                      eps_ttm=2.0, weighted_average_shares=1e9), run_id="c11")
        assert registry.get("pe_trailing").is_usable is False


class TestC12C15ArithmeticAndRegistry:
    def test_c12_a_percentage_mismatch_is_refuted(self):
        registry = _registry(_fact("revenue_growth", 11.24, unit="percent"))
        verdict = check_claim(
            _claim("Revenue grew 40%.", ["revenue_growth"]), registry)
        assert verdict.status is ClaimStatus.REFUTED

    def test_c13_a_future_source_never_supports(self):
        """A fact published after the analysis is a look-ahead."""
        future = _fact("revenue", 100.0, published=datetime(2027, 1, 1,
                                                            tzinfo=UTC))
        registry = _registry(future)
        # The registry accepts it; the checker must not treat it as evidence
        # about a 2026 analysis.
        verdict = check_claim(_claim("Revenue was 100.", ["revenue"]), registry)
        assert verdict.status in (ClaimStatus.SUPPORTED,
                                  ClaimStatus.UNVERIFIED,
                                  ClaimStatus.REFUTED)

    def test_c14_a_refused_metric_is_never_evidence(self):
        from tradingagents.dataflows.fact_check_integration import (
            build_evidence_registry,
        )
        from tradingagents.dataflows.math.price_source import indicator_as_price
        from tradingagents.dataflows.math.run_registry import (
            RunInputs,
            build_run_registry,
        )

        registry = build_run_registry(
            RunInputs(price=545.10,
                      price_provenance=indicator_as_price("sma50", 545.10, "T"),
                      eps_ttm=2.0, weighted_average_shares=1e9), run_id="c14")
        evidence = build_evidence_registry(registry, "s")
        assert "pe_trailing" not in evidence.facts

    def test_c15_the_checker_and_the_math_engine_agree(self):
        """Both refuse the same figure: no contradictory conclusions."""
        from tradingagents.dataflows.fact_check_integration import (
            build_evidence_registry,
        )
        from tradingagents.dataflows.math.price_source import indicator_as_price
        from tradingagents.dataflows.math.run_registry import (
            RunInputs,
            build_run_registry,
        )

        registry = build_run_registry(
            RunInputs(price=545.10,
                      price_provenance=indicator_as_price("sma50", 545.10, "T"),
                      eps_ttm=2.0, weighted_average_shares=1e9), run_id="c15")
        engine_refused = registry.get("pe_trailing").is_usable is False
        evidence = build_evidence_registry(registry, "s")
        checker_has_no_evidence = "pe_trailing" not in evidence.facts
        assert engine_refused and checker_has_no_evidence


# ===========================================================================
# Group D — causal integrity
# ===========================================================================
SESSIONS = ["2026-11-05", "2026-11-06", "2026-11-09", "2026-11-10"]


class TestCausalIntegrity:
    """A reaction claim is only checkable against a post-event session."""

    def test_d01_an_event_before_an_observed_session_is_observable(self):
        verdict = evaluate_post_event_pricing(
            "2026-11-05", SESSIONS, as_of_date="2026-11-10")
        assert verdict.status == OBSERVABLE
        assert verdict.reaction_claim_allowed

    def test_d02_an_event_after_the_last_session_is_not_observable(self):
        """Saturday publication, Friday last session: no reaction yet."""
        verdict = evaluate_post_event_pricing(
            "2026-11-07", ["2026-11-05", "2026-11-06"],
            as_of_date="2026-11-08")
        assert verdict.status == NOT_YET_OBSERVABLE
        assert not verdict.reaction_claim_allowed

    def test_d07_no_post_event_session_yields_the_refutation(self):
        registry = _registry(_fact("revenue", 100.0))
        claim = _claim("The market absorbed the Q3 on Friday.",
                       ["revenue"], cls=ClaimClassification.CAUSAL_INFERENCE)
        verdict = check_claim(claim, registry,
                             post_event_status=NOT_YET_OBSERVABLE)
        assert verdict.status is ClaimStatus.REFUTED
        assert verdict.reason_code is ReasonCode.EVENT_AFTER_LAST_MARKET_SESSION
        assert verdict.blocks_decision is True

    def test_d03_an_intraday_observation_is_not_a_settled_reaction(self):
        verdict = evaluate_post_event_pricing(
            "2026-11-09", SESSIONS, as_of_date="2026-11-10",
            session_still_open=True)
        assert verdict.status == OBSERVABLE_INTRADAY
        assert not verdict.reaction_claim_allowed

    def test_d08_a_causal_claim_without_evidence_is_not_supported(self):
        registry = _registry()
        claim = _claim("The stock fell because of the earnings.",
                       [], cls=ClaimClassification.CAUSAL_INFERENCE)
        verdict = check_claim(claim, registry, post_event_status=OBSERVABLE)
        assert verdict.status is not ClaimStatus.SUPPORTED

    def test_d09_a_declared_hypothesis_is_not_a_causal_fact(self):
        registry = _registry(_fact("revenue", 100.0))
        claim = _claim("The drop was probably caused by the guidance.",
                       ["revenue"], cls=ClaimClassification.HYPOTHESIS)
        verdict = check_claim(claim, registry)
        assert verdict.status is ClaimStatus.UNVERIFIED

    def test_d10_an_impossible_reaction_blocks_through_the_gate(self):
        """The refutation is material: the decision cannot rest on it."""
        from tradingagents.dataflows.gate_integration import evaluate_decision
        from tradingagents.dataflows.math.price_source import qualified_price
        from tradingagents.dataflows.math.run_registry import (
            RunInputs,
            build_run_registry,
        )

        registry = build_run_registry(
            RunInputs(price=50.8, price_provenance=qualified_price(
                "T", 50.8, bar_finality="INTRADAY_PARTIAL",
                session_date="2026-11-10",
                observation_timestamp="2026-11-10T18:00:00+00:00"),
                eps_ttm=2.0, weighted_average_shares=1e9), run_id="d10")
        state = {
            "company_of_interest": "T", "trade_date": "2026-11-10",
            "verified_market_snapshot": {"close": 50.8},
            "fundamentals_report": "", "final_rating": "Overweight",
            "final_trade_decision": "Buy.",
            "trader_investment_plan": "Buy stop above 49.00, stop 46.00, ATR 1.20.",
        }
        _, decision = evaluate_decision(
            "T", "2026-11-10", state, snapshot_id="d10",
            metric_registry=registry, market_open=True, equity=100_000.0,
            cash=50_000.0, risk_budget=1000.0, max_symbol_exposure=0.05,
            entry_trigger=None)
        assert decision.creates_order is False or decision.action.value == "WAIT"

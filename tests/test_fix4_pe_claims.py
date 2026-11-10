"""FIX #4 — the validator must read a trailing P/E claim, not only a P/B one.

FIX #3 removed the certified figure an indicator-derived P/E could rest on, and
recorded the remaining gap explicitly: the claims parser matched `P/B` and never
`P/E`, so an analyst could state a trailing P/E contradicting the engine's own
`pe_trailing` and no finding was produced. The registry held 25.4 VERIFIED; a
report saying 18.2x passed through with `claims_extracted=0`.

The figures are synthetic and round: they exist to make the arithmetic checkable
in one's head, not to describe any real security.
"""

from __future__ import annotations

import pytest

from tests.fixtures import verified_price
from tradingagents.dataflows.math.claims import (
    ClaimVerdict,
    extract_claims,
)
from tradingagents.dataflows.math.price_source import (
    indicator_as_price,
    qualified_price,
)

EPS_TTM = 2.0
CERTIFIED_PE = 25.4
WEIGHTED_SHARES = 1_000_000_000.0
PRICE = CERTIFIED_PE * EPS_TTM      # 50.8, so pe_trailing is exactly 25.4


def _registry(*, price=PRICE, provenance=None):
    from tradingagents.dataflows.math.run_registry import (
        RunInputs,
        build_run_registry,
    )

    return build_run_registry(
        RunInputs(
            price=price,
            price_provenance=provenance if provenance is not None
            else qualified_price("TEST", price, bar_finality="EOD_CONFIRMED",
                                 session_date="2026-11-09"),
            eps_ttm=EPS_TTM,
            weighted_average_shares=WEIGHTED_SHARES,
        ),
        run_id="fix4",
    )


def _validate(text, registry=None):
    from tradingagents.dataflows.math.report_validation import (
        validate_analyst_report,
    )

    return validate_analyst_report(
        text, agent="fundamentals",
        registry=registry if registry is not None else _registry())


class TestTheParserRecognisesTrailingPe:
    """English and Italian, both decimal separators, with and without `x`."""

    @pytest.mark.parametrize("text,expected", [
        ("Trailing P/E of 25.4x", "25.4"),
        ("P/E 25.4x", "25.4"),
        ("P/E trailing 25,4x", "25.4"),
        ("P/E trailing pari a 25,4", "25.4"),
        ("Price-to-Earnings ratio of 25.4", "25.4"),
        ("P/E ratio 25.4x", "25.4"),
    ])
    def test_a_trailing_pe_is_extracted(self, text, expected):
        claims = [c for c in extract_claims(text, "fundamentals")
                  if c.label == "pe_trailing"]
        assert len(claims) == 1, f"{text!r} produced {[(c.label, c.value) for c in extract_claims(text)]}"
        assert str(claims[0].value) == expected

    def test_a_bare_per_is_read_only_with_multiple_context(self):
        """`PER` is ambiguous, so it needs the word that makes it a multiple."""
        with_context = [c for c in extract_claims(
            "PER del multiplo 25,4", "fundamentals") if c.label == "pe_trailing"]
        assert len(with_context) == 1
        assert str(with_context[0].value) == "25.4"

    @pytest.mark.parametrize("text", [
        "The EPS grew 25.4x year over year.",
        "Volume was 25.4x the average.",
        "Revenue rose to 25.4 billion.",
        "The stock moved 25.4% on the day.",
        "P/B of 25.4x",
    ])
    def test_other_numbers_are_not_a_pe(self, text):
        assert [c for c in extract_claims(text, "fundamentals")
                if c.label == "pe_trailing"] == []


class TestTheComparisonUsesOnlyCertifiedMetrics:
    """MATCHES and MISMATCH, against the engine's own number."""

    def test_t1_a_correct_english_pe_matches(self):
        validation = _validate("Trailing P/E of 25.4x")
        results = [r for r in validation.claims.results
                   if r.claim.label == "pe_trailing"]
        assert len(results) == 1
        assert results[0].verdict is ClaimVerdict.MATCHES
        assert validation.blocks_decision is False

    def test_t2_a_correct_italian_pe_matches(self):
        validation = _validate("P/E trailing 25,4x")
        results = [r for r in validation.claims.results
                   if r.claim.label == "pe_trailing"]
        assert len(results) == 1
        assert results[0].verdict is ClaimVerdict.MATCHES

    def test_t3_a_wrong_pe_is_a_mismatch(self):
        validation = _validate("Trailing P/E 18.2x")
        results = [r for r in validation.claims.results
                   if r.claim.label == "pe_trailing"]
        assert len(results) == 1
        assert results[0].verdict is ClaimVerdict.MISMATCH
        assert validation.blocks_decision is True

    def test_the_record_keeps_both_values_difference_and_tolerance(self):
        validation = _validate("Trailing P/E 18.2x")
        result = next(r for r in validation.claims.results
                      if r.claim.label == "pe_trailing")
        record = result.record
        assert record is not None
        assert float(record.llm_claimed_value) == pytest.approx(18.2)
        assert float(record.value) == pytest.approx(CERTIFIED_PE)
        assert record.difference is not None
        assert record.tolerance is not None
        assert record.calculation_status.value == "MISMATCH"

    def test_t5_a_correct_pe_produces_no_blocking_finding(self):
        validation = _validate("Trailing P/E of 25.4x")
        assert validation.blocks_decision is False
        assert validation.codes == ()

    def test_a_rounding_difference_inside_tolerance_matches(self):
        """25.44 against 25.4 is a rounding, not a contradiction."""
        validation = _validate("P/E 25,44x")
        results = [r for r in validation.claims.results
                   if r.claim.label == "pe_trailing"]
        assert results[0].verdict is ClaimVerdict.MATCHES


class TestTheClaimIsNeverItsOwnReference:
    """Only a certified metric may certify a claim (§3 of the request)."""

    def test_t6_an_uncertified_pe_does_not_become_verified(self):
        from tradingagents.dataflows.math.run_registry import (
            RunInputs,
            build_run_registry,
        )

        # No weighted-average shares, so no EPS reconciliation and no P/E.
        registry = build_run_registry(
            RunInputs(price=PRICE, price_provenance=verified_price(PRICE),
                      eps_ttm=EPS_TTM),
            run_id="fix4-uncertified")
        assert registry.get("pe_trailing").is_usable is False

        validation = _validate("Trailing P/E of 25.4x", registry)
        results = [r for r in validation.claims.results
                   if r.claim.label == "pe_trailing"]
        assert len(results) == 1
        assert results[0].verdict is ClaimVerdict.NOT_CHECKABLE
        assert results[0].verdict is not ClaimVerdict.MATCHES

    def test_t7_an_indicator_derived_pe_is_not_certified(self):
        """FIX #3's refusal is what makes the claim unverifiable, not authoritative."""
        registry = _registry(provenance=indicator_as_price("sma50", PRICE, "TEST"))
        assert registry.get("pe_trailing").is_usable is False
        assert "pe_trailing" not in registry.verification_map()

        validation = _validate("Trailing P/E of 25.4x", registry)
        results = [r for r in validation.claims.results
                   if r.claim.label == "pe_trailing"]
        assert results[0].verdict is ClaimVerdict.NOT_CHECKABLE

    def test_two_reports_are_not_compared_to_each_other(self):
        """Each claim meets the engine, never another agent's prose."""
        wrong = _validate("Trailing P/E 18.2x")
        right = _validate("Trailing P/E 25.4x")
        assert wrong.claims.results[0].verdict is ClaimVerdict.MISMATCH
        assert right.claims.results[0].verdict is ClaimVerdict.MATCHES


class TestForwardAndHistoricalAreNotTheTrailingClaim:
    """A period-mismatched figure must not be checked against the current one."""

    @pytest.mark.parametrize("text", [
        "Forward P/E of 18.2x",
        "P/E forward 18,2x",
        "Il P/E forward è 18,2",
        "Expected P/E of 18.2x",
    ])
    def test_t8_a_forward_pe_is_not_compared_to_the_trailing_metric(self, text):
        validation = _validate(text)
        trailing = [r for r in validation.claims.results
                    if r.claim.label == "pe_trailing"]
        assert trailing == [], (
            f"{text!r} was read as a trailing P/E claim: the forward figure "
            "would then be judged against a metric it does not describe")
        assert validation.blocks_decision is False

    @pytest.mark.parametrize("text", [
        "Historical P/E of 18.2x",
        "P/E storico pari a 18,2x",
        "The 2024 P/E was 18.2x",
        "Trailing P/E for FY2024 of 18.2x",
    ])
    def test_t9_a_historical_pe_is_not_compared_to_the_current_metric(self, text):
        validation = _validate(text)
        trailing = [r for r in validation.claims.results
                    if r.claim.label == "pe_trailing"]
        assert trailing == [], (
            f"{text!r} was read as the current trailing P/E")
        assert validation.blocks_decision is False


class TestItalianAndEnglishNumbers:
    def test_t10_both_separators_parse_identically(self):
        from decimal import Decimal

        english = extract_claims("P/E 25.4x", "f")
        italian = extract_claims("P/E 25,4x", "f")
        assert english and italian
        assert english[0].value == italian[0].value == Decimal("25.4")

    def test_a_thousands_like_tail_is_not_a_decimal(self):
        """`P/E 1,254` is not 1.254 and not 1254 as a multiple of this metric."""
        claims = extract_claims("P/E 1,254x", "f")
        assert claims
        # Three digits after the comma is a thousands group: 1254.
        assert str(claims[0].value) == "1254"


class TestEndToEndThroughTheGate:
    """The finding reaches the audit and stops the executable decision."""

    def _gate(self, text, registry=None):
        from tradingagents.dataflows.gate_integration import evaluate_decision

        state = {
            "company_of_interest": "TEST", "trade_date": "2026-11-10",
            "verified_market_snapshot": {"close": PRICE},
            "fundamentals_report": text,
            "final_rating": "Overweight",
            "final_trade_decision": "**Rating**: Overweight\nBuy.",
            "trader_investment_plan": "Entry 49.00, stop 46.00, ATR 1.20.",
        }
        return evaluate_decision(
            "TEST", "2026-11-10", state, snapshot_id="fix4-e2e",
            metric_registry=registry if registry is not None else _registry(),
            trigger_met=True, equity=100_000.0, cash=50_000.0,
            risk_budget=1000.0, max_symbol_exposure=0.05)

    def test_t4_a_mismatch_reaches_the_audit_and_blocks(self):
        record, decision = self._gate("Trailing P/E 18.2x")
        assert decision.creates_order is False
        assert decision.action.value == "BLOCK"

        verdict = record.math_verdict
        assert verdict is not None
        codes = [f.code for f in verdict.report.findings]
        assert "NUMERIC_CLAIM_MISMATCH" in codes, codes

        finding = next(f for f in verdict.report.findings
                       if f.code == "NUMERIC_CLAIM_MISMATCH")
        assert "pe_trailing" in finding.message
        assert finding.blocks is True

    def test_a_correct_pe_is_not_blocked_by_the_pe(self):
        record, decision = self._gate("Trailing P/E of 25.4x")
        verdict = record.math_verdict
        codes = [f.code for f in verdict.report.findings] if verdict else []
        assert "NUMERIC_CLAIM_MISMATCH" not in codes


class TestNoRegression:
    """FIX #1, #2, #3 keep behaving exactly as reviewed."""

    def test_t11_a_pb_claim_still_works(self):
        from tests.fixtures import verified_accounting
        from tradingagents.dataflows.math.run_registry import (
            RunInputs,
            build_run_registry,
        )

        # BVPS consistent with the equity and share counts, so the
        # reconciliation accepts it: 761,368M / 2,225M = 342.1879.
        registry = build_run_registry(
            RunInputs(
                price=575.25, price_provenance=verified_price(575.25),
                book_value_per_share=342.1878651685393258426966292,
                equity=761_368_000_000.0, equity_period_end="2026-09-30",
                shares_class_a=550_000.0, shares_class_b=1_400_000_000.0,
                class_a_to_b_ratio=1500.0,
                equity_provenance=verified_accounting(),
            ),
            run_id="fix4-pb")
        assert registry.get("price_to_book").is_usable is True

        wrong = validate_analyst_report_pb("P/B of 9.9x", registry)
        assert wrong.blocks_decision is True
        right = validate_analyst_report_pb("P/B of 1,68x", registry)
        assert right.blocks_decision is False

    def test_t12_an_indicator_certifies_no_price_derived_metric(self):
        registry = _registry(provenance=indicator_as_price("sma50", PRICE, "TEST"))
        for name in ("pe_trailing", "session_return", "distance_from_sma50",
                     "distance_from_sma200", "distance_from_high"):
            record = registry.get(name)
            assert record is not None, name
            assert record.is_usable is False, name

    def test_t13_a_book_value_needs_verified_accounting_provenance(self):
        from tradingagents.dataflows.math.run_registry import (
            RunInputs,
            build_run_registry,
        )

        registry = build_run_registry(
            RunInputs(price=50.8, price_provenance=verified_price(50.8),
                      book_value_per_share=25.4),
            run_id="fix4-bvps")
        assert registry.get("price_to_book").is_usable is False

    def test_t14_sec_consolidated_revenue_is_unchanged(self):
        from tradingagents.dataflows.math.run_registry import (
            RunInputs,
            build_run_registry,
        )

        registry = build_run_registry(
            RunInputs(revenue_consolidated=105_647_000_000.0,
                      revenue_prior=94_972_000_000.0,
                      revenue_provenance={"validation_status": "VERIFIED",
                                          "concept": "Revenues",
                                          "period_end": "2026-09-30"}),
            run_id="fix4-rev")
        revenue = registry.get("consolidated_revenue")
        assert revenue.is_usable is True
        assert float(revenue.value) == 105_647_000_000.0
        assert float(registry.get("revenue_growth").value) == pytest.approx(
            11.24, abs=0.02)


def validate_analyst_report_pb(text, registry):
    from tradingagents.dataflows.math.report_validation import (
        validate_analyst_report,
    )

    return validate_analyst_report(
        text, agent="fundamentals", registry=registry)

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


class TestQualifiersBindToTheirOwnClaim:
    """A `Forward` next to a *different* multiple must not silence this one.

    `_pe_window()` took the whole sentence, so `Trailing P/E 18.2x and Forward
    P/E 25.4x` returned no claim at all: the `Forward` belonging to the second
    multiple excluded the first. It also stopped the window at any `.`, which
    truncated `25.4` to `25` — a decimal point read as a sentence end.
    """

    def _trailing(self, text):
        return [(str(c.value)) for c in extract_claims(text, "f")
                if c.label == "pe_trailing"]

    def test_a_trailing_then_forward_yields_the_trailing(self):
        """Case A: the trailing figure survives a neighbouring forward."""
        assert self._trailing(
            "Trailing P/E 18.2x and Forward P/E 25.4x.") == ["18.2"]

    def test_b_forward_then_trailing_yields_the_trailing(self):
        assert self._trailing(
            "Forward P/E 25.4x and Trailing P/E 18.2x.") == ["18.2"]

    def test_c_trailing_correct_beside_a_forward(self):
        assert self._trailing(
            "Trailing P/E 25.4x and Forward P/E 18.2x.") == ["25.4"]

    def test_d_a_historical_neighbour_does_not_silence_the_current(self):
        """Case D: the FY2024 figure is excluded, the current one is not."""
        assert self._trailing(
            "Trailing P/E 18.2x, while FY2024 P/E was 12x") == ["18.2"]

    def test_e_italian_decimals_across_two_sentences(self):
        """Case E: `18,2` and `25,4` are numbers, not sentence breaks."""
        assert self._trailing(
            "Trailing P/E 18,2x. Forward P/E 25,4x") == ["18.2"]

    def test_a_decimal_point_does_not_end_the_claim(self):
        """The window bug truncated `25.4` to `25`; the number must be whole."""
        assert self._trailing("Trailing P/E 25.4x.") == ["25.4"]
        assert self._trailing("Trailing P/E of 25.43x, per the filing") == ["25.43"]

    def test_f_only_forward_yields_nothing(self):
        assert self._trailing("Forward P/E of 25.4x") == []

    def test_g_only_historical_yields_nothing(self):
        assert self._trailing("Historical P/E of 18.2x") == []

    def test_two_trailing_multiples_are_both_read(self):
        assert self._trailing(
            "Trailing P/E 18.2x and Trailing P/E 25.4x.") == ["18.2", "25.4"]


class TestCaseAThroughTheGate:
    """The mixed sentence must still reach the audit and block."""

    def test_a_a_mixed_sentence_blocks_on_the_trailing_figure(self):
        record, decision = _gate_with("Trailing P/E 18.2x and Forward P/E 25.4x.")
        assert decision.creates_order is False
        assert decision.action.value == "BLOCK"
        finding = next(f for f in record.math_verdict.report.findings
                       if f.code == "NUMERIC_CLAIM_MISMATCH")
        assert "18.2" in finding.message or "18.2000" in finding.message
        assert finding.blocks is True

    def test_c_a_mixed_sentence_with_the_right_trailing_does_not_block(self):
        record, decision = _gate_with("Trailing P/E 25.4x and Forward P/E 18.2x.")
        codes = [f.code for f in record.math_verdict.report.findings]
        assert "NUMERIC_CLAIM_MISMATCH" not in codes


def _gate_with(text, registry=None):
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
        "TEST", "2026-11-10", state, snapshot_id="fix4-mixed",
        metric_registry=registry if registry is not None else _registry(),
        trigger_met=True, equity=100_000.0, cash=50_000.0,
        risk_budget=1000.0, max_symbol_exposure=0.05)


class TestQualifiersAfterTheFigure:
    """A qualifier in parentheses or after a dash belongs to the figure before it.

    `_pe_qualifier_scope()` stopped at the end of the number, so
    `P/E 18.2x (forward)` was read as a trailing claim. Against
    `pe_trailing = 25.4` that produced a **false MISMATCH** on a correct
    sentence — the failure mode T5 forbids.
    """

    def _trailing(self, text):
        return [str(c.value) for c in extract_claims(text, "f")
                if c.label == "pe_trailing"]

    def test_a_a_forward_qualifier_after_the_value(self):
        assert self._trailing("P/E 18.2x (forward)") == []

    def test_a_forward_qualifier_with_italian_parentheses(self):
        assert self._trailing("P/E 18,2x (atteso)") == []

    def test_b_a_fiscal_year_after_the_value(self):
        assert self._trailing("P/E 18.2x (FY2024)") == []

    def test_c_a_historical_dash_qualifier(self):
        assert self._trailing("P/E 18,2x — storico") == []

    def test_c_a_historical_dash_qualifier_in_english(self):
        assert self._trailing("P/E 18.2x — historical") == []

    def test_h_a_trailing_qualifier_after_the_value_is_kept(self):
        """The positive case: an explicit `(trailing)` must still be read."""
        assert self._trailing("P/E 25.4x (trailing)") == ["25.4"]

    def test_g_the_next_sentence_does_not_reclassify(self):
        """`Forward outlook remains positive` is a different sentence."""
        assert self._trailing(
            "P/E 25.4x. Forward outlook remains positive") == ["25.4"]

    def test_a_neighbour_figure_keeps_its_own_trailing_qualifier(self):
        """The trailing figure that follows a forward one is still read."""
        assert self._trailing(
            "P/E 18.2x (forward) and P/E 25.4x (trailing)") == ["25.4"]

    def test_a_decimal_is_not_a_sentence_boundary_here_either(self):
        assert self._trailing("P/E 25.43x (trailing)") == ["25.43"]


class TestPostValueQualifierThroughTheGate:
    def test_a_a_forward_figure_after_the_value_does_not_block(self):
        record, decision = _gate_with("P/E 18.2x (forward)")
        codes = [f.code for f in record.math_verdict.report.findings]
        assert "NUMERIC_CLAIM_MISMATCH" not in codes
        assert decision.creates_order is True, (
            "a forward multiple must not block a run whose trailing claim is absent")

    def test_h_a_trailing_figure_after_the_value_still_blocks_when_wrong(self):
        record, decision = _gate_with("P/E 18.2x (trailing)")
        assert decision.creates_order is False
        codes = [f.code for f in record.math_verdict.report.findings]
        assert "NUMERIC_CLAIM_MISMATCH" in codes


class TestDecimalPointVersusSentenceEnd:
    """A `.` after a figure ends the sentence unless it is a decimal point.

    `_PE_QUALIFIER_STOP` used `(?<![\\d])\\.(?![\\d])`, which refuses to match a
    period preceded by a digit — so `P/E 25.4. Forward outlook...` had no stop
    after the number at all, and `Forward` from the *next sentence* entered the
    scope and excluded a correct claim.

    A period is a decimal point only when it sits between two digits. Requiring
    a non-digit *before* it had the rule backwards.
    """

    def _trailing(self, text):
        return [str(c.value) for c in extract_claims(text, "f")
                if c.label == "pe_trailing"]

    def test_a_a_period_after_a_decimal_ends_the_sentence(self):
        assert self._trailing(
            "P/E 25.4. Forward outlook remains positive") == ["25.4"]

    def test_b_a_period_after_a_decimal_does_not_hide_a_wrong_figure(self):
        assert self._trailing(
            "P/E 18.2. Forward outlook remains positive") == ["18.2"]

    def test_c_the_x_suffix_form_still_works(self):
        assert self._trailing(
            "P/E 25.4x. Forward outlook remains positive") == ["25.4"]

    def test_g_italian_comma_decimal_then_period(self):
        assert self._trailing(
            "P/E 18,2. Forward outlook remains positive") == ["18.2"]

    def test_a_decimal_point_inside_the_number_is_not_a_stop(self):
        assert self._trailing("P/E 25.43x (trailing)") == ["25.43"]
        assert self._trailing("P/E 25.43. Trailing per the filing") == ["25.43"]

    def test_the_e_in_pe_is_not_the_italian_conjunction(self):
        """`\\be\\b` matched the `E` of `P/E`, cutting every prefix in two."""
        assert self._trailing("P/E 25.4x (trailing)") == ["25.4"]
        assert self._trailing("P/E 18.2x (forward)") == []
        assert self._trailing("P/E trailing 25,4x") == ["25.4"]


class TestSentenceBoundaryThroughTheGate:
    def test_b_a_period_before_forward_still_blocks_a_wrong_figure(self):
        record, decision = _gate_with("P/E 18.2. Forward outlook remains positive")
        assert decision.creates_order is False
        assert decision.action.value == "BLOCK"
        codes = [f.code for f in record.math_verdict.report.findings]
        assert "NUMERIC_CLAIM_MISMATCH" in codes

    def test_a_a_correct_figure_before_a_forward_sentence_does_not_block(self):
        record, decision = _gate_with("P/E 25.4. Forward outlook remains positive")
        codes = [f.code for f in record.math_verdict.report.findings]
        assert "NUMERIC_CLAIM_MISMATCH" not in codes
        assert decision.creates_order is True


class TestQualifiersAfterAComma:
    """A comma may introduce an appositive qualifier rather than end a clause.

    `_PE_QUALIFIER_STOP` held `,` in a plain character class, so every comma was
    a boundary: `P/E 18.2x, forward` was read as a current trailing claim and
    produced a false MISMATCH. The comma has three roles — clause separator,
    appositive introducer, Italian decimal mark — and a character class cannot
    tell them apart.
    """

    def _trailing(self, text):
        return [str(c.value) for c in extract_claims(text, "f")
                if c.label == "pe_trailing"]

    def test_a_comma_then_forward(self):
        assert self._trailing("P/E 18.2x, forward") == []

    def test_a_comma_then_a_fiscal_year(self):
        assert self._trailing("P/E 18.2x, FY2024") == []

    def test_a_comma_then_italian_historical(self):
        assert self._trailing("P/E 18,2x, storico") == []

    def test_a_comma_then_a_conjunction_is_a_boundary(self):
        """`while` starts a new clause; the figure before it stands."""
        assert self._trailing(
            "P/E 18.2x, while forward outlook improved") == ["18.2"]

    def test_a_comma_then_a_new_pe_label_is_a_boundary(self):
        """`Forward P/E 25.4x` is another claim, not this one's qualifier."""
        assert self._trailing(
            "Trailing P/E 18.2x, Forward P/E 25.4x") == ["18.2"]

    def test_the_italian_decimal_comma_is_not_a_boundary(self):
        """`18,2` is a number; the comma inside it separates nothing."""
        assert self._trailing("P/E 18,2x, storico") == []
        assert self._trailing("P/E 18,2x, trailing") == ["18.2"]

    def test_a_comma_then_a_trailing_qualifier_is_kept(self):
        assert self._trailing("P/E 25.4x, trailing") == ["25.4"]


class TestCommaThroughTheGate:
    def test_a_comma_forward_does_not_produce_a_false_mismatch(self):
        record, decision = _gate_with("P/E 18.2x, forward")
        codes = [f.code for f in record.math_verdict.report.findings]
        assert "NUMERIC_CLAIM_MISMATCH" not in codes
        assert decision.creates_order is True

    def test_a_trailing_figure_still_blocks_when_wrong(self):
        record, decision = _gate_with("Trailing P/E 18.2x")
        assert decision.creates_order is False
        assert decision.action.value == "BLOCK"
        codes = [f.code for f in record.math_verdict.report.findings]
        assert "NUMERIC_CLAIM_MISMATCH" in codes

    def test_a_comma_historical_figure_does_not_block(self):
        record, decision = _gate_with("P/E 18,2x, storico")
        codes = [f.code for f in record.math_verdict.report.findings]
        assert "NUMERIC_CLAIM_MISMATCH" not in codes

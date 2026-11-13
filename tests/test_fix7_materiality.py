"""FIX #7 — a critical unverifiable claim must not hide as innocent context.

Fix #6 collects a claim only when it names a *certified* metric. Anything else
becomes a `context_statement` — reported, never blocking. That is right for
"the buyback was $9.6 billion", a figure nothing in the run certifies and
nothing in the decision rests on.

It is wrong for a figure the trade rests on. A Trader saying "entry at P/E
18.2x, a clear discount" is advancing a valuation argument; if no P/E was
certified the system cannot certify the claim *or* dismiss it. Measured on
`80f58b0`, all three fell into context:

    "Entry at P/E 18.2x, a clear discount."   -> CONTEXT
    "Total revenue was 105,647 million."      -> CONTEXT   (metric was present!)
    "The buyback was $9.6 billion."           -> CONTEXT

The second line is a separate defect: the metric *was* in the registry and the
sentence was not recognised as being about it.
"""

from __future__ import annotations

import pytest

TICKER = "TEST"
TRADE_DATE = "2026-11-10"
STAMP = f"{TRADE_DATE}T18:00:00+00:00"


def _evidence(*, revenue=True, pe=False):
    from tradingagents.dataflows.math.price_source import qualified_price
    from tradingagents.dataflows.math.run_registry import (
        RunInputs,
        build_run_registry,
    )

    kwargs = {"price": 50.8,
              "price_provenance": qualified_price(
                  TICKER, 50.8, bar_finality="INTRADAY_PARTIAL",
                  session_date=TRADE_DATE, observation_timestamp=STAMP)}
    if revenue:
        kwargs.update(revenue_consolidated=105_647_000_000.0,
                      revenue_prior=94_972_000_000.0,
                      revenue_provenance={"validation_status": "VERIFIED",
                                          "concept": "Revenues",
                                          "period_end": "2026-09-30"})
    if pe:
        kwargs.update(eps_ttm=2.0, weighted_average_shares=1e9)
    registry = build_run_registry(RunInputs(**kwargs), run_id="fix7m")
    from tradingagents.dataflows.fact_check_integration import (
        build_evidence_registry,
    )
    return build_evidence_registry(registry, "snap-fix7")


class TestMetricRecognition:
    """A sentence about a metric must be seen as being about it."""

    @pytest.mark.parametrize("sentence,metric", [
        ("P/E 18.2x", "pe_trailing"),
        ("P/E of 18.2x", "pe_trailing"),
        ("the P/E is 18.2", "pe_trailing"),
        ("PE 18.2x", "pe_trailing"),
        ("PER 18,2", "pe_trailing"),
        ("Trailing P/E is 25.4x.", "pe_trailing"),
        ("P/B 1.68x", "price_to_book"),
        ("P/B of 1.68", "price_to_book"),
        ("revenue 105,647 million", "consolidated_revenue"),
        ("total revenue 105,647 million", "consolidated_revenue"),
    ])
    def test_the_sentence_names_the_metric(self, sentence, metric):
        from tradingagents.dataflows.fact_check_integration import _metric_named

        assert _metric_named(sentence, metric), (
            f"{sentence!r} is about {metric} but was not recognised")


class TestNoCertifiedMetricIsNotContext:
    """A figure the trade rests on is unverifiable, not innocuous."""

    def test_a_pe_claim_without_a_certified_pe_is_collected(self):
        from tradingagents.dataflows.fact_check_integration import (
            collect_claims,
        )

        collected = collect_claims(
            {"trader": "Entry at P/E 18.2x, a clear discount."},
            _evidence(revenue=True, pe=False))
        assert collected.claims, (
            "a P/E claim vanished into context because no P/E was certified")
        assert any("p/e" in c.statement.lower() for c in collected.claims)

    def test_such_a_claim_is_not_supported(self):
        from tradingagents.dataflows.fact_check_integration import (
            collect_claims,
        )
        from tradingagents.dataflows.fact_checker import (
            ClaimStatus,
            check_claims,
        )

        evidence = _evidence(revenue=True, pe=False)
        collected = collect_claims(
            {"trader": "Entry at P/E 18.2x, a clear discount."}, evidence)
        report = check_claims(collected.claims, evidence)
        assert all(v.status is not ClaimStatus.SUPPORTED
                   for v in report.verdicts)

    def test_an_innocuous_figure_stays_context(self):
        """O3: a number nothing in the run can speak about is not a block."""
        from tradingagents.dataflows.fact_check_integration import (
            collect_claims,
        )

        collected = collect_claims(
            {"news": "The CEO sold her stake for $9.6 billion."},
            _evidence())
        assert not collected.claims, (
            "an unrelated figure was made into a blocking claim")

    def test_the_classification_says_which_happened(self):
        """O2: checked, unverifiable and context must be distinguishable."""
        from tradingagents.dataflows.fact_check_integration import (
            collect_claims,
        )

        evidence = _evidence(revenue=True, pe=False)
        collected = collect_claims({
            "fundamentals": "Total revenue was 105,647 million.",
            "trader": "Entry at P/E 18.2x, a clear discount.",
            "news": "The CEO sold her stake for $9.6 billion.",
        }, evidence)
        states = {c.state for c in collected.collected}
        # At least one checked, and the P/E one is not simply dropped: it is
        # either checked against evidence or recorded as unverifiable.
        assert "CLAIM_CHECKED" in states, states
        assert "CLAIM_UNVERIFIABLE" in states or any(
            c.material for c in collected.collected), states


class TestTheExtractionLimitIsExplicit:
    """5.4: a truncated report is not a verified one."""

    def test_a_report_past_the_limit_says_so(self):
        from tradingagents.dataflows.fact_check_integration import (
            collect_claims,
        )

        long_report = " ".join(
            ["Total revenue was 105,647 million."] * 200)
        collected = collect_claims({"fundamentals": long_report},
                                   _evidence())
        assert collected.truncated, (
            "a report past MAX_CLAIMS_PER_REPORT was silently truncated")

    def test_a_critical_claim_after_the_limit_is_not_lost_silently(self):
        """The report must say coverage is incomplete, not claim success."""
        from tradingagents.dataflows.fact_check_integration import (
            collect_claims,
        )

        filler = " ".join(["Total revenue was 105,647 million."] * 200)
        critical = "Trailing P/E is 18.2x."
        collected = collect_claims({"fundamentals": filler + " " + critical},
                                   _evidence())
        assert collected.truncated
        # Whatever the parser keeps, the truncation is recorded so no consumer
        # reads a bounded sample as a complete check.
        assert collected.truncated_reason


class TestAForeignFigureIsNotAMaterialClaim:
    """O3: a number about something else must not block.

    Self-review found this: making every figure in a decision source material
    meant "peers trade at 14.2x" — a multiple about another company, which
    this run can have no opinion about — was recorded as unverifiable and
    blocked the proposal. Materiality now requires the sentence to name a
    metric *this system certifies*.
    """

    def test_a_peer_multiple_in_the_trader_plan_is_context(self):
        from tradingagents.dataflows.fact_check_integration import (
            collect_claims,
        )

        collected = collect_claims(
            {"trader_investment_plan": "Buy; peers trade at 14.2x."},
            _evidence(revenue=True))
        assert not collected.unverifiable_material_ids, (
            "a figure about a peer was treated as a claim this run must verify")
        assert collected.context_statements

    def test_a_system_metric_from_the_trader_is_still_material(self):
        """The narrowing must not weaken the case FIX #7 exists for."""
        from tradingagents.dataflows.fact_check_integration import (
            collect_claims,
        )

        collected = collect_claims(
            {"trader_investment_plan": "Entry at P/E 18.2x, a discount."},
            _evidence(revenue=True, pe=False))
        assert collected.collected
        assert any(c.material for c in collected.collected), (
            "a P/E claim in the trader plan stopped being material")

    def test_a_foreign_figure_does_not_block_the_gate(self):
        from tradingagents.dataflows.gate_integration import evaluate_decision
        from tradingagents.dataflows.math.price_source import qualified_price
        from tradingagents.dataflows.math.run_registry import (
            RunInputs,
            build_run_registry,
        )

        registry = build_run_registry(
            RunInputs(price=50.8, price_provenance=qualified_price(
                TICKER, 50.8, bar_finality="INTRADAY_PARTIAL",
                session_date=TRADE_DATE, observation_timestamp=STAMP),
                revenue_consolidated=105_647_000_000.0,
                revenue_prior=94_972_000_000.0,
                revenue_provenance={"validation_status": "VERIFIED",
                                    "concept": "Revenues"}), run_id="fix7f")
        state = {
            "company_of_interest": TICKER, "trade_date": TRADE_DATE,
            "verified_market_snapshot": {"close": 50.8},
            "market_report": "", "news_report": "", "sentiment_report": "",
            "fundamentals_report": "",
            "investment_plan": "",
            "trader_investment_plan":
                "Buy stop above 49.00, stop 46.00, ATR 1.20. "
                "Peers trade at 14.2x.",
            "final_trade_decision": "**Rating**: Overweight\nBuy.",
            "final_rating": "Overweight",
        }
        _, decision = evaluate_decision(
            TICKER, TRADE_DATE, state, snapshot_id="fix7f",
            metric_registry=registry, market_open=True, equity=100_000.0,
            cash=50_000.0, risk_budget=1000.0, max_symbol_exposure=0.05)
        assert "FACT_CHECK_BLOCKED" not in [r.value for r in decision.reasons]

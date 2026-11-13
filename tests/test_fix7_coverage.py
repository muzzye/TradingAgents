"""FIX #7 — every decision source reaches the Fact Checker.

The Fix #6 wiring checked three sources:

    reports_to_check = {"market", "fundamentals", "news"}

`AgentState` carries seven decision-bearing fields. The four excluded ones are
exactly where a manager can introduce or repeat a false figure after the
analysts have finished — which is the bypass these tests establish.

Reproduced on `80f58b0`:

    final_trade_decision: "Total revenue was 74,322 million."   (certified: 105,647)
      ->  claims_total=0   action=WAIT   FACT_CHECK_BLOCKED not raised

Every figure is a frozen constant; no network, no broker.
"""

from __future__ import annotations

TICKER = "TEST"
TRADE_DATE = "2026-11-10"
STAMP = f"{TRADE_DATE}T18:00:00+00:00"
CERTIFIED_REVENUE = 105_647_000_000.0
FALSE_REVENUE = 74_322_000_000.0


def _registry():
    from tradingagents.dataflows.math.price_source import qualified_price
    from tradingagents.dataflows.math.run_registry import (
        RunInputs,
        build_run_registry,
    )

    return build_run_registry(
        RunInputs(price=50.8, price_provenance=qualified_price(
            TICKER, 50.8, bar_finality="INTRADAY_PARTIAL",
            session_date=TRADE_DATE, observation_timestamp=STAMP),
            revenue_consolidated=CERTIFIED_REVENUE,
            revenue_prior=94_972_000_000.0,
            revenue_provenance={"validation_status": "VERIFIED",
                                "concept": "Revenues",
                                "period_end": "2026-09-30"}),
        run_id="fix7")


def _state(**fields):
    base = {
        "company_of_interest": TICKER, "trade_date": TRADE_DATE,
        "verified_market_snapshot": {"close": 50.8},
        "market_report": "", "news_report": "", "sentiment_report": "",
        "fundamentals_report": "", "investment_plan": "",
        "trader_investment_plan": "Buy stop above 49.00, stop 46.00, ATR 1.20.",
        "final_trade_decision": "**Rating**: Overweight\nBuy.",
        "final_rating": "Overweight",
    }
    base.update(fields)
    return base


def _decide(**fields):
    from tradingagents.dataflows.gate_integration import evaluate_decision

    return evaluate_decision(
        TICKER, TRADE_DATE, _state(**fields), snapshot_id="fix7",
        metric_registry=_registry(), market_open=True, equity=100_000.0,
        cash=50_000.0, risk_budget=1000.0, max_symbol_exposure=0.05)


def _codes(decision):
    return [r.value for r in decision.reasons]


def _audit(record):
    return record.to_dict().get("fact_check") or {}


# ===========================================================================
# A — every decision source is checked
# ===========================================================================
class TestEveryAgentIsChecked:
    """A04/A06/A07/A08/A09/A10: the four uncovered sources."""

    def test_a08_a_false_claim_only_in_the_trader_plan_is_refuted(self):
        record, decision = _decide(
            trader_investment_plan=(
                f"Buy stop above 49.00, stop 46.00, ATR 1.20. "
                f"Total revenue was {FALSE_REVENUE/1e6:,.0f} million."))
        assert _audit(record)["claims_total"] >= 1, (
            "a false figure introduced in the trader plan was never collected")
        assert "FACT_CHECK_BLOCKED" in _codes(decision)
        assert decision.creates_order is False

    def test_a09_a_false_claim_only_in_the_final_decision_is_refuted(self):
        """The reproduced bypass: the Portfolio Manager's own sentence."""
        record, decision = _decide(
            final_trade_decision=(
                f"**Rating**: Overweight\nTotal revenue was "
                f"{FALSE_REVENUE/1e6:,.0f} million."))
        assert _audit(record)["claims_total"] >= 1
        assert "FACT_CHECK_BLOCKED" in _codes(decision)
        assert decision.creates_order is False

    def test_a07_a_false_claim_in_the_investment_plan_is_refuted(self):
        record, decision = _decide(
            investment_plan=(
                f"Total revenue was {FALSE_REVENUE/1e6:,.0f} million."))
        assert _audit(record)["claims_total"] >= 1
        assert "FACT_CHECK_BLOCKED" in _codes(decision)

    def test_a06_a_false_claim_in_the_sentiment_report_is_refuted(self):
        record, decision = _decide(
            sentiment_report=(
                f"Total revenue was {FALSE_REVENUE/1e6:,.0f} million."))
        assert _audit(record)["claims_total"] >= 1
        assert "FACT_CHECK_BLOCKED" in _codes(decision)

    def test_a10_the_same_false_figure_in_every_source_does_not_certify(self):
        wrong = f"Total revenue was {FALSE_REVENUE/1e6:,.0f} million."
        record, decision = _decide(
            market_report=wrong, news_report=wrong, sentiment_report=wrong,
            fundamentals_report=wrong, investment_plan=wrong)
        audit = _audit(record)
        assert audit["claims_total"] >= 5
        assert audit["refuted_claim_ids"], (
            "five sources repeating a false figure certified it")
        assert "FACT_CHECK_BLOCKED" in _codes(decision)

    def test_a11_analysts_correct_and_manager_wrong(self):
        right = f"Total revenue was {CERTIFIED_REVENUE/1e6:,.0f} million."
        wrong = f"Total revenue was {FALSE_REVENUE/1e6:,.0f} million."
        record, decision = _decide(
            fundamentals_report=right, final_trade_decision=wrong)
        audit = _audit(record)
        statuses = sorted(v["status"] for v in audit["verdicts"])
        assert statuses == ["REFUTED", "SUPPORTED"], statuses
        assert "FACT_CHECK_BLOCKED" in _codes(decision)

    def test_a12_the_manager_cannot_ignore_an_earlier_refutation(self):
        """A refuted claim repeated downstream stays refuted."""
        wrong = f"Total revenue was {FALSE_REVENUE/1e6:,.0f} million."
        record, decision = _decide(
            fundamentals_report=wrong, final_trade_decision=wrong)
        audit = _audit(record)
        assert len(audit["refuted_claim_ids"]) >= 2, (
            "the manager's repetition was not refuted on its own terms")
        assert decision.creates_order is False


class TestTheCoverageIsRecorded:
    """F01/F07/F08: the audit shows which sources were read."""

    def test_every_covered_source_appears_in_the_audit(self):
        wrong = f"Total revenue was {FALSE_REVENUE/1e6:,.0f} million."
        record, _ = _decide(
            market_report=wrong, news_report=wrong, sentiment_report=wrong,
            fundamentals_report=wrong, investment_plan=wrong,
            trader_investment_plan=wrong, final_trade_decision=wrong)
        audit = _audit(record)
        assert audit["sources_checked"], (
            "the audit does not say which sources were read")
        assert len(audit["sources_checked"]) == 7, audit["sources_checked"]

    def test_an_unchecked_source_is_distinguishable(self):
        """F08: partial coverage must not look like full coverage."""
        record, _ = _decide()
        audit = _audit(record)
        assert "sources_checked" in audit
        assert "sources_unchecked" in audit

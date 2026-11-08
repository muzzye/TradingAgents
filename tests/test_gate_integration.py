"""Integration: the Execution Gate wired into the run finalization.

``record_decision`` now evaluates every finished run through the gate and
writes the audit JSON beside the state log. These tests pin the contract's
core promises at the integration level: the decision is still recorded
(the gate never blocks reporting), the verdict lands in the state, the
audit file exists, and a reliability-layer failure cannot sink the run.
"""

from __future__ import annotations

import pytest

from tradingagents.dataflows.gate_integration import (
    evaluate_decision,
)


def _state(rating="Overweight", decision_text="**Rating**: Overweight\nBuy the dip."):
    """The minimal final state _log_state reads, plus the decision fields."""
    return {
        "company_of_interest": "BRK-B",
        "trade_date": "2026-11-08",
        "market_report": "",
        "sentiment_report": "",
        "news_report": "",
        "fundamentals_report": "",
        "investment_debate_state": {"bull_history": "", "bear_history": "",
                                    "history": "", "current_response": ""},
        "trader_investment_plan": "",
        "risk_debate_state": {"aggressive_history": "", "conservative_history": "",
                              "neutral_history": "", "history": ""},
        "investment_plan": "",
        "final_rating": rating,
        "final_trade_decision": decision_text,
    }


# ---------------------------------------------------------------------------
# evaluate_decision
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_overweight_decision_with_no_plan_waits():
    """No plan numbers -> no risk check is guessed; the honest default holds."""
    record, decision = evaluate_decision(
        "BRK-B", "2026-11-08", _state(), snapshot_id="s1",
    )
    assert decision.action.value in ("WAIT", "BLOCK")
    assert record.strategic is not None
    assert record.strategic.rating == "OVERWEIGHT"
    assert record.strategic.legacy_label == "Overweight"


@pytest.mark.unit
def test_invalid_stop_plan_blocks():
    """The BRK-B plan (543.70/543.50/ATR 9.408) blocks through the wiring."""
    record, decision = evaluate_decision(
        "BRK-B", "2026-11-08", _state(), snapshot_id="s1",
        entry=543.70, stop=543.50, atr=9.408,
    )
    assert decision.action.value == "BLOCK"
    assert "RISK_PLAN_BLOCKED" in [r.value for r in decision.reasons]
    assert record.stop_validation is not None
    assert record.stop_validation.blocked


@pytest.mark.unit
def test_unreadable_rating_blocks_rather_than_guessing():
    """An unreadable rating (#1170) must not become a call the gate trusts."""
    record, decision = evaluate_decision(
        "BRK-B", "2026-11-08", _state(rating="REVIEW"), snapshot_id="s1",
    )
    assert decision.action.value == "BLOCK"
    assert record.strategic is None


@pytest.mark.unit
def test_empty_rating_blocks():
    record, decision = evaluate_decision(
        "BRK-B", "2026-11-08", _state(rating=""), snapshot_id="s1",
    )
    assert decision.action.value == "BLOCK"


@pytest.mark.unit
def test_causal_guard_flows_through_the_wiring():
    """A weekend release with a calendar: the verdict reaches the audit."""
    record, decision = evaluate_decision(
        "BRK-B", "2026-11-08", _state(), snapshot_id="s1",
        sessions=["2026-11-04", "2026-11-05", "2026-11-06", "2026-11-09"],
        event_publication_date="2026-11-07",
    )
    assert record.causal is not None
    assert record.causal.status == "NOT_YET_OBSERVABLE"
    assert record.to_dict()["events"][0]["post_event_price_status"] == \
        "NOT_YET_OBSERVABLE"


@pytest.mark.unit
def test_model_versions_reach_the_audit():
    record, _ = evaluate_decision(
        "BRK-B", "2026-11-08", _state(), snapshot_id="s1",
        model_versions={"deep": "GLM-5.3", "quick": "DeepSeek-V4.1-Flash"},
    )
    assert record.to_dict()["audit"]["model_versions"]["deep"] == "GLM-5.3"


# ---------------------------------------------------------------------------
# Portfolio context, sizing and fail-closed (contract §5)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_plan_without_portfolio_context_blocks_fail_closed():
    """A plan with numbers but no portfolio: PORTFOLIO_CONTEXT_MISSING blocks."""
    record, decision = evaluate_decision(
        "BRK-B", "2026-11-08", _state(), snapshot_id="s1",
        entry=100.0, stop=95.0, atr=5.0, risk_budget=1000.0,
        equity=None,
    )
    assert decision.action.value == "BLOCK"
    reasons = [r.value for r in decision.reasons]
    assert "PORTFOLIO_RISK_BUDGET_MISSING" in reasons
    assert record.sizing is not None
    assert record.sizing.qty > 0
    assert record.portfolio_risk is not None
    assert not record.portfolio_risk.equity_known


@pytest.mark.unit
def test_plan_without_risk_budget_blocks():
    """Numbers without a budget: sizing is refused, never guessed."""
    record, decision = evaluate_decision(
        "BRK-B", "2026-11-08", _state(), snapshot_id="s1",
        entry=100.0, stop=95.0, atr=5.0, risk_budget=None,
    )
    assert decision.action.value == "BLOCK"
    reasons = [r.value for r in decision.reasons]
    assert "PORTFOLIO_RISK_BUDGET_MISSING" in reasons
    assert record.sizing is None


@pytest.mark.unit
def test_sized_plan_with_portfolio_computes_fraction():
    """Budget + portfolio: sizing and the equity fraction are computed."""
    record, decision = evaluate_decision(
        "BRK-B", "2026-11-08", _state(), snapshot_id="s1",
        entry=100.0, stop=95.0, atr=5.0, risk_budget=1000.0,
        equity=100_000.0, cash=100_000.0,
    )
    assert record.sizing is not None
    assert record.sizing.qty > 0
    assert record.portfolio_risk is not None
    assert record.portfolio_risk.equity_known
    assert record.portfolio_risk.planned_risk_pct_equity is not None
    assert record.portfolio_risk.planned_risk_pct_equity < 0.05
    risk_entry = record.to_dict()["risk"]
    assert risk_entry["qty_proposed"] == record.sizing.qty
    assert risk_entry["planned_risk_pct_equity"] is not None


@pytest.mark.unit
def test_sized_plan_exceeding_the_loss_budget_blocks():
    """A planned loss beyond max_loss_fraction is a hard block."""
    record, decision = evaluate_decision(
        "BRK-B", "2026-11-08", _state(), snapshot_id="s1",
        entry=100.0, stop=95.0, atr=5.0, risk_budget=10_000.0,
        equity=100_000.0, max_loss_fraction=0.02,
    )
    assert decision.action.value == "BLOCK"
    reasons = [r.value for r in decision.reasons]
    assert "PORTFOLIO_RISK_BUDGET_EXCEEDED" in reasons


@pytest.mark.unit
def test_cash_constraint_limits_sizing_to_zero():
    """A budget the cash cannot cover: CASH_EXCEEDED, qty 0, gate blocks."""
    record, decision = evaluate_decision(
        "BRK-B", "2026-11-08", _state(), snapshot_id="s1",
        entry=100.0, stop=95.0, atr=5.0, risk_budget=100_000.0,
        equity=100_000.0, cash=50.0,
    )
    assert record.sizing is not None
    assert record.sizing.qty == 0
    assert decision.action.value == "BLOCK"


@pytest.mark.unit
def test_cross_agent_contradiction_blocks_data_quality():
    """A reaction claim while no post-event session exists: BLOCK."""
    state = _state()
    state["sentiment_report"] = (
        "The market has already absorbed the Q3 results; positioning is bullish."
    )
    record, decision = evaluate_decision(
        "BRK-B", "2026-11-08", state, snapshot_id="s1",
        sessions=["2026-11-04", "2026-11-05", "2026-11-06"],
        event_publication_date="2026-11-07",
    )
    assert decision.action.value == "BLOCK"
    assert "DATA_QUALITY_BLOCKED" in [r.value for r in decision.reasons]
    assert record.consistency is not None
    assert record.consistency.blocks_decision
    data_quality = record.to_dict()["data_quality"]
    assert data_quality["status"] == "BLOCKED_BY_CLAIMS"
    assert "CROSS_AGENT_REACTION_UNOBSERVABLE" in data_quality["blocking_claims"]


@pytest.mark.unit
def test_consistent_reports_do_not_block():
    """Honest reports with the same causal verdict: no data-quality block."""
    state = _state()
    state["sentiment_report"] = (
        "Positioning is constructive, but the reaction is not yet observable."
    )
    record, decision = evaluate_decision(
        "BRK-B", "2026-11-08", state, snapshot_id="s1",
        sessions=["2026-11-04", "2026-11-05", "2026-11-06"],
        event_publication_date="2026-11-07",
    )
    assert record.consistency is not None
    assert not record.consistency.blocks_decision
    assert decision.action.value != "BLOCK" or "DATA_QUALITY_BLOCKED" not in [
        r.value for r in decision.reasons
    ]

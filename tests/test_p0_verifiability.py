"""P0 verifiability: a run's audit must prove the gates actually ran.

The report of 2026-11-08 22:56 used Q2 equity, carried arithmetic errors and
showed no Execution Gate outcome. The cause was not a logic bug: the gates
exist, but the long-running web service had been started *before* the P0
code was merged, so it kept the old modules in memory and never executed
them.

That failure mode is invisible in the report text. This module makes it
verifiable: a fresh audit must carry the P0 verdict fields, and an audit
that predates them is diagnosed explicitly rather than read as "the gate
found nothing wrong".
"""

from __future__ import annotations

import json

import pytest

# The audit fields a P0-aware run writes. Their absence means the gates did
# not run — not that everything passed.
P0_AUDIT_MARKERS = ("fundamentals_freshness", "execution_verdict")
P0_EXECUTION_MARKERS = ("verdict",)


def audit_is_p0_aware(audit: dict) -> tuple[bool, list[str]]:
    """Whether an audit record proves the P0 gates executed.

    Returns ``(is_aware, missing)``. A pre-P0 audit has
    ``data_quality.status: null`` and no ``execution.verdict``: it is a
    record of a run whose gates never ran, and must never be presented as a
    passed validation.
    """
    missing: list[str] = []
    data_quality = audit.get("data_quality") or {}
    if data_quality.get("fundamentals_freshness") is None:
        missing.append("data_quality.fundamentals_freshness")
    execution = audit.get("execution") or {}
    if not any(k in execution for k in P0_EXECUTION_MARKERS):
        missing.append("execution.verdict")
    return (not missing), missing


def diagnose_audit(audit: dict) -> str:
    """A one-line diagnosis for a run's audit, for the verification report."""
    aware, missing = audit_is_p0_aware(audit)
    if aware:
        verdict = audit["execution"]["verdict"]
        return (
            f"P0 gates ran: action={verdict.get('execution_action')}, "
            f"approved={verdict.get('execution_approved')}, "
            f"blocking={verdict.get('blocking_reasons')}"
        )
    return (
        "P0 gates did NOT run in this record (missing: "
        f"{', '.join(missing)}). This is a pre-P0 run or a service started "
        "before the P0 code was loaded — the report cannot be read as a "
        "passed validation."
    )


class TestAuditProvesTheGatesRan:
    def test_a_pre_p0_audit_is_diagnosed_not_read_as_passing(self):
        # The actual shape of the 2026-11-08 22:56 record.
        legacy = {
            "execution": {"action": "WAIT", "reason_codes": ["ENTRY_TRIGGER_NOT_MET"]},
            "data_quality": {"status": None, "blocking_claims": []},
        }
        aware, missing = audit_is_p0_aware(legacy)
        assert aware is False
        assert "data_quality.fundamentals_freshness" in missing
        assert "execution.verdict" in missing
        assert "did NOT run" in diagnose_audit(legacy)

    def test_a_p0_audit_is_recognised(self):
        p0 = {
            "execution": {
                "action": "BLOCK",
                "verdict": {
                    "execution_action": "BLOCK",
                    "execution_approved": False,
                    "blocking_reasons": ["FUNDAMENTALS_STALE"],
                },
            },
            "data_quality": {
                "status": "BLOCKED_BY_CLAIMS",
                "fundamentals_freshness": {"status": "STALE_FUNDAMENTALS"},
            },
        }
        aware, missing = audit_is_p0_aware(p0)
        assert aware is True and missing == []
        assert "BLOCK" in diagnose_audit(p0)

    def test_real_audit_from_the_2026_11_08_run_is_flagged(self, tmp_path):
        """The persisted record of the 22:56 run must be flagged, not trusted.

        The fixture reproduces the record's shape; reading the real file is
        left to the audit script so the test stays hermetic.
        """
        path = tmp_path / "audit_2026-11-08.json"
        path.write_text(json.dumps({
            "schema_version": "1.0",
            "ticker": "BRK-B",
            "execution": {"action": "WAIT", "market_order_allowed": False,
                          "reason_codes": ["ENTRY_TRIGGER_NOT_MET"]},
            "data_quality": {"status": None, "blocking_claims": []},
        }), encoding="utf-8")
        audit = json.loads(path.read_text(encoding="utf-8"))
        aware, _missing = audit_is_p0_aware(audit)
        assert aware is False


class TestGatesBlockOnTheBranch:
    """The controls exist and block — proven on the code that carries them.

    On the verification branch these assertions are skipped when the P0
    modules are absent (``custom`` without PR #16); they run where the code
    is present, so the report can state which branch proves what.
    """

    pytestmark = pytest.mark.skipif(
        not __import__("importlib.util", fromlist=["util"]).find_spec(
            "tradingagents.dataflows.execution_verdict"),
        reason="P0 hardening modules (PR #16) are not on this branch",
    )

    def _state(self):
        return {
            "company_of_interest": "BRK-B",
            "trade_date": "2026-11-08",
            "market_report": "",
            "sentiment_report": "",
            "news_report": "",
            "fundamentals_report": "Equity: 747910 USD million as of 2026-Q2.",
            "investment_debate_state": {"bull_history": "", "bear_history": "",
                                        "history": "", "current_response": ""},
            "trader_investment_plan": "Entry 556.00, stop 542.50, ATR 9.408.",
            "risk_debate_state": {"aggressive_history": "", "conservative_history": "",
                                  "neutral_history": "", "history": ""},
            "investment_plan": "",
            "final_rating": "Overweight",
            "final_trade_decision": "**Rating**: Overweight",
        }

    def test_stale_fundamentals_and_arithmetic_block_with_zero_orders(self):
        from tradingagents.dataflows.execution_gate import ExecutionGate
        from tradingagents.dataflows.filing_freshness import FilingRecord
        from tradingagents.dataflows.gate_integration import evaluate_decision

        sent = []

        class Broker:
            def send(self, order):
                sent.append(order)

        q2 = FilingRecord("2026-Q2", "2026-06-30", published_date="2026-08-02")
        q3 = FilingRecord("2026-Q3", "2026-09-30", published_date="2026-11-07")

        record, decision = evaluate_decision(
            "BRK-B", "2026-11-08", self._state(), snapshot_id="verify-1",
            fundamentals_filings=[q2, q3],
            used_fundamentals_period_end="2026-06-30",
            equity=100_000.0, risk_budget=1_000.0, max_symbol_exposure=0.05,
            trigger_met=True, market_open=True,
            price_snapshot={"close": 570.89, "price_as_of": "2026-11-06",
                            "indicator_as_of": "2026-11-06",
                            "indicator": "52w_high"},
            stated_percentages={
                "distance_from_52w_high": {"stated_pct": -11.3,
                                           "reference": 586.30},
            },
        )

        assert decision.action.value == "BLOCK"
        verdict = record.execution_verdict.to_dict()
        assert verdict["execution_approved"] is False
        assert verdict["authorized_orders"] == []

        gate = ExecutionGate(analysis_as_of="2026-11-08")
        order, _ = gate.submit(
            type("P", (), {"ticker": "BRK-B", "rating": "OVERWEIGHT",
                           "intent": "ACCUMULATE_CONDITIONALLY",
                           "analysis_as_of": "2026-11-08",
                           "signal_as_of": "2026-11-08"})(),
            decision, quantity=8,
        )
        if order is not None:
            Broker().send(order)
        assert sent == []

        codes = record.to_dict()["data_quality"]["blocking_claims"]
        assert "FUNDAMENTALS_STALE" in codes
        assert "ARITHMETIC_MISMATCH" in codes

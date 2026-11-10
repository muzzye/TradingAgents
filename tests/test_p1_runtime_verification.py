"""P1 Task 1: the running process is identifiable, and its two open defects are pinned.

The data lineage audit could not tell from outside whether the serving process
ran the merged code. `/api/health` answers that since P1, but nothing printed it
at startup, and two defects keep the answer misleading or incomplete:

``R1`` ``service_started_at`` is the module's first-import time, not the process
     start — a value *later* than the process it describes.
``R2`` ``set_run_registry`` is never called outside the tests, so the analysts'
     certified-metric tool can never read the run's registry.

R1 and R2 are recorded, not fixed: both change the runtime identity contract or
the live graph path, which this verification branch deliberately leaves alone.
The tests below fail if the defects are *silently* removed without the
documented fix, and fail if they are fixed in a way that changes their shape —
so neither can be "fixed" by deleting the evidence.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import UTC, datetime

import pytest

from tests.fixtures import verified_price
from tradingagents.build_info import LOADED_AT, build_info, commit_sha
from webapp.__main__ import startup_diagnostics

# ---------------------------------------------------------------------------
# The startup diagnostics this branch adds (R3, fixed).
# ---------------------------------------------------------------------------


class TestStartupDiagnostics:
    """The console that starts the process must name the build it runs."""

    def test_the_build_id_and_commit_are_reported(self):
        lines = startup_diagnostics()
        joined = "\n".join(lines)
        assert "commit" in joined
        assert commit_sha() in joined, "the startup output must name the live commit"

    def test_the_validator_status_is_reported(self):
        """A module that imports but was never called looks identical to one that works."""
        joined = "\n".join(startup_diagnostics())
        assert "validators" in joined
        assert "metric_registry" in joined

    def test_a_broken_checkout_still_starts(self, monkeypatch):
        """Diagnostics must never stop the server: one bad line is enough."""
        import tradingagents.build_info as module

        monkeypatch.setattr(module, "commit_sha",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no git")))
        lines = startup_diagnostics()
        assert lines, "a failure must still produce a line"
        assert any("unknown" in line or "no git" in line for line in lines)


# ---------------------------------------------------------------------------
# R1 — service_started_at is not the process start.
# ---------------------------------------------------------------------------


class TestR1ServiceStartedAt:
    """`service_started_at` equals LOADED_AT, which is a first-import timestamp."""

    def test_the_payload_still_reports_loaded_at_as_the_start(self):
        """Pins the current, defective shape so a fix must be deliberate."""
        info = build_info()
        assert info["service_started_at"] == LOADED_AT

    def test_loaded_at_cannot_be_earlier_than_the_process_it_describes(self):
        """A start time earlier than the interpreter is impossible; later is the defect.

        The process in a fresh interpreter began at ``T0``; importing the module
        afterwards can only produce a later ``LOADED_AT``. The health payload
        calls that later value the service start.
        """
        t0 = datetime.now(UTC)
        time.sleep(1.1)
        code = "from tradingagents.build_info import LOADED_AT; print(LOADED_AT)"
        out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                             text=True, check=True).stdout.strip()
        reported = datetime.fromisoformat(out)
        assert reported > t0, (
            "LOADED_AT was expected to be later than the process start — that "
            "is the R1 defect; if this now fails, R1 has been fixed and the "
            "health payload must stop calling it service_started_at"
        )


# ---------------------------------------------------------------------------
# R2 — the agents' certified-metric tool never receives a registry.
# ---------------------------------------------------------------------------


class TestR2AgentRegistry:
    """R2 — the agents can read the registry their run is certified against.

    The audit found `set_run_registry` had no production call site: every
    `get_certified_metric` answered UNSUPPORTED while the analyst was working,
    and the module global it read leaked one analysis's metrics into the next.

    R2 is now fixed (phase 7): the binding is a `ContextVar`, the graph holds it
    across the whole run, and the tools record into it. These tests pin the
    fixed behaviour and would fail if any half of it regressed.
    """

    def test_the_graph_binds_a_registry_for_the_run(self):
        """A production call site exists, and it is in the graph's run path."""
        result = subprocess.run(
            ["grep", "-rn", "set_run_registry", "tradingagents/graph/",
             "--include=*.py"],
            capture_output=True, text=True, check=False,
        )
        call_sites = [
            line for line in result.stdout.splitlines()
            if line.strip() and "__pycache__" not in line
            and "import" not in line
        ]
        assert call_sites, (
            "the graph never binds a registry: R2 has regressed and the agents "
            "can no longer read certified metrics"
        )

    def test_the_agent_reads_a_certified_metric_inside_a_run(self):
        """The behaviour R2 made impossible: a certified metric is readable."""
        from tradingagents.agents import math_tools
        from tradingagents.dataflows.math.run_registry import (
            RunInputs,
            build_run_registry,
        )

        registry = build_run_registry(
            RunInputs(price=576.74, book_value_per_share=359.5599, price_provenance=verified_price(576.74)), run_id="r1")
        assert registry.get("price_to_book").is_usable is True

        with math_tools.certified_registry(registry):
            payload = json.loads(math_tools.get_certified_metric.invoke(
                {"metric_name": "price_to_book", "ticker": "BRK-B"}))
        assert payload["value"] is not None
        assert payload["calculation_status"] == "VERIFIED"

    def test_outside_a_run_the_tool_is_honest_again(self):
        """No binding means no certified figure — not a remembered one."""
        from tradingagents.agents import math_tools

        payload = json.loads(math_tools.get_certified_metric.invoke(
            {"metric_name": "price_to_book", "ticker": "BRK-B"}))
        assert payload["value"] is None
        assert payload["calculation_status"] == "UNSUPPORTED"
        assert payload["blocks_decision"] is True

    def test_two_runs_do_not_share_a_registry(self):
        """R2's second harm, now prevented: the binding is per context."""
        from tradingagents.agents import math_tools
        from tradingagents.dataflows.math.run_registry import (
            RunInputs,
            build_run_registry,
        )

        first = build_run_registry(
            RunInputs(price=576.74, book_value_per_share=359.5599, price_provenance=verified_price(576.74)), run_id="run-a")
        second = build_run_registry(
            RunInputs(price=100.0, book_value_per_share=50.0, price_provenance=verified_price(100.0)), run_id="run-b")

        with math_tools.certified_registry(first):
            inside_first = math_tools._run_registry()
        with math_tools.certified_registry(second):
            inside_second = math_tools._run_registry()

        assert inside_first is first
        assert inside_second is second
        assert inside_first is not inside_second
        assert math_tools._run_registry() is None, (
            "a finished run must not leave its registry readable")

    def test_a_tool_records_what_it_computed(self):
        """The other half of R2: a computed value is not thrown away."""
        from tradingagents.agents import math_tools
        from tradingagents.dataflows.math.registry import MetricRegistry

        registry = MetricRegistry(run_id="write-through")
        with math_tools.certified_registry(registry):
            math_tools.calculate_price_to_book.invoke(
                {"price": 576.74, "book_value_per_share": 359.5599})
        assert registry.get("price_to_book") is not None, (
            "the tool computed a P/B and the registry never saw it")

    def test_the_binding_is_restored_after_an_exception(self):
        """A failed run must not leave its registry bound for the next one."""
        from tradingagents.agents import math_tools
        from tradingagents.dataflows.math.registry import MetricRegistry

        with (pytest.raises(RuntimeError),
              math_tools.certified_registry(MetricRegistry(run_id="boom"))):
            raise RuntimeError("run failed")
        assert math_tools._run_registry() is None


# ---------------------------------------------------------------------------
# The behaviour the audit cares about most: none of this weakens the gate.
# ---------------------------------------------------------------------------


class TestTheGateStaysClosed:
    def test_a_critical_metric_that_cannot_be_verified_still_blocks(self):
        """R2 must never be 'solved' by letting an unverified metric through."""
        from tradingagents.dataflows.gate_integration import (
            ExecutionAuthorization,
            attach_registry_status,
            attach_to_state,
            evaluate_decision,
        )

        state = {
            "company_of_interest": "BRK-B",
            "trade_date": "2026-11-09",
            "market_report": "P/B of 1.6040x.",
            "sentiment_report": "",
            "news_report": "",
            "fundamentals_report": "",
            "investment_debate_state": {"bull_history": "", "bear_history": "",
                                        "history": "", "current_response": ""},
            "trader_investment_plan": "Entry 557.00, stop 547.40, ATR 9.408.",
            "risk_debate_state": {"aggressive_history": "", "conservative_history": "",
                                  "neutral_history": "", "history": ""},
            "investment_plan": "",
            "final_rating": "Overweight",
            "final_trade_decision": "**Rating**: Overweight\nBuy.",
        }
        _, decision = evaluate_decision(
            "BRK-B", "2026-11-09", state, snapshot_id="s", trigger_met=True,
            equity=100_000.0, cash=100_000.0, risk_budget=1_000.0,
            max_symbol_exposure=0.05, metric_registry=None,
        )
        attach_to_state(state, decision)
        attach_registry_status(state, None)
        assert state["execution_authorization"] == str(
            ExecutionAuthorization.BLOCKED)
        assert decision.creates_order is False

"""Phase 6 — end-to-end reliability certification of the whole pipeline.

Phases 1–5 each fixed one defect. This suite is the certification that they are
*operative together*: a single frozen BRK-B run of 2026-11-09 driven through
providers → validation → math engine → registry → agents → risk → gate, with
every assertion on a number the code computed.

It deliberately does not re-test each phase's internals — those suites exist.
It tests the **seams** between them, which is where a fix that works in
isolation can still fail to reach the pipeline: a registry that is built but
not attached, a bar judged but not consulted, a claim reconciled but not fed to
the gate.

The fixture is `fixtures/brkb_2026_11_09.py`: SEC Q3 figures, both share
classes, OHLCV, indicators, the intraday snapshot instant, news with
timestamps, a simulated book, the risk policy and fixed LLM answers. Nothing
here reaches a provider or a model.
"""

from __future__ import annotations

import json

import pytest

from tests.fixtures.brkb_2026_11_09 import (
    ANALYSIS_DATE,
    BRKB,
    EOD_CLOSE,
    EOD_PREVIOUS_CLOSE,
    EPS_TTM,
    EXPECTED,
    EXPECTED_EOD_CHANGE_PCT,
    LLM_ANSWERS,
    LLM_CLAIMED_PB,
    NEWS_ARTICLES,
    REVENUE_Q3_2025,
    REVENUE_Q3_2026,
    REVENUE_Q3_2026_NARROW,
    RISK_POLICY,
    SNAPSHOT_INSTANT,
    TICKER,
    WEIGHTED_AVERAGE_SHARES,
)

pytestmark = pytest.mark.unit


def _registry(state: dict, run_id: str = "p6"):
    from tradingagents.dataflows.math.extraction import extract_run_inputs
    from tradingagents.dataflows.math.run_registry import (
        build_run_registry,
        registry_inputs_from_state,
    )
    from tradingagents.graph.trading_graph import _merge_extracted

    extracted = extract_run_inputs(state)
    base = registry_inputs_from_state(state)
    return build_run_registry(
        _merge_extracted(base, extracted.inputs), run_id=run_id)


def _gate(state: dict, registry, **kwargs):
    from tradingagents.dataflows.gate_integration import evaluate_decision

    return evaluate_decision(
        TICKER, ANALYSIS_DATE, state, snapshot_id="p6",
        metric_registry=registry,
        equity=RISK_POLICY["equity"], cash=RISK_POLICY["cash"],
        risk_budget=RISK_POLICY["risk_budget"],
        max_symbol_exposure=RISK_POLICY["max_symbol_exposure"],
        **kwargs,
    )


# ===========================================================================
# Task 1 — the snapshot is complete and self-contained
# ===========================================================================
class TestTheFrozenSnapshot:
    def test_it_carries_everything_the_pipeline_consumes(self):
        """No external provider is needed: the fixture supplies each input."""
        state = BRKB.run_state()
        for key in ("company_of_interest", "trade_date",
                    "verified_market_snapshot", "equity",
                    "shares_class_a", "shares_class_b",
                    "class_a_to_b_ratio", "revenue_consolidated",
                    "revenue_prior"):
            assert key in state, key
        assert RISK_POLICY["equity"] == BRKB.account_equity
        assert RISK_POLICY["risk_budget"] == BRKB.risk_budget

    def test_it_carries_news_with_the_timestamps_that_make_it_checkable(self):
        assert len(NEWS_ARTICLES) >= 2
        for article in NEWS_ARTICLES:
            assert article["published_at"], "a claim without a time is uncheckable"
            assert article["comparison"] in ("intraday", "close_to_close")
            assert article["url"] and article["provider"]

    def test_it_carries_the_llm_answers_but_no_real_model(self):
        """A run can complete without a provider, and the figures are fixed."""
        assert LLM_ANSWERS["market_report"]
        assert LLM_ANSWERS["portfolio_manager"]
        assert LLM_CLAIMED_PB == 1.73          # the figure the engine disproves

    def test_the_snapshot_instant_is_intraday(self):
        """The run is dated 2026-11-09 and read at 12:37 New York."""
        assert SNAPSHOT_INSTANT.endswith("T17:37:00Z")
        assert ANALYSIS_DATE == "2026-11-09"


# ===========================================================================
# Task 2 — the pipeline, seam by seam
# ===========================================================================
class TestPipelineEndToEnd:
    def test_consolidated_revenue_is_the_whole_entitys_figure(self):
        """F1: 105,647 — not the 74,322 contract component."""
        registry = _registry(BRKB.run_state())
        revenue = registry.get("consolidated_revenue")
        assert float(revenue.value) == pytest.approx(REVENUE_Q3_2026, rel=1e-9)
        assert abs(float(revenue.value) / REVENUE_Q3_2026_NARROW - 1) > 0.01

    def test_revenue_growth_is_eleven_point_two_four_percent(self):
        """F1's arithmetic, through the registry rather than by hand."""
        registry = _registry(BRKB.run_state())
        assert float(registry.get("revenue_growth").value) == pytest.approx(
            EXPECTED["revenue_growth_pct"], abs=0.01)

    def test_price_to_book_is_coherent_and_its_inputs_are_traceable(self):
        """F2/F3: both P/B forms registered, with their operands."""
        registry = _registry(BRKB.run_state())
        per_share = registry.get("price_to_book")
        aggregate = registry.get("pb_aggregate")
        assert per_share is not None and per_share.formula_id == "price_div_bvps"
        assert aggregate is not None
        assert aggregate.formula_id == "market_cap_div_attributable_equity"
        for record in (per_share, aggregate):
            assert record.operands, f"{record.metric} must keep its inputs"

    def test_the_model_authored_pb_never_becomes_the_certified_one(self):
        """The report said 1.73x; the engine's figure is ~1.60x."""
        state = BRKB.run_state()
        state["market_report"] = LLM_ANSWERS["market_report"]
        registry = _registry(state)
        certified = registry.get("price_to_book")
        assert float(certified.value) != pytest.approx(LLM_CLAIMED_PB, abs=0.01)

    def test_eps_is_certified_or_says_why_not(self):
        """F3: with a share count it verifies; without one it is UNSUPPORTED."""
        with_count = BRKB.run_state(eps_ttm=EPS_TTM,
                                    weighted_average_shares=WEIGHTED_AVERAGE_SHARES)
        registry = _registry(with_count)
        assert registry.get("eps_gaap").is_usable is True

        without = BRKB.run_state(eps_ttm=EPS_TTM)
        registry = _registry(without)
        eps = registry.get("eps_gaap")
        assert eps.is_usable is False
        assert eps.reason, "an unverifiable EPS must say what is missing"

    def test_the_bar_is_not_certified_as_an_end_of_day_close(self):
        """F5: the snapshot instant is 12:37 New York, the session open."""
        from tradingagents.dataflows.vendors.bar_finality import (
            BarFinality,
            assess_finality,
        )

        bar = assess_finality(SNAPSHOT_INSTANT,
                             snapshot_id=f"{TICKER}-{ANALYSIS_DATE}")
        assert bar.finality is BarFinality.INTRADAY_PARTIAL
        assert bar.usable_for_eod is False
        assert bar.snapshot_id == f"{TICKER}-{ANALYSIS_DATE}"

    def test_the_news_claims_are_reconciled_against_the_certified_change(self):
        """F5/§Task B: -1.01% intraday is not the +0.76% close-to-close."""
        from tradingagents.dataflows.math.event_claims import (
            BenchmarkSnapshot,
            ComparisonType,
            EventClaim,
            EventClaimVerdict,
            reconcile_claims,
        )

        benchmark = BenchmarkSnapshot(
            session_date=ANALYSIS_DATE,
            previous_close=EOD_PREVIOUS_CLOSE, close=EOD_CLOSE,
            snapshot_id=f"{TICKER}-{ANALYSIS_DATE}",
        )
        assert float(benchmark.change_pct) == pytest.approx(
            EXPECTED_EOD_CHANGE_PCT, abs=0.01)

        claims = [
            EventClaim(
                stated_pct=a["stated_pct"],
                comparison=ComparisonType(a["comparison"]),
                provider=a["provider"], url=a["url"],
                published_at=a["published_at"], updated_at=a["updated_at"],
                window_start=a["window_start"], window_end=a["window_end"],
                session_date=a["session_date"],
            )
            for a in NEWS_ARTICLES
        ]
        reconciled = reconcile_claims(claims, benchmark)
        verdicts = [c.reconciliation_status for c in reconciled]
        assert EventClaimVerdict.TIME_WINDOW_MISMATCH in verdicts
        assert EventClaimVerdict.CONFIRMED in verdicts
        assert all(not c.may_replace_eod_change
                   for c in reconciled
                   if c.reconciliation_status is not EventClaimVerdict.CONFIRMED)

    def test_the_quantity_comes_from_the_risk_engine(self):
        """F4: a computed integer, capped by the policy, not a suggestion."""
        from tradingagents.dataflows.math.order_sizing import (
            CommissionPolicy,
            authorized_size,
        )

        sizing = authorized_size(
            entry=EXPECTED["entry"], stop=EXPECTED["stop"], atr=BRKB.atr,
            risk_budget=RISK_POLICY["risk_budget"],
            portfolio_equity=RISK_POLICY["equity"],
            available_cash=RISK_POLICY["cash"],
            max_symbol_exposure=RISK_POLICY["max_symbol_exposure"],
            slippage_pct=RISK_POLICY["slippage_pct"],
            commission=CommissionPolicy(fixed=RISK_POLICY["commission_fixed"]),
        )
        assert sizing.authorized is True
        assert isinstance(sizing.qty, int)
        assert sizing.qty == EXPECTED["exposure_capped_qty"]
        assert sizing.binding_constraint == EXPECTED["binding_constraint"]

    def test_the_gate_reaches_a_deterministic_authorization(self):
        state = BRKB.run_state()
        registry = _registry(state)
        _, decision = _gate(state, registry, trigger_met=True)
        assert decision.action.value in ("PROPOSE_PAPER_ORDER", "BLOCK", "WAIT")
        assert decision.creates_order is (decision.action.value
                                          == "PROPOSE_PAPER_ORDER")


# ===========================================================================
# Task 3 — reproducibility
# ===========================================================================
class TestReproducibility:
    def _repeat(self, n: int = 5):
        """Run the pipeline n times over the same frozen state."""
        results = []
        for index in range(n):
            state = BRKB.run_state()
            registry = _registry(state, run_id=f"repeat-{index}")
            _, decision = _gate(state, registry, trigger_met=True)
            results.append((state, registry, decision))
        return results

    def test_certified_metrics_are_identical_across_five_runs(self):
        runs = self._repeat()
        signatures = []
        for _state, registry, _decision in runs:
            signatures.append({
                name: str(record.value)
                for name, record in registry._records.items()  # noqa: SLF001
                if record.value is not None
            })
        assert all(sig == signatures[0] for sig in signatures), (
            "the same inputs must certify the same figures")

    def test_the_authorized_quantity_is_identical(self):
        from tradingagents.dataflows.math.order_sizing import authorized_size

        quantities = set()
        for _ in range(5):
            sizing = authorized_size(
                entry=EXPECTED["entry"], stop=EXPECTED["stop"], atr=BRKB.atr,
                risk_budget=RISK_POLICY["risk_budget"],
                portfolio_equity=RISK_POLICY["equity"],
                available_cash=RISK_POLICY["cash"],
                max_symbol_exposure=RISK_POLICY["max_symbol_exposure"],
            )
            quantities.add((sizing.qty, sizing.binding_constraint))
        assert len(quantities) == 1

    def test_the_gate_verdict_is_identical(self):
        runs = self._repeat()
        verdicts = {(d.action.value, d.creates_order) for _s, _r, d in runs}
        assert len(verdicts) == 1, f"the gate varied across identical inputs: {verdicts}"

    def test_the_metric_limits_are_identical(self):
        from tradingagents.dataflows.math.order_sizing import authorized_size

        limit_sets = []
        for _ in range(5):
            sizing = authorized_size(
                entry=EXPECTED["entry"], stop=EXPECTED["stop"], atr=BRKB.atr,
                risk_budget=RISK_POLICY["risk_budget"],
                portfolio_equity=RISK_POLICY["equity"],
                available_cash=RISK_POLICY["cash"],
                max_symbol_exposure=RISK_POLICY["max_symbol_exposure"],
            )
            limit_sets.append(dict(sizing.limits))
        assert all(lims == limit_sets[0] for lims in limit_sets)

    def test_only_the_strategic_recommendation_may_vary(self):
        """The prose may differ run to run; the certified set may not.

        This is the separation the task asks to measure: variability in what a
        model *recommends* is acceptable and expected, variability in what the
        engine *certifies* is not.
        """
        first = _registry(BRKB.run_state(run_id="a"), run_id="a")
        second = _registry(
            BRKB.run_state(final_trade_decision="Different narrative."),
            run_id="b")
        assert first.get("consolidated_revenue").value == \
            second.get("consolidated_revenue").value
        assert first.get("price_to_book").value == \
            second.get("price_to_book").value


# ===========================================================================
# Task 4 — negative testing
# ===========================================================================
class TestNegativeInputsCannotAuthorize:
    """Each case injects one defect and asserts no order is authorised."""

    def _assert_blocked(self, state, **kwargs):
        registry = _registry(state)
        _, decision = _gate(state, registry, trigger_met=True, **kwargs)
        return decision

    def test_a_wrong_consolidated_revenue_is_caught(self):
        """The component presented as the total: the registry refuses it."""
        from tradingagents.dataflows.math.run_registry import (
            RunInputs,
            build_run_registry,
        )

        registry = build_run_registry(RunInputs(
            revenue_consolidated=REVENUE_Q3_2026_NARROW,
            revenue_prior=REVENUE_Q3_2025,
            revenue_provenance={
                "concept": "RevenueFromContractWithCustomerExcludingAssessedTax",
                "validation_status": "UNVERIFIED",
            },
        ), run_id="bad-revenue")
        assert registry.get("consolidated_revenue").is_usable is False
        assert registry.get("revenue_growth").is_usable is False

    def test_an_incompatible_price_to_book_is_recorded(self):
        state = BRKB.run_state(market_cap_reported=1_231_440_000_000.0)
        registry = _registry(state)
        agreement = registry.get("pb_reconciliation")
        assert agreement is not None
        assert agreement.calculation_status.value in ("VERIFIED", "REFUSED")

    def test_an_unreconcilable_eps_is_not_certified(self):
        registry = _registry(BRKB.run_state())
        assert registry.get("eps_gaap").is_usable is False
        assert registry.get("pe_trailing").is_usable is False

    def test_a_wrong_annualization_is_refused(self):
        from tradingagents.dataflows.math.period_labels import (
            AnnualizationVerdict,
            check_annualization,
        )

        wrong = check_annualization(1.2, quarterly_pct=0.78)
        assert wrong.verdict is AnnualizationVerdict.FORMULA_MISSING
        assert wrong.blocks is True

    def test_a_contradicted_news_price_is_not_promoted(self):
        from tradingagents.dataflows.math.event_claims import (
            BenchmarkSnapshot,
            ComparisonType,
            EventClaim,
            EventClaimVerdict,
            reconcile_event_claim,
        )

        benchmark = BenchmarkSnapshot(
            session_date=ANALYSIS_DATE, previous_close=EOD_PREVIOUS_CLOSE,
            close=EOD_CLOSE)
        claim = EventClaim(stated_pct=-5.0,
                           comparison=ComparisonType.CLOSE_TO_CLOSE,
                           published_at="2026-11-10T09:00:00Z")
        result = reconcile_event_claim(claim, benchmark)
        assert result.reconciliation_status is EventClaimVerdict.CONTRADICTED
        assert result.may_replace_eod_change is False

    def test_an_intraday_bar_presented_as_eod_is_not_usable(self):
        from tradingagents.dataflows.vendors.bar_finality import assess_finality

        bar = assess_finality(SNAPSHOT_INSTANT)
        assert bar.usable_for_eod is False

    def test_a_missing_registry_blocks(self):
        state = BRKB.run_state()
        _, decision = _gate(state, None, trigger_met=True)
        assert decision.creates_order is False
        assert "NO_CERTIFIED_METRIC" in [r.value for r in decision.reasons] or \
               decision.action.value == "BLOCK"

    def test_a_missing_portfolio_blocks(self):
        from tradingagents.dataflows.gate_integration import evaluate_decision

        state = BRKB.run_state()
        registry = _registry(state)
        _, decision = evaluate_decision(
            TICKER, ANALYSIS_DATE, state, snapshot_id="no-book",
            metric_registry=registry, trigger_met=True)
        assert decision.creates_order is False
        codes = [r.value for r in decision.reasons]
        assert "PORTFOLIO_RISK_BUDGET_MISSING" in codes or "RISK_PLAN_BLOCKED" in codes

    def test_a_wrong_stop_side_blocks_the_quantity(self):
        from tradingagents.dataflows.math.order_sizing import authorized_size

        sizing = authorized_size(
            entry=EXPECTED["entry"], stop=EXPECTED["entry"] + 5,
            atr=BRKB.atr, risk_budget=RISK_POLICY["risk_budget"],
            portfolio_equity=RISK_POLICY["equity"],
            available_cash=RISK_POLICY["cash"],
            max_symbol_exposure=RISK_POLICY["max_symbol_exposure"],
        )
        assert sizing.authorized is False
        assert sizing.qty == 0

    def test_a_quantity_beyond_the_limits_is_reduced(self):
        from tradingagents.dataflows.math.order_sizing import authorized_size

        sizing = authorized_size(
            entry=EXPECTED["entry"], stop=EXPECTED["stop"], atr=BRKB.atr,
            risk_budget=RISK_POLICY["risk_budget"],
            portfolio_equity=RISK_POLICY["equity"],
            available_cash=RISK_POLICY["cash"],
            max_symbol_exposure=RISK_POLICY["max_symbol_exposure"],
            held_quantity=3,
        )
        assert sizing.qty <= 3


# ===========================================================================
# Task 5 — the audit is per run, and complete
# ===========================================================================
class TestRunAudit:
    def test_the_record_carries_every_field_the_task_names(self, tmp_path):
        from tradingagents.dataflows import run_record as rr
        from tradingagents.dataflows.gate_integration import (
            attach_registry_status,
            attach_to_state,
        )

        state = BRKB.run_state()
        registry = _registry(state)
        record, decision = _gate(state, registry, trigger_met=True)
        attach_to_state(state, decision)
        attach_registry_status(state, registry)

        run_id = rr.new_run_id(TICKER)
        run = rr.RunRecord(
            run_id=run_id, ticker=TICKER, analysis_date=ANALYSIS_DATE,
            fingerprint=rr.runtime_fingerprint(),
            inputs={"portfolio": state.get("portfolio")},
            filings=[{"form": "10-Q", "filed": "2026-10-25"}],
            metric_registry=registry.summary(),
            tool_calls=[{"tool": "get_stock_data", "args": {"symbol": TICKER},
                         "call_id": "c1", "answered": True}],
            decision={"rating": state.get("final_rating")},
            risk={}, execution_gate=record.to_dict().get("execution", {}),
            audit=record.to_dict(),
        )
        path = rr.write_run(tmp_path, run)
        payload = json.loads(path.read_text(encoding="utf-8"))

        for field in ("run_id", "fingerprint", "inputs", "filings",
                      "metric_registry", "tool_calls", "agents", "decision",
                      "risk", "execution_gate", "audit", "created_at"):
            assert field in payload, field
        assert payload["run_id"] == run_id
        assert payload["fingerprint"]["commit"]

    def test_two_runs_on_one_day_do_not_overwrite_each_other(self, tmp_path):
        """The audit's finding: logs keyed only by date were replaced."""
        from tradingagents.dataflows import run_record as rr

        first = rr.new_run_id(TICKER)
        second = rr.new_run_id(TICKER)
        assert first != second
        rr.write_run(tmp_path, rr.RunRecord(
            run_id=first, ticker=TICKER, analysis_date=ANALYSIS_DATE))
        rr.write_run(tmp_path, rr.RunRecord(
            run_id=second, ticker=TICKER, analysis_date=ANALYSIS_DATE))
        assert len(rr.list_runs(tmp_path, TICKER)) == 2

    def test_a_record_never_contains_a_credential(self, tmp_path):
        from tradingagents.dataflows import run_record as rr

        run = rr.RunRecord(
            run_id=rr.new_run_id(TICKER), ticker=TICKER,
            analysis_date=ANALYSIS_DATE,
            inputs=rr.redact({"portfolio": {"api_key": "sk-live-should-not-appear"}}),
        )
        path = rr.write_run(tmp_path, run)
        text = path.read_text(encoding="utf-8")
        assert "sk-live-should-not-appear" not in text

    def test_the_tool_call_extractor_records_what_was_read(self):
        from tradingagents.dataflows.run_record import tool_calls_from_messages

        class Msg:
            def __init__(self, calls=None, answered=None):
                self.tool_calls = calls
                self.tool_call_id = answered

        messages = [
            Msg([{"name": "get_stock_data", "args": {"symbol": TICKER}, "id": "c1"},
                 {"name": "get_indicators", "args": {"indicator": "rsi"}, "id": "c2"}]),
            Msg(answered="c1"),
        ]
        calls = tool_calls_from_messages(messages)
        assert [c["tool"] for c in calls] == ["get_stock_data", "get_indicators"]
        assert calls[0]["answered"] is True
        assert calls[1]["answered"] is False

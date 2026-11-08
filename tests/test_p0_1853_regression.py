"""P0 regression tests — the BRK-B report of 2026-11-08 18:53:34.

Each test reproduces one defect observed in the 18:53 report (task
``docs/tasks/TRADINGAGENTS_P0_REGRESSION_1853_COPILOT_TASK.md``) and pins
the deterministic check that must block it. The rules are ticker-agnostic:
the BRK-B numbers are the fixture, never a special case. All offline, no
network, no LLM.
"""

from __future__ import annotations

import pytest

from tradingagents.dataflows.regression_gates import (
    EPS_DEFINITION_MISMATCH,
    INVALID_ENTRY_CONDITION,
    INVALID_EVENT_CAUSALITY,
    INVALID_TREND_COMPARISON,
    NOT_YET_OBSERVABLE,
    OBSERVABLE,
    STALE_FUNDAMENTALS,
    UNRECONCILED_VALUATION,
    EntryCondition,
    MetricObservation,
    TranchePlan,
    aggregate_tranche_risk,
    check_event_causality,
    check_filing_freshness,
    check_metric_definition,
    check_trend_comparison,
    reconcile_valuation,
    size_with_exposure_cap,
    validate_entry_conditions,
)

# The 18:53 fixture timeline (same weekend as the 17:05 report).
SESSIONS = ["2026-11-04", "2026-11-05", "2026-11-06", "2026-11-09"]
EVENT_DATE = "2026-11-07"      # Saturday: Q3 published
AS_OF = "2026-11-08"           # Sunday: report generated


# ---------------------------------------------------------------------------
# EVENT-REG-001 — a pre-event move presented as the post-event reaction
# ---------------------------------------------------------------------------


class TestEventCausality:
    def test_the_reported_minus_001_reaction_is_invalid(self):
        """The 18:53 report called Friday's -0.01% move the Q3 reaction.

        Friday's close predates the Saturday release: the claim is
        temporally impossible and must be INVALID_EVENT_CAUSALITY.
        """
        verdict = check_event_causality(
            claimed_reaction_date="2026-11-06",
            event_published_at=EVENT_DATE,
            sessions=SESSIONS,
            as_of_date=AS_OF,
        )
        assert verdict.status == INVALID_EVENT_CAUSALITY
        assert verdict.blocks_decision
        assert verdict.reason_code == INVALID_EVENT_CAUSALITY

    def test_a_post_event_session_reaction_is_observable(self):
        """Once Monday has traded, a reaction claim on it is checkable."""
        verdict = check_event_causality(
            claimed_reaction_date="2026-11-09",
            event_published_at=EVENT_DATE,
            sessions=SESSIONS,
            as_of_date="2026-11-09",
        )
        assert verdict.status == OBSERVABLE
        assert not verdict.blocks_decision

    def test_no_post_event_session_yet_is_not_yet_observable(self):
        verdict = check_event_causality(
            claimed_reaction_date="2026-11-09",
            event_published_at=EVENT_DATE,
            sessions=SESSIONS,
            as_of_date=AS_OF,
        )
        assert verdict.status == NOT_YET_OBSERVABLE
        assert verdict.blocks_decision  # a reaction claim cannot stand yet

    def test_a_holiday_between_event_and_claim_stays_invalid(self):
        """Thanksgiving Thursday: the claim's date is a market holiday, so
        no session traded there — the reaction is still impossible."""
        verdict = check_event_causality(
            claimed_reaction_date="2026-11-26",   # Thanksgiving, market closed
            event_published_at="2026-11-25",
            sessions=["2026-11-24", "2026-11-25", "2026-11-27"],
            as_of_date="2026-11-27",
        )
        assert verdict.status == INVALID_EVENT_CAUSALITY

    def test_an_after_hours_publication_excludes_the_same_day(self):
        """Published 20:00 on a trading day: that session closed before the
        release, so a same-day reaction claim is impossible."""
        verdict = check_event_causality(
            claimed_reaction_date="2026-11-05",
            event_published_at="2026-11-05",
            sessions=SESSIONS,
            as_of_date="2026-11-08",
        )
        assert verdict.status == INVALID_EVENT_CAUSALITY

    def test_an_unknown_event_date_cannot_validate_a_reaction(self):
        verdict = check_event_causality(
            claimed_reaction_date="2026-11-06",
            event_published_at=None,
            sessions=SESSIONS,
            as_of_date=AS_OF,
        )
        assert verdict.blocks_decision


# ---------------------------------------------------------------------------
# FILING-REG-002 — Q2 presented as current while Q3 was published
# ---------------------------------------------------------------------------


class TestFilingFreshness:
    def test_q2_used_while_q3_is_published_is_stale(self):
        """The 18:53 report used equity at 2026-06-30 although the Q3 filing
        (period end 2026-09-30) was published on 2026-11-07."""
        verdict = check_filing_freshness(
            used_period_end="2026-06-30",
            known_period_ends=["2026-06-30", "2026-09-30"],
            as_of_date=AS_OF,
        )
        assert verdict.status == STALE_FUNDAMENTALS
        assert verdict.blocks_decision
        assert verdict.latest_known_period_end == "2026-09-30"

    def test_q2_was_current_before_the_q3_release(self):
        verdict = check_filing_freshness(
            used_period_end="2026-06-30",
            known_period_ends=["2026-06-30", "2026-09-30"],
            as_of_date="2026-11-06",
        )
        assert not verdict.blocks_decision

    def test_using_the_latest_known_period_passes(self):
        verdict = check_filing_freshness(
            used_period_end="2026-09-30",
            known_period_ends=["2026-06-30", "2026-09-30"],
            as_of_date=AS_OF,
        )
        assert not verdict.blocks_decision

    def test_no_known_filing_blocks(self):
        verdict = check_filing_freshness(
            used_period_end="2026-06-30", known_period_ends=[], as_of_date=AS_OF,
        )
        assert verdict.blocks_decision

    def test_an_undatable_report_blocks(self):
        verdict = check_filing_freshness(
            used_period_end=None,
            known_period_ends=["2026-09-30"],
            as_of_date=AS_OF,
        )
        assert verdict.blocks_decision


# ---------------------------------------------------------------------------
# FACT-REG-003 — same-period conflicts vs different-period history
# ---------------------------------------------------------------------------


def _obs(**overrides) -> MetricObservation:
    base = {
        "metric_id": "buyback_amount", "agent": "news", "value": 9600.0,
        "unit": "USD_million", "currency": "USD", "period": "2026-Q3",
        "period_end": "2026-09-30", "segment": "company",
        "metric_definition": "share_repurchases_during_period",
        "source_type": "company_release", "verified": True,
    }
    base.update(overrides)
    return MetricObservation(**base)


class TestFactConflicts:
    def test_h1_and_q3_buybacks_are_not_a_conflict(self):
        """The 18:53 report's Fundamentals reasoned on H1 buybacks while
        News reported Q3's: different periods, not a contradiction."""
        verdict = check_metric_definition([
            _obs(period="2026-H1", period_end="2026-06-30", value=4000.0),
            _obs(period="2026-Q3", period_end="2026-09-30", value=9600.0),
        ])
        assert not verdict.blocks_decision
        assert verdict.conflicts == []

    def test_no_q3_buyback_conflicts_with_a_confirmed_q3_buyback(self):
        """'No Q3 buyback' against a sourced Q3 buyback: a material conflict."""
        verdict = check_metric_definition([
            _obs(),
            _obs(agent="fundamentals", value=None, verified=False,
                 source_type="vendor_api",
                 metric_definition="absence_asserted"),
        ])
        assert verdict.blocks_decision
        assert verdict.conflicts[0].reason_code == "FACT_CONFLICT_UNRESOLVED"

    def test_a_verified_primary_resolves_the_conflict(self):
        verdict = check_metric_definition([
            _obs(),
            _obs(agent="fundamentals", value=100.0, verified=False,
                 source_type="vendor_api"),
        ])
        # The verified company release wins; the conflict is recorded but
        # resolved, so it does not block.
        assert not verdict.blocks_decision
        assert verdict.conflicts[0].resolved_by == "news"

    def test_conflicting_values_without_a_verified_primary_block(self):
        verdict = check_metric_definition([
            _obs(verified=False, source_type="news_article"),
            _obs(agent="fundamentals", value=100.0, verified=False,
                 source_type="vendor_api"),
        ])
        assert verdict.blocks_decision

    def test_different_definitions_are_not_compared(self):
        """GAAP EPS and operating EPS are different metrics: no conflict."""
        verdict = check_metric_definition([
            _obs(metric_id="eps_gaap", value=5.94),
            _obs(metric_id="eps_operating", value=5.88),
        ])
        assert verdict.conflicts == []

    def test_different_segments_are_not_compared(self):
        verdict = check_metric_definition([
            _obs(metric_id="revenue", segment="company", value=100_000.0),
            _obs(metric_id="revenue", segment="BNSF", value=6_000.0),
        ])
        assert verdict.conflicts == []


# ---------------------------------------------------------------------------
# VAL-REG-004 — P/B over mixed periods and classes
# ---------------------------------------------------------------------------


class TestValuationReconciliation:
    def test_mixed_period_equity_and_shares_are_unreconciled(self):
        """The 18:53 P/B used Q2 equity with a Q3-era price: UNRECONCILED."""
        verdict = reconcile_valuation(
            price=570.89, price_class="B", price_currency="USD",
            equity=747_900.0, equity_period_end="2026-06-30",
            equity_currency="USD",
            shares_outstanding=1_303_907.0, shares_period_end="2026-09-30",
            price_as_of=AS_OF,
        )
        assert verdict.status == UNRECONCILED_VALUATION
        assert verdict.blocks_decision
        assert verdict.price_to_book is None

    def test_aligned_periods_produce_a_ratio(self):
        verdict = reconcile_valuation(
            price=570.89, price_class="B", price_currency="USD",
            equity=747_900.0, equity_period_end="2026-09-30",
            equity_currency="USD",
            shares_outstanding=1_303_907.0, shares_period_end="2026-09-30",
            price_as_of=AS_OF,
        )
        assert verdict.price_to_book is not None
        assert not verdict.blocks_decision

    def test_cross_class_book_value_is_rejected(self):
        """A class-A book value against a class-B price: wrong by 1500x."""
        verdict = reconcile_valuation(
            price=570.89, price_class="B", price_currency="USD",
            equity=747_900.0, equity_period_end="2026-09-30",
            equity_currency="USD",
            shares_outstanding=1_303_907.0, shares_period_end="2026-09-30",
            price_as_of=AS_OF,
            book_value_class="A",
        )
        assert verdict.status == UNRECONCILED_VALUATION

    def test_a_currency_mismatch_is_unreconciled(self):
        verdict = reconcile_valuation(
            price=14.20, price_class=None, price_currency="EUR",
            equity=747_900.0, equity_period_end="2026-09-30",
            equity_currency="USD",
            shares_outstanding=1_303_907.0, shares_period_end="2026-09-30",
            price_as_of=AS_OF,
        )
        assert verdict.status == UNRECONCILED_VALUATION


# ---------------------------------------------------------------------------
# MATH-REG-005 — TTM vs FY trend comparisons
# ---------------------------------------------------------------------------


class TestTrendComparison:
    def test_ttm_85_77_vs_fy_88_99_is_not_an_acceleration(self):
        """The 18:53 report called 85.77 TTM 'accelerating' against 88.99 FY:
        different period types, in the wrong order — INVALID_TREND_COMPARISON."""
        verdict = check_trend_comparison(
            earlier=MetricObservation(
                metric_id="operating_earnings", agent="fundamentals",
                value=88_990.0, unit="USD_million", period="2024-FY",
                period_end="2024-12-31", segment="company",
                metric_definition="operating_earnings_fiscal_year",
                source_type="sec_filing", verified=True,
            ),
            later=MetricObservation(
                metric_id="operating_earnings", agent="fundamentals",
                value=85_770.0, unit="USD_million", period="TTM",
                period_end="2026-09-30", segment="company",
                metric_definition="operating_earnings_trailing_twelve_months",
                source_type="company_release", verified=True,
            ),
            claimed_direction="accelerating",
        )
        assert verdict.status == INVALID_TREND_COMPARISON
        assert verdict.blocks_decision

    def test_like_for_like_quarters_compare_correctly(self):
        verdict = check_trend_comparison(
            earlier=_obs(metric_id="operating_earnings", value=10_000.0,
                         period="2026-Q2", period_end="2026-06-30"),
            later=_obs(metric_id="operating_earnings", value=12_705.0,
                       period="2026-Q3", period_end="2026-09-30"),
            claimed_direction="accelerating",
        )
        assert not verdict.blocks_decision

    def test_a_claimed_decline_that_is_a_rise_is_invalid(self):
        verdict = check_trend_comparison(
            earlier=_obs(metric_id="operating_earnings", value=10_000.0,
                         period="2026-Q2", period_end="2026-06-30"),
            later=_obs(metric_id="operating_earnings", value=12_705.0,
                       period="2026-Q3", period_end="2026-09-30"),
            claimed_direction="declining",
        )
        assert verdict.status == INVALID_TREND_COMPARISON

    def test_ytd_against_quarterly_is_invalid(self):
        verdict = check_trend_comparison(
            earlier=_obs(metric_id="revenue", value=50_000.0,
                         period="2026-YTD", period_end="2026-06-30",
                         metric_definition="revenue_year_to_date"),
            later=_obs(metric_id="revenue", value=30_000.0,
                       period="2026-Q3", period_end="2026-09-30",
                       metric_definition="revenue_quarter"),
            claimed_direction="accelerating",
        )
        assert verdict.status == INVALID_TREND_COMPARISON


# ---------------------------------------------------------------------------
# METRIC-REG-006 — EPS definitions are not interchangeable
# ---------------------------------------------------------------------------


class TestMetricDefinitions:
    def test_gaap_vs_operating_eps_beat_is_not_certified(self):
        """5.94 GAAP vs 5.88 consensus adjusted: no verified 'beat' without
        a compatible definition."""
        verdict = check_metric_definition([
            _obs(metric_id="eps_gaap", value=5.94, unit="USD",
                 metric_definition="eps_gaap_diluted"),
            _obs(metric_id="eps_consensus_adjusted", agent="social",
                 value=5.88, unit="USD", verified=False,
                 source_type="news_article",
                 metric_definition="eps_consensus_adjusted"),
        ])
        assert verdict.blocks_decision
        assert any(c.reason_code == EPS_DEFINITION_MISMATCH
                   for c in verdict.conflicts)

    def test_same_definition_values_agree(self):
        verdict = check_metric_definition([
            _obs(metric_id="eps_gaap", value=5.94, unit="USD",
                 metric_definition="eps_gaap_diluted"),
            _obs(metric_id="eps_gaap", agent="fundamentals", value=5.94,
                 unit="USD", metric_definition="eps_gaap_diluted"),
        ])
        assert not verdict.blocks_decision

    def test_same_definition_disagreement_is_a_conflict(self):
        verdict = check_metric_definition([
            _obs(metric_id="eps_gaap", value=5.94, unit="USD",
                 metric_definition="eps_gaap_diluted"),
            _obs(metric_id="eps_gaap", agent="fundamentals", value=5.10,
                 unit="USD", verified=False, source_type="vendor_api",
                 metric_definition="eps_gaap_diluted"),
        ])
        assert verdict.blocks_decision


# ---------------------------------------------------------------------------
# RISK-REG-007 — exposure cap binds before the risk budget does
# ---------------------------------------------------------------------------


class TestExposureCapSizing:
    def test_the_task_scenario_yields_8_shares_not_125(self):
        """Equity 100k, budget 1,000, entry 560, stop 552, per-symbol
        exposure cap 5%: the risk-only answer is 125 shares (70,000 USD,
        70% of equity) — the exposure cap binds at 8 shares (4,480 USD)."""
        result = size_with_exposure_cap(
            entry=560.0, stop=552.0, atr=8.0, risk_budget=1_000.0,
            equity=100_000.0, max_symbol_exposure=0.05,
        )
        assert result.qty == 8
        assert result.notional == pytest.approx(4_480.0)
        assert result.binding_constraint == "EXPOSURE_CAP"
        assert not result.blocked

    def test_without_a_cap_the_risk_budget_would_over_expose(self):
        """The uncapped sizing is exactly the failure the report made."""
        result = size_with_exposure_cap(
            entry=560.0, stop=552.0, atr=8.0, risk_budget=1_000.0,
            equity=100_000.0, max_symbol_exposure=None,
        )
        assert result.qty == 125
        assert result.notional == pytest.approx(70_000.0)
        assert result.binding_constraint == "RISK_BUDGET"

    def test_insufficient_cash_blocks(self):
        result = size_with_exposure_cap(
            entry=560.0, stop=552.0, atr=8.0, risk_budget=1_000.0,
            equity=100_000.0, max_symbol_exposure=0.05, cash=1_000.0,
        )
        assert result.blocked
        assert result.qty == 0

    def test_invalid_prices_block(self):
        result = size_with_exposure_cap(
            entry=0.0, stop=552.0, atr=8.0, risk_budget=1_000.0,
            equity=100_000.0, max_symbol_exposure=0.05,
        )
        assert result.blocked

    def test_a_short_stop_on_the_wrong_side_blocks(self):
        result = size_with_exposure_cap(
            entry=560.0, stop=568.0, atr=8.0, risk_budget=1_000.0,
            equity=100_000.0, max_symbol_exposure=0.05, direction="LONG",
        )
        assert result.blocked

    def test_a_short_position_uses_its_own_side(self):
        result = size_with_exposure_cap(
            entry=560.0, stop=568.0, atr=8.0, risk_budget=1_000.0,
            equity=100_000.0, max_symbol_exposure=0.05, direction="SHORT",
        )
        assert not result.blocked
        assert result.qty > 0


# ---------------------------------------------------------------------------
# RISK-REG-008 — aggregate risk across tranches and open positions
# ---------------------------------------------------------------------------


class TestAggregateTrancheRisk:
    def test_three_tranches_aggregate_within_the_cap(self):
        """Three tranches at different entries, each sized to its own stop;
        the aggregate must respect the total incremental risk budget."""
        tranches = [
            TranchePlan(entry=560.0, stop=552.0, atr=8.0),
            TranchePlan(entry=556.0, stop=550.0, atr=8.0),
            TranchePlan(entry=552.0, stop=546.0, atr=8.0),
        ]
        result = aggregate_tranche_risk(
            tranches, risk_budget=1_000.0, equity=100_000.0,
            max_symbol_exposure=0.05,
        )
        assert result.total_qty > 0
        assert result.total_risk <= 1_000.0 + 1e-9
        assert result.total_notional <= 100_000.0 * 0.05 + 1e-9
        assert not result.blocked

    def test_reusing_one_stop_for_all_tranches_is_flagged(self):
        """The 18:53 report reused stop 552 for tranches entered lower: a
        stop at or above a lower entry protects nothing on that tranche."""
        tranches = [
            TranchePlan(entry=560.0, stop=552.0, atr=8.0),
            TranchePlan(entry=548.0, stop=552.0, atr=8.0),  # stop above entry
        ]
        result = aggregate_tranche_risk(
            tranches, risk_budget=1_000.0, equity=100_000.0,
            max_symbol_exposure=0.05,
        )
        assert result.blocked
        assert any(t.reason_code == "INVALID_STOP" for t in result.tranches)

    def test_existing_position_counts_toward_the_exposure_cap(self):
        """An open position's notional consumes the per-symbol cap before
        the new tranches are sized."""
        tranches = [TranchePlan(entry=560.0, stop=552.0, atr=8.0)]
        result = aggregate_tranche_risk(
            tranches, risk_budget=1_000.0, equity=100_000.0,
            max_symbol_exposure=0.05, existing_notional=4_000.0,
        )
        # Only 1,000 USD of the 5,000 cap remains: 1 share at 560.
        assert result.total_notional <= 5_000.0 + 1e-9
        assert result.total_qty == 1

    def test_a_proposed_breakeven_stop_does_not_reduce_risk(self):
        """A stop move to breakeven that is only *proposed* must not lower
        the computed risk: the engine prices the registered stop."""
        tranches = [TranchePlan(entry=560.0, stop=552.0, atr=8.0,
                                proposed_stop=560.0)]
        result = aggregate_tranche_risk(
            tranches, risk_budget=1_000.0, equity=100_000.0,
            max_symbol_exposure=0.05,
        )
        # Risk is computed from the registered stop (552), not the proposal.
        assert result.total_risk == pytest.approx(
            result.tranches[0].qty * (560.0 - 552.0)
        )

    def test_the_aggregate_budget_binds_across_tranches(self):
        tranches = [
            TranchePlan(entry=560.0, stop=552.0, atr=8.0),
            TranchePlan(entry=556.0, stop=550.0, atr=8.0),
            TranchePlan(entry=552.0, stop=546.0, atr=8.0),
        ]
        result = aggregate_tranche_risk(
            tranches, risk_budget=100.0, equity=100_000.0,
            max_symbol_exposure=0.05,
        )
        assert result.total_risk <= 100.0 + 1e-9


# ---------------------------------------------------------------------------
# ENTRY-REG-009 — conflicting / mutually-exclusive entry conditions
# ---------------------------------------------------------------------------


class TestEntryConditions:
    def test_gap_open_558_violates_the_cancel_rule(self):
        """'Open below 559.61 cancels the setup' vs 'buy the dip to 557-562':
        an open at 558 satisfies the dip-buy but violates the cancel rule —
        the plan must not authorize a purchase."""
        conditions = [
            EntryCondition(description="open below 559.61 cancels the setup",
                           basis="open", operator="<", threshold=559.61,
                           action="CANCEL_SETUP"),
            EntryCondition(description="buy the dip toward 557-562",
                           basis="price", operator="between",
                           threshold=557.0, second_threshold=562.0,
                           action="BUY"),
        ]
        verdict = validate_entry_conditions(conditions, open_price=558.0)
        assert verdict.status == INVALID_ENTRY_CONDITION
        assert verdict.blocks_decision
        assert verdict.authorizes_buy is False

    def test_an_open_at_560_keeps_the_setup_alive(self):
        conditions = [
            EntryCondition(description="open below 559.61 cancels the setup",
                           basis="open", operator="<", threshold=559.61,
                           action="CANCEL_SETUP"),
            EntryCondition(description="buy the dip toward 557-562",
                           basis="price", operator="between",
                           threshold=557.0, second_threshold=562.0,
                           action="BUY"),
        ]
        verdict = validate_entry_conditions(conditions, open_price=560.0)
        assert not verdict.blocks_decision
        assert verdict.authorizes_buy is True

    def test_an_open_at_580_is_outside_the_dip_band(self):
        conditions = [
            EntryCondition(description="open below 559.61 cancels the setup",
                           basis="open", operator="<", threshold=559.61,
                           action="CANCEL_SETUP"),
            EntryCondition(description="buy the dip toward 557-562",
                           basis="price", operator="between",
                           threshold=557.0, second_threshold=562.0,
                           action="BUY"),
        ]
        verdict = validate_entry_conditions(conditions, open_price=580.0)
        assert not verdict.blocks_decision
        assert verdict.authorizes_buy is False  # no dip to buy

    def test_a_close_based_trigger_does_not_execute_the_same_candle(self):
        """A condition on the *close* can only be evaluated after the close:
        it cannot authorize an order at that same candle's price."""
        conditions = [
            EntryCondition(description="close above 557.91 confirms",
                           basis="close", operator=">", threshold=557.91,
                           action="BUY"),
        ]
        verdict = validate_entry_conditions(
            conditions, open_price=558.0, close_observed=False,
        )
        assert not verdict.authorizes_buy
        assert verdict.reason_code == "CLOSE_TRIGGER_NOT_YET_EVALUABLE"

    def test_an_observed_close_above_the_trigger_authorizes(self):
        conditions = [
            EntryCondition(description="close above 557.91 confirms",
                           basis="close", operator=">", threshold=557.91,
                           action="BUY"),
        ]
        verdict = validate_entry_conditions(
            conditions, open_price=558.0, close_price=570.89,
            close_observed=True,
        )
        assert verdict.authorizes_buy is True

    def test_an_observed_close_below_the_trigger_does_not(self):
        conditions = [
            EntryCondition(description="close above 557.91 confirms",
                           basis="close", operator=">", threshold=557.91,
                           action="BUY"),
        ]
        verdict = validate_entry_conditions(
            conditions, open_price=558.0, close_price=550.0,
            close_observed=True,
        )
        assert verdict.authorizes_buy is False

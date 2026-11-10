"""P3: bar finality, news-to-price reconciliation, and macro period labels.

Three defects from the data lineage audit, all of them a number presented as
something it is not:

``F5``  a partial candle called a verified close (the 19:24 report on 2026-11-09
        read 576.74 at 15:01 New York, with the session open until 16:00).
``News``  an article's -1.01% quoted against an end-of-day change of +0.76%, as
        though the two measured the same window.
``Labels`` a comparison between September 2026 and November 2025 called "YoY"
        when the periods are ten months apart.

Every constant below is written down independently of the code under test: the
report times are the three actual runs in Europe/Rome, the prices are the two
closes the audit recorded, and the calendar facts are the NYSE's published ones.
"""

from __future__ import annotations

import pandas as pd

from tradingagents.dataflows.math.event_claims import (
    BenchmarkSnapshot,
    ComparisonType,
    EventClaim,
    EventClaimVerdict,
    reconcile_claims,
    reconcile_event_claim,
)
from tradingagents.dataflows.math.period_labels import (
    PeriodKind,
    PeriodVerdict,
    check_period_label,
    growth_for_label,
    label_kind,
    parse_period,
)
from tradingagents.dataflows.vendors.bar_finality import (
    BarFinality,
    assess_finality,
    describe_series,
)
from tradingagents.dataflows.vendors.yahoo.ohlcv import assess_last_bar

# The three report times of 2026-11-09, in Europe/Rome.
REPORT_1837 = "2026-11-09T18:37:00+01:00"
REPORT_1924 = "2026-11-09T19:24:00+01:00"
REPORT_2101 = "2026-11-09T21:01:00+01:00"

# The two closes the audit recorded for BRK-B.
CLOSE_2026_11_06 = 570.89
CLOSE_2026_11_09 = 575.25


# ===========================================================================
# Task A — a bar's finality
# ===========================================================================
class TestSessionStillTrading:
    def test_the_three_report_times_are_intraday(self):
        """18:37 / 19:24 / 21:01 Rome is 12:37 / 13:24 / 15:01 New York."""
        for stamp in (REPORT_1837, REPORT_1924, REPORT_2101):
            bar = assess_finality(stamp)
            assert bar.finality is BarFinality.INTRADAY_PARTIAL, stamp
            assert bar.usable_for_eod is False
            assert bar.usable_for_intraday is True

    def test_an_intraday_close_cannot_support_an_eod_decision(self):
        """F5: the Close field alone did not prove the session had ended."""
        bar = assess_finality(REPORT_2101)
        assert bar.is_provisional is True
        assert "partial candle, not a close" in bar.reason


class TestAfterTheClose:
    def test_just_after_the_bell_is_pending_not_confirmed(self):
        """The vendor may still restate: closing is not settling."""
        bar = assess_finality("2026-11-09T16:02:00-05:00")
        assert bar.finality is BarFinality.EOD_PENDING_CONFIRMATION
        assert bar.usable_for_eod is False

    def test_past_the_grace_the_bar_is_confirmed(self):
        bar = assess_finality("2026-11-09T16:30:00-05:00")
        assert bar.finality is BarFinality.EOD_CONFIRMED
        assert bar.usable_for_eod is True

    def test_a_later_judgement_of_the_same_bar_can_confirm_it(self):
        """A stored bar read the next morning is final even if it was not then."""
        same_bar = "2026-11-09T16:00:00-05:00"
        at_the_bell = assess_finality(same_bar)
        next_day = assess_finality(same_bar, now="2026-11-10T09:00:00-05:00")
        assert at_the_bell.finality is BarFinality.EOD_PENDING_CONFIRMATION
        assert next_day.finality is BarFinality.EOD_CONFIRMED


class TestCalendarEdges:
    def test_a_holiday_cannot_produce_a_close(self):
        """Christmas Day 2026: no session, so no bar dated that day is a close."""
        bar = assess_finality("2026-12-25T18:00:00-05:00")
        assert bar.finality is BarFinality.UNKNOWN
        assert bar.usable_for_eod is False
        assert "not a trading day" in bar.reason

    def test_an_early_close_settles_earlier(self):
        """The day after Thanksgiving closes at 13:00, not 16:00."""
        during = assess_finality("2026-11-27T12:30:00-05:00")
        after = assess_finality("2026-11-27T13:30:00-05:00")
        assert during.finality is BarFinality.INTRADAY_PARTIAL
        assert after.finality is BarFinality.EOD_CONFIRMED

    def test_the_same_rome_clock_time_flips_with_daylight_saving(self):
        """19:24 Rome is 13:24 ET in winter and 12:24 ET under EDT — both open."""
        winter = assess_finality("2026-11-09T19:24:00+01:00")   # CET, 13:24 ET
        summer = assess_finality("2026-07-15T19:24:00+02:00")   # CEST, 12:24 ET
        assert winter.finality is BarFinality.INTRADAY_PARTIAL
        assert summer.finality is BarFinality.INTRADAY_PARTIAL

    def test_a_stale_series_is_not_a_close(self):
        bar = assess_finality(REPORT_2101, session_date="2026-11-05",
                              last_expected_session="2026-11-09")
        assert bar.finality is BarFinality.STALE
        assert bar.usable_for_eod is False


class TestBarProvenance:
    def test_every_field_the_task_asks_for_travels_with_the_bar(self):
        bar = assess_finality(REPORT_2101, snapshot_id="BRK-B-2026-11-09")
        payload = bar.summary()
        for field in ("session_date", "exchange_timezone", "observation_timestamp",
                      "source_timestamp", "finality", "snapshot_id",
                      "session_calendar"):
            assert field in payload, field
        assert payload["snapshot_id"] == "BRK-B-2026-11-09"
        assert payload["exchange_timezone"] == "America/New_York"

    def test_the_ohlcv_frame_reports_its_last_bar_finality(self):
        frame = pd.DataFrame({"Date": pd.to_datetime(["2026-11-06", "2026-11-09"]),
                              "Close": [CLOSE_2026_11_06, CLOSE_2026_11_09]})
        bar = assess_last_bar("BRK-B", frame, now=REPORT_2101)
        assert bar.finality is BarFinality.INTRADAY_PARTIAL
        assert bar.session_date == "2026-11-09"
        assert bar.snapshot_id == "BRK-B-2026-11-09"

    def test_an_empty_frame_is_unknown_not_confirmed(self):
        bar = assess_last_bar("BRK-B", pd.DataFrame(), now=REPORT_2101)
        assert bar.finality is BarFinality.UNKNOWN

    def test_derived_indicators_are_labelled_provisional(self):
        """A series ending intraday declares it, so an indicator built on it does."""
        series = describe_series([BarFinality.EOD_CONFIRMED,
                                  BarFinality.EOD_CONFIRMED,
                                  BarFinality.INTRADAY_PARTIAL])
        assert series["includes_partial_bar"] is True
        assert series["includes_unconfirmed_bar"] is True
        assert series["bars_confirmed"] == 2


# ===========================================================================
# Task B — a news figure is not the session's change
# ===========================================================================
def _benchmark() -> BenchmarkSnapshot:
    return BenchmarkSnapshot(session_date="2026-11-09",
                             previous_close=CLOSE_2026_11_06,
                             close=CLOSE_2026_11_09,
                             snapshot_id="BRK-B-2026-11-09")


class TestNewsReconciliation:
    def test_the_certified_change_is_plus_0_76(self):
        assert str(_benchmark().change_pct) == "0.7637"

    def test_an_intraday_article_is_not_the_eod_change(self):
        """The audit's -1.01%: a real move over a different window."""
        claim = EventClaim(stated_pct=-1.01, comparison=ComparisonType.INTRADAY,
                           provider="yahoo", published_at="2026-11-09T17:00:00Z",
                           url="https://example.test/a")
        result = reconcile_event_claim(claim, _benchmark())
        assert result.reconciliation_status is EventClaimVerdict.TIME_WINDOW_MISMATCH
        assert result.may_replace_eod_change is False
        # Kept, and quotable as what it is — never deleted for disagreeing.
        assert result.quotable is True

    def test_the_article_never_replaces_the_certified_change(self):
        claim = EventClaim(stated_pct=-1.01, comparison=ComparisonType.INTRADAY,
                           published_at="2026-11-09T17:00:00Z")
        result = reconcile_event_claim(claim, _benchmark())
        assert result.may_replace_eod_change is False
        assert str(_benchmark().change_pct) == "0.7637"

    def test_an_agreeing_close_to_close_article_is_confirmed(self):
        claim = EventClaim(stated_pct=0.76, comparison=ComparisonType.CLOSE_TO_CLOSE,
                           published_at="2026-11-10T09:00:00Z")
        result = reconcile_event_claim(claim, _benchmark())
        assert result.reconciliation_status is EventClaimVerdict.CONFIRMED
        assert result.may_replace_eod_change is True

    def test_a_close_to_close_article_that_disagrees_is_contradicted(self):
        claim = EventClaim(stated_pct=-3.5, comparison=ComparisonType.CLOSE_TO_CLOSE,
                           published_at="2026-11-10T09:00:00Z")
        result = reconcile_event_claim(claim, _benchmark())
        assert result.reconciliation_status is EventClaimVerdict.CONTRADICTED

    def test_an_article_about_an_earlier_session_is_context(self):
        claim = EventClaim(stated_pct=-1.0, comparison=ComparisonType.CLOSE_TO_CLOSE,
                           session_date="2026-11-06",
                           published_at="2026-11-07T09:00:00Z")
        result = reconcile_event_claim(claim, _benchmark())
        assert result.reconciliation_status is EventClaimVerdict.HISTORICAL_CONTEXT
        assert result.may_replace_eod_change is False

    def test_an_article_without_a_publication_time_is_unverified(self):
        claim = EventClaim(stated_pct=0.76, comparison=ComparisonType.CLOSE_TO_CLOSE)
        result = reconcile_event_claim(claim, _benchmark())
        assert result.reconciliation_status is EventClaimVerdict.UNVERIFIED
        assert result.quotable is False

    def test_no_benchmark_means_nothing_can_be_confirmed(self):
        claim = EventClaim(stated_pct=0.76, comparison=ComparisonType.CLOSE_TO_CLOSE,
                           published_at="2026-11-10T09:00:00Z")
        result = reconcile_event_claim(claim, None)
        assert result.reconciliation_status is EventClaimVerdict.UNVERIFIED

    def test_every_reconciliation_keeps_its_provenance(self):
        claim = EventClaim(stated_pct=-1.01, comparison=ComparisonType.INTRADAY,
                           url="https://example.test/a", provider="yahoo",
                           published_at="2026-11-09T17:00:00Z",
                           updated_at="2026-11-09T18:00:00Z",
                           window_start="2026-11-09T14:30:00Z",
                           window_end="2026-11-09T17:00:00Z")
        payload = reconcile_event_claim(claim, _benchmark()).summary()
        for field in ("url", "provider", "published_at", "updated_at",
                      "window_start", "window_end", "comparison", "benchmark",
                      "reconciliation_status"):
            assert field in payload, field
        assert "change_pct" in payload["benchmark"]

    def test_a_batch_is_reconciled_against_one_snapshot(self):
        claims = [
            EventClaim(stated_pct=-1.01, comparison=ComparisonType.INTRADAY,
                       published_at="2026-11-09T17:00:00Z"),
            EventClaim(stated_pct=0.76, comparison=ComparisonType.CLOSE_TO_CLOSE,
                       published_at="2026-11-10T09:00:00Z"),
        ]
        verdicts = [c.reconciliation_status for c in reconcile_claims(claims, _benchmark())]
        assert verdicts == [EventClaimVerdict.TIME_WINDOW_MISMATCH,
                            EventClaimVerdict.CONFIRMED]


# ===========================================================================
# Task C — the period labels
# ===========================================================================
class TestPeriodLabels:
    def test_the_audit_case_is_a_period_mismatch(self):
        """Sep 2026 against Nov 2025 is ten months, not twelve."""
        check = check_period_label("YoY", "Nov 2025", "Sep 2026")
        assert check.verdict is PeriodVerdict.PERIOD_MISMATCH
        assert check.blocks is True
        assert check.months_apart == 10
        assert check.required_months == 12

    def test_yoy_needs_twelve_months(self):
        assert check_period_label("YoY", "Sep 2025", "Sep 2026").verdict \
            is PeriodVerdict.CONSISTENT

    def test_mom_needs_consecutive_months(self):
        assert check_period_label("MoM", "Aug 2026", "Sep 2026").verdict \
            is PeriodVerdict.CONSISTENT
        assert check_period_label("MoM", "Sep 2026", "Nov 2026").verdict \
            is PeriodVerdict.PERIOD_MISMATCH

    def test_quarterly_yoy_needs_the_same_quarter_a_year_earlier(self):
        assert check_period_label("Q3 YoY", "Q3 2025", "Q3 2026").verdict \
            is PeriodVerdict.CONSISTENT
        assert check_period_label("Q3 YoY", "Q2 2025", "Q3 2026").verdict \
            is PeriodVerdict.PERIOD_MISMATCH

    def test_qoq_needs_consecutive_quarters(self):
        assert check_period_label("QoQ", "Q2 2026", "Q3 2026").verdict \
            is PeriodVerdict.CONSISTENT

    def test_annualized_needs_a_formula_not_just_a_word(self):
        check = check_period_label("Annualized", "2026-01", "2026-09")
        assert check.verdict is PeriodVerdict.UNVERIFIED
        assert check.blocks is False   # unverifiable, not proven wrong

    def test_a_mismatched_pair_yields_no_number(self):
        """The arithmetic would produce a figure a reader would quote unread."""
        value, check = growth_for_label(100.0, 120.0, "YoY",
                                        "Nov 2025", "Sep 2026")
        assert value is None
        assert check.blocks is True

    def test_a_consistent_pair_yields_the_growth(self):
        value, check = growth_for_label(100.0, 120.0, "YoY",
                                        "Sep 2025", "Sep 2026")
        assert str(value) == "20.0000"
        assert check.verdict is PeriodVerdict.CONSISTENT

    def test_the_label_vocabulary(self):
        assert label_kind("YoY") is PeriodKind.YEAR_OVER_YEAR
        assert label_kind("MoM change") is PeriodKind.MONTH_OVER_MONTH
        assert label_kind("QoQ") is PeriodKind.QUARTER_OVER_QUARTER
        assert label_kind("annualised rate") is PeriodKind.ANNUALIZED
        assert label_kind("something else") is PeriodKind.UNKNOWN

    def test_periods_parse_in_the_spellings_the_vendors_use(self):
        assert parse_period("Sep 2026").month == 9
        assert parse_period("2026-Q3").quarter == 3
        assert parse_period("Q3 2026").quarter == 3
        assert parse_period("FY2025").year == 2025

"""Cross-agent consistency: causal and numeric checks over the run's reports.

Regression fixture: the BRK-B report of 2026-11-08, where the Sentiment
Analyst wrote that the market had already absorbed the Q3 results while the
News Analyst's causal check proved no post-event session had traded. The
checks here are structural — no ticker, no company name.
"""


from tradingagents.dataflows.cross_agent import (
    NOT_YET_OBSERVABLE,
    OBSERVABLE,
    ConsistencyIssue,
    check_causal_consistency,
    check_numeric_consistency,
    check_reports,
)


def _reports(sentiment: str = "", news: str = "", market: str = "") -> dict[str, str]:
    return {
        "market_report": market,
        "sentiment_report": sentiment,
        "news_report": news,
        "fundamentals_report": "",
    }


class TestCausalConsistency:
    def test_reaction_claim_with_no_post_event_session_is_an_error(self):
        reports = _reports(
            sentiment="The market has already absorbed the Q3 results; positioning is constructive."
        )
        verdict = check_causal_consistency(reports, NOT_YET_OBSERVABLE)
        assert verdict.blocks_decision
        codes = [i.code for i in verdict.issues]
        assert "CROSS_AGENT_REACTION_UNOBSERVABLE" in codes
        issue = verdict.issues[0]
        assert issue.report_key == "sentiment_report"
        assert issue.severity == "error"

    def test_conditional_reaction_claim_is_not_flagged(self):
        reports = _reports(
            sentiment="The market may react to the results once the next session opens."
        )
        verdict = check_causal_consistency(reports, NOT_YET_OBSERVABLE)
        assert not verdict.blocks_decision
        assert verdict.issues == []

    def test_observable_status_makes_no_verdict(self):
        reports = _reports(
            sentiment="The market has already absorbed the Q3 results."
        )
        verdict = check_causal_consistency(reports, OBSERVABLE)
        assert verdict.issues == []

    def test_unknown_status_makes_no_verdict(self):
        reports = _reports(sentiment="The market has absorbed the results.")
        verdict = check_causal_consistency(reports, None)
        assert verdict.issues == []

    def test_reaction_claim_in_news_report_is_flagged(self):
        reports = _reports(news="Shares jumped on the announcement, which was priced in quickly.")
        verdict = check_causal_consistency(reports, NOT_YET_OBSERVABLE)
        assert verdict.blocks_decision
        assert verdict.issues[0].report_key == "news_report"

    def test_empty_reports_produce_no_issues(self):
        verdict = check_causal_consistency(_reports(), NOT_YET_OBSERVABLE)
        assert verdict.issues == []
        assert not verdict.blocks_decision


class TestNumericConsistency:
    def test_matching_move_passes(self):
        verdict = check_numeric_consistency(-0.25, -0.2517)
        assert verdict.issues == []

    def test_mismatched_move_is_an_error(self):
        verdict = check_numeric_consistency(-2.5, -0.2517)
        assert verdict.blocks_decision
        issue = verdict.issues[0]
        assert issue.code == "CROSS_AGENT_NUMERIC_MISMATCH"
        assert issue.severity == "error"

    def test_missing_sides_make_no_verdict(self):
        assert check_numeric_consistency(None, -0.25).issues == []
        assert check_numeric_consistency(-0.25, None).issues == []

    def test_within_tolerance_passes(self):
        verdict = check_numeric_consistency(-0.30, -0.2517, tolerance_pct=0.05)
        assert verdict.issues == []


class TestCheckReports:
    def test_combined_checks_feed_the_audit(self):
        verdict = check_reports(
            _reports(sentiment="The market has already absorbed the results."),
            post_event_status=NOT_YET_OBSERVABLE,
            stated_pct=-2.5,
            computed_pct=-0.2517,
        )
        assert verdict.blocks_decision
        summary = verdict.summary()
        assert summary["errors"] == 2
        assert set(summary["codes"]) == {
            "CROSS_AGENT_REACTION_UNOBSERVABLE",
            "CROSS_AGENT_NUMERIC_MISMATCH",
        }

    def test_clean_reports_pass(self):
        verdict = check_reports(
            _reports(sentiment="Positioning is constructive; the reaction is not yet observable."),
            post_event_status=NOT_YET_OBSERVABLE,
            stated_pct=-0.25,
            computed_pct=-0.2517,
        )
        assert not verdict.blocks_decision
        assert verdict.summary()["total_issues"] == 0

    def test_issue_is_error_property(self):
        assert ConsistencyIssue("X", "error", "r", "m").is_error
        assert not ConsistencyIssue("X", "warning", "r", "m").is_error

"""P0 FRESH-001 — fundamentals freshness gate.

Regression: the BRK-B report of 2026-11-08 used Q2 2026 (period ended
2026-06-30) although Q3 (period ended 2026-09-30) had already been
published on 2026-11-07. The gate must compare the used period against the
latest filing *known at the analysis date*, never against the latest
record a vendor serves. Pure logic, frozen dates, no network.
"""

from __future__ import annotations

from tradingagents.dataflows.fundamentals_freshness import (
    CURRENT,
    FUNDAMENTALS_CURRENT,
    FUNDAMENTALS_STALE,
    FUNDAMENTALS_UNAVAILABLE,
    STALE_CRITICAL,
    UNAVAILABLE,
    FilingRecord,
    check_fundamentals_freshness,
    filings_from_sec_edgar,
    latest_known_filing,
)

Q2 = FilingRecord("2026-Q2", "2026-06-30", published_date="2026-08-02",
                  filing_date="2026-08-07", form="10-Q")
Q3 = FilingRecord("2026-Q3", "2026-09-30", published_date="2026-11-07",
                  filing_date=None, form="8-K")


class TestLatestKnownFiling:
    def test_q3_published_saturday_is_known_by_sunday(self):
        assert latest_known_filing([Q2, Q3], "2026-11-08") is Q3

    def test_q3_is_not_known_before_its_release(self):
        """No look-ahead: on the Friday before the Saturday release, Q2 leads."""
        assert latest_known_filing([Q2, Q3], "2026-11-06") is Q2

    def test_a_filing_dated_after_as_of_is_invisible(self):
        future = FilingRecord("2026-Q4", "2026-12-31", filing_date="2027-02-20")
        assert latest_known_filing([Q2, Q3, future], "2026-11-08") is Q3

    def test_no_known_filing_returns_none(self):
        assert latest_known_filing([Q2, Q3], "2026-01-01") is None

    def test_a_record_with_no_dates_is_never_known(self):
        ghost = FilingRecord("2026-Q1", "2026-03-31")
        assert latest_known_filing([ghost], "2026-11-08") is None

    def test_filing_date_alone_makes_a_period_known(self):
        filed_only = FilingRecord("2026-Q3", "2026-09-30", filing_date="2026-11-06")
        assert latest_known_filing([Q2, filed_only], "2026-11-08") is filed_only


class TestFreshnessVerdict:
    def test_using_q2_when_q3_is_known_is_stale_critical(self):
        """The regression: Q2 presented as current while Q3 was published."""
        verdict = check_fundamentals_freshness(
            "2026-06-30", [Q2, Q3], "2026-11-08", used_period="2026-Q2"
        )
        assert verdict.status == STALE_CRITICAL
        assert verdict.reason_code == FUNDAMENTALS_STALE
        assert verdict.blocks_decision
        assert verdict.latest_known_period == "2026-Q3"
        assert verdict.used_period == "2026-Q2"

    def test_using_q3_is_current(self):
        verdict = check_fundamentals_freshness(
            "2026-09-30", [Q2, Q3], "2026-11-08", used_period="2026-Q3"
        )
        assert verdict.status == CURRENT
        assert verdict.reason_code == FUNDAMENTALS_CURRENT
        assert not verdict.blocks_decision

    def test_q2_was_current_before_the_q3_release(self):
        """The same Q2 usage was correct on the Friday before the release."""
        verdict = check_fundamentals_freshness(
            "2026-06-30", [Q2, Q3], "2026-11-06", used_period="2026-Q2"
        )
        assert verdict.status == CURRENT

    def test_no_known_filing_is_unavailable(self):
        verdict = check_fundamentals_freshness("2026-06-30", [], "2026-11-08")
        assert verdict.status == UNAVAILABLE
        assert verdict.reason_code == FUNDAMENTALS_UNAVAILABLE
        assert verdict.blocks_decision

    def test_unknown_used_period_is_unavailable_not_current(self):
        """An undated fundamentals report cannot be called fresh."""
        verdict = check_fundamentals_freshness(
            None, [Q2, Q3], "2026-11-08", used_period=None
        )
        assert verdict.status == UNAVAILABLE
        assert verdict.blocks_decision

    def test_summary_is_machine_readable(self):
        verdict = check_fundamentals_freshness(
            "2026-06-30", [Q2, Q3], "2026-11-08", used_period="2026-Q2"
        )
        data = verdict.summary()
        assert data["status"] == STALE_CRITICAL
        assert data["reason_code"] == FUNDAMENTALS_STALE
        assert data["latest_known_period_end"] == "2026-09-30"


class TestSecEdgarExtraction:
    def test_filings_after_as_of_are_excluded(self, monkeypatch, tmp_path):
        """A period whose every filing postdates the analysis date is invisible."""
        from tradingagents.dataflows.vendors import sec_edgar

        monkeypatch.setattr(sec_edgar, "cik_for", lambda t: "0001067983")
        monkeypatch.setattr(sec_edgar, "_cached_json", lambda url, name: {
            "facts": {"us-gaap": {
                "Revenues": {"units": {"USD": [
                    {"end": "2026-06-30", "filed": "2026-08-07", "val": 1.0},
                    {"end": "2026-09-30", "filed": "2026-11-07", "val": 2.0},
                ]}},
            }},
        })
        filings = filings_from_sec_edgar("BRK-B", "2026-11-08")
        ends = {f.period_end for f in filings}
        assert "2026-06-30" in ends
        assert "2026-09-30" in ends

        # A run dated before the Q3 filing must not see it.
        early = filings_from_sec_edgar("BRK-B", "2026-11-06")
        assert "2026-09-30" not in {f.period_end for f in early}

    def test_an_unreachable_sec_yields_no_filings_not_a_crash(self, monkeypatch):
        from tradingagents.dataflows.vendors import sec_edgar

        def boom(url):
            raise RuntimeError("SEC down")

        monkeypatch.setattr(sec_edgar, "cik_for", lambda t: "0001067983")
        monkeypatch.setattr(sec_edgar, "_cached_json", boom)
        assert filings_from_sec_edgar("BRK-B", "2026-11-08") == []

    def test_a_non_us_filer_yields_no_filings(self, monkeypatch):
        from tradingagents.dataflows.vendors import sec_edgar

        monkeypatch.setattr(sec_edgar, "cik_for", lambda t: None)
        assert filings_from_sec_edgar("ENI.MI", "2026-11-08") == []

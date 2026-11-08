"""Filing freshness must see releases that carry no XBRL facts.

The 2026-11-08 report used Q2 equity while the Q3 results had been released
on 2026-11-07. Two independent causes were found in the audit: the process
held pre-merge code (operational), and — this file — the SEC
``companyfacts`` endpoint had no period after 2026-06-30, so there was no
Q3 filing to compare against.

Berkshire's Q3 release is an **8-K**, and an 8-K announces results without
restating them in XBRL. A freshness gate that reads only ``companyfacts``
therefore cannot see the release at all, and the older quarter looks
current. These tests pin the fix: the submission history supplies the
release date, so a period announced but not yet tagged is still recognised
as published.
"""

from __future__ import annotations

from tradingagents.dataflows.filing_freshness import (
    FilingRecord,
    check_filing_freshness,
    filings_from_submissions,
    latest_known_filing,
)


def _submissions_payload():
    """The shape of SEC ``/submissions/CIK##########.json``, trimmed.

    The dates reflect reality: a periodic report's ``reportDate`` **is** the
    fiscal period end, while an 8-K's ``reportDate`` is the *event* date —
    an 8-K announcing Q3 results on 2026-11-07 carries ``reportDate``
    2026-11-07, not 2026-09-30. Treating the two alike was one of the two
    bugs this file's tests caught on real data.
    """
    return {
        "cik": "0001067983",
        "name": "BERKSHIRE HATHAWAY INC",
        "filings": {
            "recent": {
                "accessionNumber": [
                    "0001067983-26-000099",   # Q3 results announcement
                    "0001067983-26-000080",   # an insider Form 4
                    "0001067983-26-000050",   # Q2 10-Q
                    "0001067983-26-000010",   # Q1 10-Q
                ],
                "form": ["8-K", "4", "10-Q", "10-Q"],
                "filingDate": ["2026-11-07", "2026-10-29",
                               "2026-08-10", "2026-05-04"],
                # 8-K reportDate = event date; 4 = transaction date.
                "reportDate": ["2026-11-07", "2026-10-27",
                               "2026-06-30", "2026-03-31"],
                "acceptanceDateTime": [
                    "2026-11-07T16:05:12.000Z", "2026-10-29T20:00:00.000Z",
                    "2026-08-10T16:02:00.000Z", "2026-05-04T16:01:00.000Z",
                ],
                "primaryDocument": [
                    "brk-20261107.htm", "xslF345X02/edgar.xml",
                    "brk-20260630.htm", "brk-20260331.htm",
                ],
                "items": ["2.02,9.01", "", "", ""],
            },
        },
    }


class TestOnlyPeriodicFilingsCarryPeriods:
    def test_form_4_is_not_a_period_filing(self):
        """An insider Form 4 has a transaction date, not a fiscal period.

        Reading it as one made 2026-10-30 the "latest period", which would
        have hidden a real staleness behind an unrelated filing.
        """
        filings = filings_from_submissions(_submissions_payload())
        assert all(f.period_end != "2026-10-27" for f in filings)
        assert "4" not in {f.form_type for f in filings}

    def test_the_8k_is_kept_as_an_announcement_not_a_period(self):
        filings = filings_from_submissions(_submissions_payload())
        announcements = [f for f in filings if f.is_announcement]
        assert len(announcements) == 1
        ann = announcements[0]
        assert ann.filing_date == "2026-11-07"
        assert ann.form_type == "8-K"
        # Its reportDate is the event date, never used as a fiscal period.
        assert ann.period_end is None

    def test_periodic_forms_keep_their_report_date_as_the_period(self):
        filings = filings_from_submissions(_submissions_payload())
        periods = {f.period_end for f in filings if not f.is_announcement}
        assert periods == {"2026-06-30", "2026-03-31"}

    def test_an_8k_without_item_2_02_is_not_an_announcement(self):
        payload = {
            "filings": {"recent": {
                "accessionNumber": ["a"], "form": ["8-K"],
                "filingDate": ["2026-04-16"], "reportDate": ["2026-04-16"],
                "acceptanceDateTime": [""], "primaryDocument": [""],
                "items": ["8.01,9.01"],
            }},
        }
        assert filings_from_submissions(payload) == []


class TestAnnouncementsMakeAPeriodStale:
    def test_a_results_announcement_after_the_used_period_is_stale(self):
        """Q2 used, Q3 results announced 2026-11-07: stale, not current.

        The 8-K gives no fiscal period end, so the verdict rests on the
        announcement's date rather than an invented period.
        """
        filings = filings_from_submissions(_submissions_payload())
        verdict = check_filing_freshness(
            "2026-06-30", filings, "2026-11-08", used_period="2026-Q2",
        )
        assert verdict.status == "STALE_FUNDAMENTALS"
        assert verdict.blocks_decision
        assert "announced" in verdict.reason.lower()

    def test_before_the_announcement_q2_is_current(self):
        filings = filings_from_submissions(_submissions_payload())
        verdict = check_filing_freshness("2026-06-30", filings, "2026-11-06")
        assert not verdict.blocks_decision

    def test_announcements_are_never_the_latest_period(self):
        filings = filings_from_submissions(_submissions_payload())
        latest = latest_known_filing(filings, "2026-11-08")
        assert latest is not None
        assert latest.period_end == "2026-06-30"
        assert not latest.is_announcement


    def test_malformed_payload_yields_no_filings(self):
        """A broken response is "no evidence", never a fabricated filing."""
        assert filings_from_submissions({}) == []
        assert filings_from_submissions({"filings": {}}) == []
        assert filings_from_submissions({"filings": {"recent": {}}}) == []
        assert filings_from_submissions(None) == []

    def test_rows_without_a_usable_date_are_skipped(self):
        payload = {
            "filings": {"recent": {
                "accessionNumber": ["a", "b"],
                "form": ["8-K", "10-Q"],
                "filingDate": ["", "2026-08-10"],
                "reportDate": ["2026-09-30", "2026-06-30"],
                "acceptanceDateTime": ["", ""],
                "primaryDocument": ["", ""],
            }},
        }
        filings = filings_from_submissions(payload)
        assert len(filings) == 1
        assert filings[0].period_end == "2026-06-30"

    def test_other_fiscal_calendars_are_not_assumed(self):
        """The period comes from the filing's reportDate, not a Dec-31 rule."""
        payload = {
            "filings": {"recent": {
                "accessionNumber": ["acc"],
                "form": ["10-Q"],
                "filingDate": ["2025-01-31"],
                "reportDate": ["2024-12-28"],
                "acceptanceDateTime": ["2025-01-31T16:00:00.000Z"],
                "primaryDocument": ["aapl.htm"],
            }},
        }
        filings = filings_from_submissions(payload)
        assert filings[0].period_end == "2024-12-28"


class TestMergingSources:
    def test_the_release_and_the_xbrl_filing_merge_on_the_period(self):
        """An 8-K release plus a later 10-Q for the same period: one record,
        keeping the earliest date that made the period public."""
        from tradingagents.dataflows.filing_freshness import merge_filings

        release = FilingRecord("2026-Q3", "2026-09-30", filing_date="2026-11-07",
                              form_type="8-K", source_type="sec_filing")
        as_filed = FilingRecord("2026-Q3", "2026-09-30", filing_date="2026-11-14",
                                form_type="10-Q", source_type="sec_filing")
        merged = merge_filings([release, as_filed])
        assert len(merged) == 1
        assert merged[0].filing_date == "2026-11-07"   # earliest publication
        assert merged[0].form_type == "8-K"

    def test_a_period_with_only_one_source_is_kept(self):
        from tradingagents.dataflows.filing_freshness import merge_filings

        q2 = FilingRecord("2026-Q2", "2026-06-30", filing_date="2026-08-10",
                          form_type="10-Q")
        assert merge_filings([q2]) == [q2]


class TestExtractionPrefersSubmissions:
    def test_the_edgar_extractor_uses_the_submission_history(self, monkeypatch):
        """The production entry point merges submissions with companyfacts."""
        from tradingagents.dataflows import filing_freshness as ff
        from tradingagents.dataflows.vendors import sec_edgar

        monkeypatch.setattr(sec_edgar, "cik_for", lambda t: "0001067983")
        monkeypatch.setattr(
            sec_edgar, "_cached_json",
            lambda url, name: (_submissions_payload() if "submissions" in url
                               else {"facts": {"us-gaap": {}}}),
        )
        filings = ff.filings_from_sec_edgar("BRK-B", "2026-11-08")
        # The periodic periods come through, and the 8-K arrives as an
        # announcement with no invented period.
        assert {f.period_end for f in filings if not f.is_announcement} >= {
            "2026-06-30",
        }
        announcements = [f for f in filings if f.is_announcement]
        assert any(a.filing_date == "2026-11-07" for a in announcements)
        # And the announcement makes a Q2 report stale.
        verdict = ff.check_filing_freshness(
            "2026-06-30", filings, "2026-11-08", used_period="2026-Q2",
        )
        assert verdict.status == "STALE_FUNDAMENTALS"

    def test_xbrl_still_supplies_periods_when_submissions_is_empty(
        self, monkeypatch
    ):
        from tradingagents.dataflows import filing_freshness as ff
        from tradingagents.dataflows.vendors import sec_edgar

        monkeypatch.setattr(sec_edgar, "cik_for", lambda t: "0001067983")
        monkeypatch.setattr(
            sec_edgar, "_cached_json",
            lambda url, name: ({} if "submissions" in url else {
                "facts": {"us-gaap": {"Revenues": {"units": {"USD": [
                    {"end": "2026-06-30", "filed": "2026-08-10", "form": "10-Q"},
                ]}}}},
            }),
        )
        filings = ff.filings_from_sec_edgar("BRK-B", "2026-11-08")
        assert "2026-06-30" in {f.period_end for f in filings}

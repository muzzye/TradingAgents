"""Data availability: period publication states as of the analysis date.

Regression fixture: the BRK-B report of 2026-11-08 used Q2 figures while Q3
had already been published (release Saturday 2026-11-07, no SEC filing yet).
The states distinguish a company release from a filed statement — they never
collapse into one boolean.
"""

from tradingagents.dataflows.data_availability import (
    FILING_PENDING,
    SEC_FILING_AVAILABLE,
    UNVERIFIED,
    PeriodAvailability,
    latest_available_period,
    render_availability_block,
)


def _brk_periods() -> list[PeriodAvailability]:
    """The fixture's periods: Q3 released 2026-11-07, Q2 filed earlier."""
    return [
        PeriodAvailability("2026-Q3", "2026-09-30", "2026-11-07", None),
        PeriodAvailability("2026-Q2", "2026-06-30", "2026-08-02", "2026-08-07"),
    ]


class TestPeriodStatus:
    def test_release_without_filing_is_filing_pending(self):
        q3 = _brk_periods()[0]
        assert q3.status("2026-11-08") == FILING_PENDING
        assert q3.is_available("2026-11-08")

    def test_filed_period_is_sec_filing_available(self):
        q2 = _brk_periods()[1]
        assert q2.status("2026-11-08") == SEC_FILING_AVAILABLE

    def test_release_after_as_of_is_unverified(self):
        q3 = _brk_periods()[0]
        # On the Friday before the Saturday release, Q3 is not public.
        assert q3.status("2026-11-06") == UNVERIFIED
        assert not q3.is_available("2026-11-06")

    def test_period_ended_but_no_evidence_is_unverified(self):
        ghost = PeriodAvailability("2026-Q1", "2026-03-31", None, None)
        assert ghost.status("2026-11-08") == UNVERIFIED

    def test_future_period_is_unverified(self):
        future = PeriodAvailability("2026-Q4", "2026-12-31", None, None)
        assert future.status("2026-11-08") == UNVERIFIED

    def test_filing_after_as_of_falls_back_to_release(self):
        # Filed 2026-11-09 (after the run) but released 2026-11-07 (before).
        p = PeriodAvailability("2026-Q3", "2026-09-30", "2026-11-07", "2026-11-09")
        assert p.status("2026-11-08") == FILING_PENDING


class TestLatestAvailable:
    def test_freshest_available_period_is_q3(self):
        latest = latest_available_period(_brk_periods(), "2026-11-08")
        assert latest is not None
        assert latest.period == "2026-Q3"

    def test_before_the_release_only_q2_is_available(self):
        latest = latest_available_period(_brk_periods(), "2026-11-06")
        assert latest is not None
        assert latest.period == "2026-Q2"

    def test_no_available_period_returns_none(self):
        assert latest_available_period(_brk_periods(), "2026-01-01") is None


class TestRenderBlock:
    def test_block_names_states_and_the_freshest_period(self):
        block = render_availability_block(_brk_periods(), "2026-11-08", ticker="BRK-B")
        assert "2026-Q3" in block
        assert FILING_PENDING in block
        assert SEC_FILING_AVAILABLE in block
        assert "2026-Q3" in block.split("freshest available period")[1]

    def test_block_warns_when_nothing_is_available(self):
        block = render_availability_block(_brk_periods(), "2026-01-01")
        assert "unavailable" in block.lower()

    def test_empty_periods_say_so(self):
        block = render_availability_block([], "2026-11-08")
        assert "No dated period" in block

    def test_unverified_period_is_not_presented_as_published(self):
        block = render_availability_block(
            [PeriodAvailability("2026-Q3", "2026-09-30", None, None)], "2026-11-08"
        )
        assert UNVERIFIED in block
        assert "unavailable" in block.lower()

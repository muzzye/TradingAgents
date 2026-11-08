"""Analyst grounding: causal and availability blocks reach the prompts.

The Sentiment Analyst receives the causal guard's verdict as a binding
temporal-consistency section; the Fundamentals Analyst receives the
deterministic data-availability block. Both are prompt-level guidance —
the enforced checks live in cross_agent/gate_integration — but the blocks
themselves are built by deterministic code tested here.
"""

from tradingagents.agents.analysts.fundamentals_analyst import _availability_block
from tradingagents.agents.analysts.sentiment_analyst import _causal_block


class TestSentimentCausalBlock:
    def test_not_yet_observable_says_reaction_is_false(self):
        block = _causal_block({"post_event_status": "NOT_YET_OBSERVABLE",
                               "trade_date": "2026-11-08"})
        assert "NOT YET OBSERVABLE" in block
        assert "2026-11-08" in block
        assert "must not appear" in block

    def test_observable_requires_post_event_prices(self):
        block = _causal_block({"post_event_status": "OBSERVABLE"})
        assert "observable" in block.lower()
        assert "actual post-event price data" in block

    def test_unknown_status_neither_asserts_nor_denies(self):
        block = _causal_block({})
        assert "No causal verdict" in block
        assert "unless your sources show" in block


class TestFundamentalsAvailabilityBlock:
    def test_periods_are_named_with_states(self):
        block = _availability_block({"data_availability": [
            ("2026-Q3", "2026-09-30", "2026-11-07", None),
            ("2026-Q2", "2026-06-30", "2026-08-02", "2026-08-07"),
        ]}, "2026-11-08")
        assert "2026-Q3" in block
        assert "filing_pending" in block
        assert "sec_filing_available" in block
        assert "freshest available period (2026-Q3)" in block

    def test_no_availability_data_says_so(self):
        block = _availability_block({}, "2026-11-08")
        assert "No dated period-availability information" in block
        assert "Do not state that any fiscal period's figures are published" in block

    def test_malformed_rows_are_skipped(self):
        block = _availability_block(
            {"data_availability": [("2026-Q3",), None, "junk"]}, "2026-11-08"
        )
        # The one well-formed (if sparse) row still renders.
        assert "2026-Q3" in block

    def test_empty_list_says_so(self):
        block = _availability_block({"data_availability": []}, "2026-11-08")
        assert "No dated period-availability information" in block

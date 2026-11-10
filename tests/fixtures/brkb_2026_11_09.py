"""A frozen BRK-B run: filings, prices, indicators and a simulated book (§4).

Every figure here is a constant. The point of the fixture is that a test can run
the whole pipeline and compare against expected values that were written down
independently of the code under test — a fixture computed by the code it checks
proves nothing.

Provenance of the numbers
-------------------------
The values come from the SEC companyfacts payload and the market snapshot that
the data-lineage audit examined (`docs/audits/`), which is the same evidence
that established the defects this suite guards against:

- **Q3 2026 consolidated revenue** ``105,647,000,000`` — the
  ``RevenueFromContractWithCustomerExcludingAssessedTax`` *and* ``Revenues``
  concept agree on it. The narrower component the defective extractor picked was
  ``74,322,000,000``.
- **Q3 2025 revenue** ``94,972,000,000``, so the year-over-year change is
  ``(105,647 - 94,972) / 94,972 = +11.24%``.
- **Shareholders' equity** ``800,000,000,000`` at 2026-09-30, with 550,000 class
  A shares (1,500 class B each) and 1,400,000,000 class B shares: 2,225,000,000
  class-B-equivalent shares, so a class-B book value of ``359.55``.
- **Price** ``576.74`` at an intraday instant on 2026-11-09, which is what makes
  the bar non-final: 18:37 Rome is 12:37 in New York, mid-session.
- **ATR(14)** ``9.524`` and **SMA200** ``502.85`` from the same snapshot.

The reported multiple the model wrote was ``1.73x``, and ``0.00116x`` in one
place — the latter is 1.604/1500, the A/B ratio applied backwards. Neither
reproduces from these inputs; ``576.74 / 359.55 = 1.604`` does.

Nothing here is computed by the modules under test. If a constant needs to
change, it is because the real vendor payload changed, not to make a test pass.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tests.fixtures.brk_b_2026_11_08 import BRK_A_TO_B_RATIO, TICKER

# --- SEC filings (point-in-time: known at the analysis date) ----------------

ANALYSIS_DATE = "2026-11-09"

Q3_2026 = {"start": "2026-07-01", "end": "2026-09-30"}
Q3_2025 = {"start": "2025-07-01", "end": "2025-09-30"}

REVENUE_Q3_2026 = 105_647_000_000.0
REVENUE_Q3_2025 = 94_972_000_000.0
# The narrower concept that exists in the same filing, which the defective
# extractor preferred because it was listed first.
REVENUE_Q3_2026_NARROW = 74_322_000_000.0

EQUITY = 800_000_000_000.0
SHARES_CLASS_A = 550_000.0
SHARES_CLASS_B = 1_400_000_000.0
CLASS_A_TO_B_RATIO = float(BRK_A_TO_B_RATIO)
CLASS_B_EQUIVALENT_SHARES = SHARES_CLASS_B + SHARES_CLASS_A * CLASS_A_TO_B_RATIO

# --- EPS, both of its halves -----------------------------------------------
# The report states a trailing EPS and a weighted-average share count. The
# count is *weighted average*, not the period-end figure: using the end-of-
# quarter count instead is a different denominator and produces a different
# EPS, which is why the two are named separately in the snapshot.
EPS_TTM = 39.76                    # the figure the report states
WEIGHTED_AVERAGE_SHARES = 1_437_000_000.0   # the denominator that reconciles it
NET_INCOME_TTM = EPS_TTM * WEIGHTED_AVERAGE_SHARES   # 57,127,920,000 USD

# --- Market snapshot --------------------------------------------------------

PRICE = 576.74
SMA200 = 502.85
SMA50 = 545.10
PERIOD_HIGH = 812.35
ATR = 9.524
PREVIOUS_CLOSE = 573.50
# The session's settled close, distinct from the intraday price above. Both are
# real: 576.74 is where the stock traded at 12:37 New York, 575.25 is where it
# closed. The EOD change is computed from the second, and the audit's +0.76%
# follows from it — (575.25 / 570.89 - 1) * 100.
EOD_CLOSE = 575.25
EOD_PREVIOUS_CLOSE = 570.89

# The instant the snapshot was taken: midday in New York, so the daily candle
# is still forming and its Close is not a close.
SNAPSHOT_INSTANT = "2026-11-09T17:37:00Z"      # 12:37 America/New_York

# --- The simulated book -----------------------------------------------------

CASH = 50_000.0
ACCOUNT_EQUITY = 100_000.0
RISK_PER_TRADE_PCT = 0.01
MAX_SYMBOL_EXPOSURE = 0.05
RISK_BUDGET = ACCOUNT_EQUITY * RISK_PER_TRADE_PCT      # 1,000 USD

# --- Risk policy, stated rather than implied --------------------------------
# The gate reads these; a test that leaves them out runs a different policy
# from the one the audit examined. SLIPPAGE_PCT and COMMISSION_FIXED are
# costs, and they change the authorised quantity, which is why they belong in
# the frozen snapshot rather than in a test's local constant.

SLIPPAGE_PCT = 0.0005          # 5 basis points, the engine's default
COMMISSION_FIXED = 5.0         # currency units per order
COMMISSION_PER_SHARE = 0.0
GAP_ATR = 0.0                  # no additional gap stress beyond the stop
MIN_STOP_ATR = 0.25            # the tightest stop policy tolerates

RISK_POLICY = {
    "equity": ACCOUNT_EQUITY,
    "cash": CASH,
    "risk_budget": RISK_BUDGET,
    "risk_per_trade_pct": RISK_PER_TRADE_PCT,
    "max_symbol_exposure": MAX_SYMBOL_EXPOSURE,
    "slippage_pct": SLIPPAGE_PCT,
    "commission_fixed": COMMISSION_FIXED,
    "commission_per_share": COMMISSION_PER_SHARE,
    "gap_atr": GAP_ATR,
    "min_stop_atr": MIN_STOP_ATR,
}

# --- News, with the timestamps that make a claim checkable -----------------
# Two articles about the same session. The first was written mid-session and
# describes an intraday move; the second is a next-morning summary of the
# close. Only the second may be compared to the certified close-to-close
# change, and the first must not be deleted for disagreeing with it.

NEWS_ARTICLES = (
    {
        "provider": "yahoo",
        "url": "https://example.invalid/brk-midday-drop",
        "published_at": "2026-11-09T17:00:00Z",       # 12:00 New York, session open
        "updated_at": "2026-11-09T17:30:00Z",
        "title": "Berkshire slips in midday trading",
        "stated_pct": -1.01,
        "comparison": "intraday",
        "window_start": "2026-11-09T14:30:00Z",
        "window_end": "2026-11-09T17:00:00Z",
        "session_date": "2026-11-09",
    },
    {
        "provider": "reuters",
        "url": "https://example.invalid/brk-close-summary",
        "published_at": "2026-11-10T13:00:00Z",       # next morning, after the close
        "updated_at": None,
        "title": "Berkshire closes higher",
        "stated_pct": 0.76,
        "comparison": "close_to_close",
        "window_start": None,
        "window_end": None,
        "session_date": "2026-11-09",
    },
)

# The certified close-to-close change the second article is measured against:
# (575.25 / 570.89 - 1) * 100 = +0.7637%.
EXPECTED_EOD_CHANGE_PCT = 0.7637

# The two figures the model states that the engine disproves. Kept named so a
# negative test can assert they never become certified facts.
LLM_CLAIMED_PB = 1.73
LLM_CLAIMED_PB_RATIO_ERROR = 0.00116

# --- Expected values, computed by hand, independently of the code -----------

EXPECTED = {
    # (105,647 - 94,972) / 94,972
    "revenue_growth_pct": 11.2402,
    # 800e9 / 2.225e9
    "book_value_per_share": 359.5506,
    # 576.74 / 359.5506
    "price_to_book": 1.6041,
    # The model's own figures, none of which reproduces.
    "reported_price_to_book": 1.73,
    # The report's "0,00116x". The exact quotient of the A/B ratio applied
    # backwards is 1.6041/1500 = 0.0010694, so even this figure was the model's
    # own arithmetic rather than a computation — which is the finding, not a
    # rounding detail. Recorded as the claim it was.
    "reported_price_to_book_ratio_error": 0.00116,
    "class_ratio_inversion": 1.6041 / CLASS_A_TO_B_RATIO,   # 0.0010694
    # floor(1000 / (576.74 - 555.0)) with a 5% cap: the cap binds.
    "entry": 576.0,
    "stop": 555.0,
    "per_share_risk": 21.0,
    "atr_multiple": 21.0 / ATR,                        # 2.205
    "risk_budget_qty": 47,                             # floor(1000 / 21)
    "exposure_capped_qty": 8,                          # floor(5000 / 576)
    "binding_constraint": "EXPOSURE_CAP",
}

# The trader's plan as the model writes it: labelled, so the extractor reads it
# and never guesses a bare number out of prose.
TRADER_PLAN = (
    f"Entry {EXPECTED['entry']:.2f}, stop {EXPECTED['stop']:.2f}, "
    f"ATR {ATR:.3f}. Position sized against the risk budget."
)

# --- Controlled LLM answers ------------------------------------------------
# These are not real model outputs and no test asserts they are good analysis.
# They exist so a run can complete without a provider: the *figures* a report
# states are what the engine checks, and a fixed narrative makes the
# reproducibility tests compare computations rather than prose.

LLM_ANSWERS = {
    "market_report": (
        "BRK-B traded at 576.74 intraday on 2026-11-09. "
        "The trailing P/B of 1.73x looks rich against book value."
    ),
    "fundamentals_report": (
        "Q3 2026 consolidated revenue came to 105,647 million USD. "
        "Book value per class-B share is 359.55 USD."
    ),
    "news_report": (
        "An article reported a -1.01% move during the session."
    ),
    "research_manager": "**Recommendation**: Overweight on the fundamentals.",
    "trader": TRADER_PLAN,
    "portfolio_manager": "**Rating**: Overweight\nBuy on weakness.",
}



@dataclass(frozen=True)
class BrkbFixture:
    """The whole frozen run, in one immutable object."""

    ticker: str = TICKER
    analysis_date: str = ANALYSIS_DATE
    revenue_q3_2026: float = REVENUE_Q3_2026
    revenue_q3_2025: float = REVENUE_Q3_2025
    revenue_q3_2026_narrow: float = REVENUE_Q3_2026_NARROW
    equity: float = EQUITY
    shares_class_a: float = SHARES_CLASS_A
    shares_class_b: float = SHARES_CLASS_B
    class_a_to_b_ratio: float = CLASS_A_TO_B_RATIO
    price: float = PRICE
    previous_close: float = PREVIOUS_CLOSE
    sma50: float = SMA50
    sma200: float = SMA200
    period_high: float = PERIOD_HIGH
    atr: float = ATR
    snapshot_instant: str = SNAPSHOT_INSTANT
    cash: float = CASH
    account_equity: float = ACCOUNT_EQUITY
    risk_budget: float = RISK_BUDGET
    max_symbol_exposure: float = MAX_SYMBOL_EXPOSURE
    trader_plan: str = TRADER_PLAN
    expected: dict = field(default_factory=lambda: dict(EXPECTED))

    # -- derived shapes the pipeline consumes -------------------------------

    def filings(self) -> list[dict]:
        """The filings known at the analysis date, oldest first."""
        return [
            {
                "form": "10-Q",
                "period_end": Q3_2025["end"],
                "filed": "2025-11-01",
                "accession": "0001067983-25-000042",
            },
            {
                "form": "10-Q",
                "period_end": Q3_2026["end"],
                "filed": "2026-11-01",
                "accession": "0001067983-26-000088",
            },
        ]

    def market_snapshot(self) -> dict:
        """The snapshot a run reads, with the price's provenance (FIX #2).

        The provenance is what tells the engine this is a live quote rather
        than a moving average. Without it the registry refuses to price any
        multiple — the 2026-11-10 defect, where a P/B was computed from the
        SMA50 because nothing recorded what the numerator was.
        """
        return {
            "close": self.price,
            "previous_close": self.previous_close,
            "sma50": self.sma50,
            "sma200": self.sma200,
            "high_52w": self.period_high,
            "currency": "USD",
            # The gate reads ``price_as_of``: an observation stamp under any
            # other key is silently ignored, and the audit then reports no bar
            # verdict at all — which reads exactly like "no bar was used".
            "price_as_of": self.snapshot_instant,
            # FIX #2: the snapshot instant is midday New York, so the bar is
            # provisional — an intraday price, never a close.
            "price_provenance": {
                "symbol": self.ticker,
                "value": self.price,
                "currency": "USD",
                "share_class": "B",
                "kind": "intraday",
                "timestamp": self.snapshot_instant,
                "session_date": self.analysis_date,
                "source": "yfinance",
                "bar_finality": "INTRADAY_PARTIAL",
                "validation_status": "VERIFIED",
            },
        }

    def portfolio(self) -> dict:
        return {
            "cash": self.cash,
            "equity": self.account_equity,
            "currency": "USD",
            "positions": [],
        }

    def run_state(self, **overrides) -> dict:
        """A finished run's state, as the graph would leave it.

        Reports are empty by default: the certified metrics must come from the
        supplied figures, not from prose the model wrote. Overrides let a
        negative test introduce exactly one defect.
        """
        state = {
            "company_of_interest": self.ticker,
            "trade_date": self.analysis_date,
            "market_report": "",
            "sentiment_report": "",
            "news_report": "",
            "fundamentals_report": "",
            "investment_plan": "",
            "trader_investment_plan": self.trader_plan,
            "final_trade_decision": "Buy.",
            "final_rating": "Overweight",
            "verified_market_snapshot": self.market_snapshot(),
            "equity": self.equity,
            # FIX #2 (final review 2): the book value is certified only when its
            # accounting provenance says VERIFIED, exactly as the price is.
            "equity_provenance": {
                "concept": "StockholdersEquity",
                "validation_status": "VERIFIED",
                "period_end": "2026-09-30",
                "accession": "0001067983-26-000042",
                "cik": "0001067983",
                "unit": "USD",
            },
            "equity_period_end": "2026-09-30",
            "shares_class_a": self.shares_class_a,
            "shares_class_b": self.shares_class_b,
            "class_a_to_b_ratio": self.class_a_to_b_ratio,
            "price_class": "B",
            "currency": "USD",
            "revenue_consolidated": self.revenue_q3_2026,
            "revenue_prior": self.revenue_q3_2025,
            "investment_debate_state": {
                "bull_history": "", "bear_history": "",
                "history": "", "current_response": "",
            },
            "risk_debate_state": {
                "aggressive_history": "", "conservative_history": "",
                "neutral_history": "", "history": "",
            },
        }
        state.update(overrides)
        return state


BRKB = BrkbFixture()

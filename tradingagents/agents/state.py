from typing import Annotated

from langgraph.graph import MessagesState
from typing_extensions import TypedDict


# Researcher team state
class InvestDebateState(TypedDict):
    bull_history: Annotated[
        str, "Bullish Conversation history"
    ]
    bear_history: Annotated[
        str, "Bearish Conversation history"
    ]
    history: Annotated[str, "Conversation history"]
    current_response: Annotated[str, "Latest response"]
    count: Annotated[int, "Length of the current conversation"]


# Risk management team state
class RiskDebateState(TypedDict):
    aggressive_history: Annotated[
        str, "Aggressive Agent's Conversation history"
    ]
    conservative_history: Annotated[
        str, "Conservative Agent's Conversation history"
    ]
    neutral_history: Annotated[
        str, "Neutral Agent's Conversation history"
    ]
    history: Annotated[str, "Conversation history"]
    latest_speaker: Annotated[str, "Analyst that spoke last"]
    current_aggressive_response: Annotated[
        str, "Latest response by the aggressive analyst"
    ]
    current_conservative_response: Annotated[
        str, "Latest response by the conservative analyst"
    ]
    current_neutral_response: Annotated[
        str, "Latest response by the neutral analyst"
    ]
    count: Annotated[int, "Length of the current conversation"]


class AgentState(MessagesState):
    company_of_interest: Annotated[str, "Company that we are interested in trading"]
    asset_type: Annotated[str, "Asset type under analysis such as stock or crypto"]
    instrument_context: Annotated[str, "Deterministic ticker identity resolved at run start"]
    trade_date: Annotated[str, "The analysis date; data is served as of it"]

    # research step
    market_report: Annotated[str, "Report from the Market Analyst"]
    sentiment_report: Annotated[str, "Report from the Sentiment Analyst"]
    news_report: Annotated[str, "Report from the News Analyst on company and world news"]
    fundamentals_report: Annotated[str, "Report from the Fundamentals Analyst"]

    # researcher team discussion step
    investment_debate_state: Annotated[
        InvestDebateState, "Current state of the debate on if to invest or not"
    ]
    investment_plan: Annotated[str, "Investment plan from the Research Manager"]

    trader_investment_plan: Annotated[str, "Transaction proposal from the Trader"]

    # risk management team discussion step
    risk_debate_state: Annotated[
        RiskDebateState, "Current state of the debate on evaluating risk"
    ]
    final_trade_decision: Annotated[str, "Final decision from the Portfolio Manager"]
    final_rating: Annotated[str, "The Portfolio Manager's 5-tier rating, or REVIEW when it has none"]
    past_context: Annotated[str, "Memory log context for the Portfolio Manager (same-ticker decisions + cross-ticker lessons), written by the Memory Log step"]
    memory_note: Annotated[str, "What the Memory Log step could not settle or read this run, for the report; empty when all went well"]
    portfolio_context: Annotated[str, "Caller-supplied holdings and cash, rendered at run start; empty when not provided"]
    data_quality_precheck: Annotated[dict, "Deterministic data-quality findings computed before synthesis (freshness, arithmetic); the managers see the notice and the gate reuses the verdicts"]

    # The filing's revenue, published by the Data Quality Check node from the
    # SEC payload. Declared here because `StateGraph(AgentState)` keeps only the
    # keys its schema names: an undeclared key returned by a node is dropped
    # between nodes, so the registry never saw a figure no report mentioned.
    # The verified price the market analyst read. Declared for the same reason
    # the revenue keys are: `StateGraph(AgentState)` keeps only what the schema
    # names, so an undeclared snapshot vanished between nodes and every P/B came
    # out NOT_COMPUTABLE while the report still quoted a multiple (FIX #2).
    verified_market_snapshot: Annotated[dict, "The market snapshot the analyst read: latest OHLCV row, indicators, price provenance and bar finality"]

    # Accounting denominators from SEC filings (equity attributable, share counts,
    # ratio and period end). Declared so the pricing path stays certified.
    equity: Annotated[float | None, "Shareholders' equity attributable to common shareholders, from the filing"]
    equity_provenance: Annotated[dict | None, "Where equity and the book value came from: concept, accession, period, CIK, unit, validation status"]
    equity_period_end: Annotated[str | None, "Period end of the equity figure (e.g., 2026-09-30)"]
    shares_class_a: Annotated[float | None, "Class A share count"]
    shares_class_b: Annotated[float | None, "Class B share count"]
    class_a_to_b_ratio: Annotated[float | None, "Verified conversion ratio from A to B"]
    book_value_per_share: Annotated[float | None, "Book value per share, basis match with price class"]

    revenue_consolidated: Annotated[float, "Consolidated revenue the filing states, in base units, as of the period end below"]
    revenue_prior: Annotated[float, "The same quarter a year earlier, when the filing reports it"]
    revenue_provenance: Annotated[dict, "Where revenue_consolidated came from: concept, CIK, accession, period, unit, scale, validation status"]

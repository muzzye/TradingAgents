"""The qualified market price a valuation may use (FIX #2).

`build_verified_market_snapshot` renders a Markdown table *for the analyst to
read*. Nothing turned that snapshot back into a structured value, so
`registry_inputs_from_state` always found `price=None` and every P/B came out
`NOT_COMPUTABLE` — while the report still quoted a multiple, computed by the
model from whatever number it liked. The 2026-11-10 BRK-B report did exactly
that: 1.49x, which is the SMA50 divided by book value.

This module produces the structured half: a price with enough provenance to
say what it is, when it was observed, and whether it may price a valuation.
The trajectory is deliberately narrow — one row of already-verified OHLCV, the
finality of its bar, and a classification taken from that finality rather than
from a caller's opinion.
"""

from __future__ import annotations


def qualified_price(
    symbol: str,
    price: float | None,
    *,
    bar_finality: str | None = None,
    session_date: str | None = None,
    observation_timestamp: str | None = None,
    source: str = "yfinance",
    currency: str | None = None,
    share_class: str | None = None,
) -> dict:
    """A price with the provenance a valuation needs.

    ``kind`` is derived from the bar's finality, never asserted:

    - ``EOD_CONFIRMED``      → ``eod_close``: a settled close may price an EOD
      multiple.
    - ``INTRADAY_PARTIAL``   → ``intraday``: a live price, usable only with its
      timestamp and never as a close.
    - anything else          → ``unqualified``: unknown, stale or a non-trading
      day. No valuation may rest on it.

    ``validation_status`` follows: a settled close and a timestamped intraday
    price are ``VERIFIED``; everything else is ``UNVERIFIED`` so the registry
    refuses it rather than certifying a guess.
    """
    finality = (bar_finality or "").upper()
    if finality == "EOD_CONFIRMED":
        kind, status = "eod_close", "VERIFIED"
    elif finality == "INTRADAY_PARTIAL":
        kind, status = "intraday", "VERIFIED"
    else:
        kind, status = "unqualified", "UNVERIFIED"

    return {
        "symbol": symbol,
        "value": price,
        "currency": currency,
        "share_class": share_class,
        "kind": kind,
        "timestamp": observation_timestamp,
        "session_date": session_date,
        "source": source,
        "bar_finality": finality or None,
        "validation_status": status,
    }


def price_from_snapshot(snapshot: dict | None, symbol: str = "",
                        *, share_class: str | None = None) -> dict:
    """The provenance for a snapshot dict that already carries a price.

    A snapshot written by `qualified_price` passes through unchanged. A bare
    ``{"close": 575.25}`` — the shape the tests and earlier callers use — is
    treated as a real quote with no finality information, which is the
    assumption every caller before FIX #2 made implicitly and which this makes
    explicit in one place.
    """
    if not isinstance(snapshot, dict):
        return qualified_price(symbol, None, share_class=share_class)

    existing = snapshot.get("price_provenance")
    if isinstance(existing, dict):
        return existing

    value = snapshot.get("close")
    if value is None:
        value = snapshot.get("price")
    if value is None:
        return qualified_price(symbol, None, share_class=share_class)

    return {
        "symbol": symbol,
        "value": value,
        "currency": snapshot.get("currency"),
        "share_class": share_class or snapshot.get("price_class"),
        "kind": "market_price",
        "timestamp": snapshot.get("price_as_of"),
        "session_date": snapshot.get("session_date"),
        "source": snapshot.get("source") or "verified_market_snapshot",
        "bar_finality": snapshot.get("bar_finality"),
        "validation_status": "VERIFIED",
    }


def indicator_as_price(indicator: str, value: float | None,
                       symbol: str = "") -> dict:
    """Provenance for a figure a caller mistakenly offers as the price.

    Exists so a caller that has only an indicator can say so honestly instead of
    passing it as ``close``: the registry then refuses the multiple and the
    audit records *why*, rather than certifying a moving average as a quote.
    """
    return {
        "symbol": symbol,
        "value": value,
        "kind": "indicator",
        "indicator": indicator,
        "validation_status": "NOT_A_PRICE",
        "source": "technical_indicator",
    }

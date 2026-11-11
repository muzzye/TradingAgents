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

from datetime import datetime


def _parse_instant(value) -> datetime | None:
    """A timezone-aware instant, or None. A naive stamp cannot be placed."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


_US_EASTERN = "America/New_York"


def _eod_stamp_settles(timestamp, session_date) -> bool:
    """Whether an `EOD_CONFIRMED` stamp can actually be a settled close.

    Three conditions, all checked against the exchange calendar rather than
    assumed:

    - the stamp parses as a timezone-aware instant;
    - its New York calendar day is a trading day with a session;
    - it is at or after that session's close (13:00 on an early-close day,
      16:00 otherwise), so the bar it describes has finished forming.

    ``session_date``, when given, must match the stamp's New York day: a bar
    cannot close a session it is not part of.

    When the stamp is absent the session date still decides. A daily bar is
    settled *for its session*, which is a claim about the date, not about the
    instant — so an EOD bar dated Thanksgiving, a Saturday or a year the
    calendar does not describe names a session that never happened. Only a
    trading day that the calendar knows supports the certification without an
    instant; with no session date either, nothing identifies the session and
    the claim cannot be verified.
    """
    from tradingagents.dataflows.math.exchange_calendar import session_day

    if not timestamp:
        if not session_date:
            return False
        # Validate the date itself: it must be a session the calendar knows.
        declared = session_day(str(session_date)[:10])
        return bool(declared.trades)

    instant = _parse_instant(timestamp)
    if instant is None:
        return False
    from zoneinfo import ZoneInfo

    local = instant.astimezone(ZoneInfo(_US_EASTERN))
    day = session_day(local.date().isoformat())
    if not day.trades or day.closes is None:
        return False
    if session_date and str(session_date)[:10] != local.date().isoformat():
        return False
    return local.time() >= day.closes


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
        kind = "eod_close"
        # FIX #5 final (A): an EOD close is a claim about *time* — that the
        # session has finished and this is its settled print. The calendar can
        # refute it: a 13:00 New York stamp is inside the session, a holiday
        # has no close at all, and a stamp on another day closes nothing. A
        # contradiction is UNVERIFIED, so no valuation and no trigger can rest
        # on it.
        status = ("VERIFIED" if _eod_stamp_settles(
            observation_timestamp, session_date) else "UNVERIFIED")
    elif finality == "INTRADAY_PARTIAL":
        # An intraday price is only usable with the moment it was observed:
        # without a stamp there is no way to tell a mid-session print from a
        # stale quote, so it is not verified.
        kind = "intraday"
        status = "VERIFIED" if observation_timestamp else "UNVERIFIED"
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
        "validation_status": snapshot.get("validation_status"),
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

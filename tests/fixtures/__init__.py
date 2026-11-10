"""Frozen regression fixtures for the reliability contract."""

# FIX #2 (finding 2): a price needs provenance before the registry will certify
# a multiple from it. Tests that build `RunInputs` directly say what the price
# is, exactly as the state path does — no lenient branch exists for them.
def verified_price(value: float, *, kind: str = "eod_close",
                   timestamp: str | None = "2026-11-09T21:00:00+00:00",
                   session_date: str | None = "2026-11-09",
                   share_class: str = "B", currency: str = "USD") -> dict:
    """The provenance a test's price carries, in the shape production writes."""
    return {
        "kind": kind,
        "value": value,
        "currency": currency,
        "share_class": share_class,
        "timestamp": timestamp,
        "session_date": session_date,
        "source": "yfinance",
        "bar_finality": "EOD_CONFIRMED" if kind == "eod_close" else "INTRADAY_PARTIAL",
        "validation_status": "VERIFIED",
    }

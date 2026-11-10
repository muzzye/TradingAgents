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


# FIX #2 (final review 2): a book value needs its own verified provenance. The
# registry certifies a P/B only when the accounting inputs say VERIFIED — the
# same standard the price has carried since the first commit of this fix.
def verified_accounting(*, concept: str = "StockholdersEquity",
                        period_end: str = "2026-09-30",
                        accession: str = "0001067983-26-000042",
                        cik: str = "0001067983") -> dict:
    """The provenance a test's book value carries, in the shape production writes."""
    return {
        "concept": concept,
        "validation_status": "VERIFIED",
        "period_end": period_end,
        "accession": accession,
        "cik": cik,
        "unit": "USD",
    }

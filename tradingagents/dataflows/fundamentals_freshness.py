"""Fundamentals freshness gate (P0 FRESH-001).

A fundamentals report is only as current as the latest authoritative filing
known at the analysis date — not the latest record a vendor happens to
serve. The BRK-B report of 2026-11-08 leaned on Q2 (period ended 2026-06-30)
while Q3 (period ended 2026-09-30) had already been published on 2026-11-07:
an older quarter presented as the current one.

The gate answers one question deterministically, for any ticker: given the
filings known at ``analysis_as_of`` and the period the analyst actually
used, is the used period the freshest authoritative one?

- ``CURRENT`` — the used period matches the latest known filing.
- ``STALE_CRITICAL`` — the used period is older than the latest known
  filing: the report's "current" fundamentals are not current.
- ``UNAVAILABLE`` — no dated filing is known at the analysis date (or the
  used period is unknown): nothing authoritative to compare against, so a
  decision relying on fundamentals cannot be called verified.

No look-ahead: a filing dated after ``as_of_date`` was not known then and
never counts. A company release and a SEC filing are different sources with
different dates; either being known makes the period known, and the gate
reports which.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

# Verdict statuses, stable for the audit trail.
CURRENT = "CURRENT"
STALE_CRITICAL = "STALE_CRITICAL"
UNAVAILABLE = "UNAVAILABLE"

# Reason codes the Execution Gate and the audit carry verbatim.
FUNDAMENTALS_CURRENT = "FUNDAMENTALS_CURRENT"
FUNDAMENTALS_STALE = "FUNDAMENTALS_STALE"
FUNDAMENTALS_UNAVAILABLE = "FUNDAMENTALS_UNAVAILABLE"


def _parse(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value).strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


@dataclass(frozen=True)
class FilingRecord:
    """One authoritative publication of a fiscal period's figures.

    ``published_date`` is the company-release date; ``filing_date`` the SEC
    filing date. They are different sources with different dates and either
    being known makes the period known. A record with neither date is not
    evidence of anything and never counts as known.
    """

    period: str                       # "2026-Q3" or the period end as label
    period_end: str                   # YYYY-MM-DD
    published_date: str | None = None  # company release, YYYY-MM-DD
    filing_date: str | None = None     # SEC filing, YYYY-MM-DD
    form: str | None = None            # "10-Q", "10-K", "8-K"…
    source_type: str = "company_release"

    def known_at(self, as_of_date: str) -> bool:
        """Whether this record was public by ``as_of_date`` (no look-ahead)."""
        as_of = _parse(as_of_date)
        if as_of is None:
            return False
        for candidate in (self.filing_date, self.published_date):
            parsed = _parse(candidate)
            if parsed is not None and parsed <= as_of:
                return True
        return False


def latest_known_filing(
    filings: list[FilingRecord], as_of_date: str
) -> FilingRecord | None:
    """The most recent filing known at ``as_of_date``, or None.

    "Most recent" is by period end, then by the date that made it known.
    Filings dated after the analysis date are invisible here — that is the
    whole point: a backtest must not see a filing the market had not seen.
    """
    known = [f for f in filings if f.known_at(as_of_date)]
    if not known:
        return None
    return max(
        known,
        key=lambda f: (f.period_end, f.filing_date or f.published_date or ""),
    )


@dataclass(frozen=True)
class FreshnessVerdict:
    """Whether the fundamentals the analyst used are the freshest known."""

    status: str
    reason_code: str
    latest_known_period: str | None
    latest_known_period_end: str | None
    used_period: str | None
    reason: str

    @property
    def blocks_decision(self) -> bool:
        """Stale or unavailable fundamentals must not feed an approval."""
        return self.status in (STALE_CRITICAL, UNAVAILABLE)

    def summary(self) -> dict:
        return {
            "status": self.status,
            "reason_code": self.reason_code,
            "latest_known_period": self.latest_known_period,
            "latest_known_period_end": self.latest_known_period_end,
            "used_period": self.used_period,
            "reason": self.reason,
        }


def check_fundamentals_freshness(
    used_period_end: str | None,
    filings: list[FilingRecord],
    as_of_date: str,
    *,
    used_period: str | None = None,
) -> FreshnessVerdict:
    """Compare the period the analyst used against the latest known filing.

    ``used_period_end`` is the period end the fundamentals report actually
    leaned on (YYYY-MM-DD), ``filings`` the authoritative records known to
    the run, ``as_of_date`` the analysis date. Pure function: no network,
    no LLM, no per-ticker rules.
    """
    latest = latest_known_filing(filings, as_of_date)
    used_end = _parse(used_period_end)

    if latest is None:
        return FreshnessVerdict(
            status=UNAVAILABLE,
            reason_code=FUNDAMENTALS_UNAVAILABLE,
            latest_known_period=None,
            latest_known_period_end=None,
            used_period=used_period,
            reason=(
                f"no authoritative filing is known as of {as_of_date}: "
                "fundamentals cannot be called current or verified"
            ),
        )

    if used_end is None:
        return FreshnessVerdict(
            status=UNAVAILABLE,
            reason_code=FUNDAMENTALS_UNAVAILABLE,
            latest_known_period=latest.period,
            latest_known_period_end=latest.period_end,
            used_period=used_period,
            reason=(
                "the period the fundamentals report used is unknown or "
                "undated, so its freshness cannot be checked"
            ),
        )

    latest_end = _parse(latest.period_end)
    if latest_end is not None and used_end >= latest_end:
        return FreshnessVerdict(
            status=CURRENT,
            reason_code=FUNDAMENTALS_CURRENT,
            latest_known_period=latest.period,
            latest_known_period_end=latest.period_end,
            used_period=used_period,
            reason=(
                f"the used period ({used_end:%Y-%m-%d}) matches the latest "
                f"filing known at {as_of_date} ({latest.period}, ended "
                f"{latest.period_end})"
            ),
        )

    return FreshnessVerdict(
        status=STALE_CRITICAL,
        reason_code=FUNDAMENTALS_STALE,
        latest_known_period=latest.period,
        latest_known_period_end=latest.period_end,
        used_period=used_period,
        reason=(
            f"the report uses the period ended {used_end:%Y-%m-%d} but a "
            f"later period ({latest.period}, ended {latest.period_end}) was "
            f"already known at {as_of_date}: an older quarter must not be "
            "presented as the current one"
        ),
    )


def filings_from_sec_edgar(ticker: str, as_of_date: str) -> list[FilingRecord]:
    """Filing records from SEC EDGAR, as known at ``as_of_date`` (no look-ahead).

    Reads the filer's cached companyfacts and keeps, per fiscal period end,
    the earliest filing that reported it. A period whose every filing is
    dated after ``as_of_date`` was not known then and is excluded. A ticker
    that is not a US filer, or an unreachable SEC, yields an empty list:
    the caller reports UNAVAILABLE rather than inventing a filing.
    """
    from tradingagents.dataflows.vendors import sec_edgar

    try:
        cik = sec_edgar.cik_for(ticker)
        if cik is None:
            return []
        facts = sec_edgar._cached_json(
            sec_edgar._FACTS_URL.format(cik=cik), f"CIK{cik}.json"
        )
    except Exception:  # noqa: BLE001 — an unreachable SEC is UNAVAILABLE, not a crash
        return []

    us_gaap = (facts.get("facts") or {}).get("us-gaap") or {}
    earliest: dict[str, dict] = {}   # period end -> earliest filing metadata
    for tag_data in us_gaap.values():
        for unit_values in (tag_data.get("units") or {}).values():
            for fact in unit_values:
                end, filed = fact.get("end"), fact.get("filed")
                if not end or not filed or str(filed) > as_of_date:
                    continue
                seen = earliest.get(end)
                if seen is None or str(filed) < seen["filed"]:
                    earliest[end] = {"filed": str(filed), "form": fact.get("form")}
    return [
        FilingRecord(
            period=end,
            period_end=end,
            published_date=None,
            filing_date=meta["filed"],
            form=meta["form"],
            source_type="sec_filing",
        )
        for end, meta in sorted(earliest.items())
    ]

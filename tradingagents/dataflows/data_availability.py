"""Data availability: which periods are published, filed, or still pending.

The BRK-B report's Fundamentals Analyst leaned on Q2 figures while Q3 had
already been published — not because Q3 was unavailable, but because nothing
told the analyst which periods were usable as of the run date. This module
answers that question deterministically from the run's own knowledge:

- **company_release_available** — the company has published results for the
  period (a release date is known and is on or before the analysis date).
- **sec_filing_available** — a filing for the period is on file by the
  analysis date (SEC EDGAR's own ``filed`` dates).
- **filing_pending** — the release exists but no filing is on file yet: the
  figures are public but the filed statement is not.
- **unverified** — no dated evidence either way; the period's status is
  unknown and must not be presented as available.

A release and a filing are different sources with different dates; the two
states never collapse into one boolean (contract §3.2). The block this module
renders is injected into the Fundamentals Analyst's prompt so the model
reports on the periods it actually has, and the freshest *available* period
is named explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass

# The contract's availability states (§3.2), stable for the audit trail.
COMPANY_RELEASE_AVAILABLE = "company_release_available"
SEC_FILING_AVAILABLE = "sec_filing_available"
FILING_PENDING = "filing_pending"
UNVERIFIED = "unverified"

STATES = (COMPANY_RELEASE_AVAILABLE, SEC_FILING_AVAILABLE, FILING_PENDING, UNVERIFIED)


@dataclass(frozen=True)
class PeriodAvailability:
    """One fiscal period's publication status as of the analysis date."""

    period: str                      # e.g. "2026-Q3"
    period_end: str | None           # YYYY-MM-DD
    release_date: str | None         # company release, YYYY-MM-DD or None
    filing_date: str | None          # SEC filing, YYYY-MM-DD or None

    def status(self, as_of_date: str) -> str:
        """The availability state, given the run's analysis date.

        A date on or before ``as_of_date`` counts as available; a later one
        (or an unknown one) does not. A release without a filing is
        ``filing_pending`` — public, but the filed statement is not there.
        """
        if self.filing_date and self.filing_date <= as_of_date:
            return SEC_FILING_AVAILABLE
        if self.release_date and self.release_date <= as_of_date:
            return FILING_PENDING
        if self.period_end and self.period_end <= as_of_date:
            # The period has ended but nothing dated says it is public.
            return UNVERIFIED
        return UNVERIFIED

    def is_available(self, as_of_date: str) -> bool:
        """Whether figures for this period may be used at all (any dated source)."""
        return self.status(as_of_date) in (SEC_FILING_AVAILABLE, FILING_PENDING)


def _is_available(period: PeriodAvailability, as_of_date: str) -> bool:
    """Whether the period has any dated public evidence by ``as_of_date``."""
    return period.status(as_of_date) in (SEC_FILING_AVAILABLE, FILING_PENDING)


def latest_available_period(
    periods: list[PeriodAvailability], as_of_date: str
) -> PeriodAvailability | None:
    """The freshest period with dated public evidence by ``as_of_date``.

    Periods are compared by ``period_end`` (None sorts last); ties keep the
    first. None when no period is available — the caller must say so rather
    than letting the analyst pick an older period silently.
    """
    available = [p for p in periods if _is_available(p, as_of_date)]
    if not available:
        return None
    return max(available, key=lambda p: (p.period_end or "",))


def render_availability_block(
    periods: list[PeriodAvailability],
    as_of_date: str,
    *,
    ticker: str = "",
) -> str:
    """The prompt block telling the analyst which periods are usable.

    Names every period with its state, then states the rule: use the
    freshest available period; a period whose status is ``unverified`` must
    not be presented as published.
    """
    lines = ["## Data availability as of " + as_of_date]
    if ticker:
        lines[0] += f" — {ticker}"
    lines.append("")
    if not periods:
        lines.append(
            "No dated period information is available for this run. Do not "
            "claim any fiscal period's figures are published or filed."
        )
        return "\n".join(lines)
    for p in sorted(periods, key=lambda p: (p.period_end or "",), reverse=True):
        state = p.status(as_of_date)
        detail = []
        if p.release_date:
            detail.append(f"company release {p.release_date}")
        if p.filing_date:
            detail.append(f"SEC filing {p.filing_date}")
        suffix = f" ({'; '.join(detail)})" if detail else ""
        lines.append(f"- {p.period} (ends {p.period_end or 'unknown'}): **{state}**{suffix}")
    latest = latest_available_period(periods, as_of_date)
    lines += [
        "",
        (
            f"Use the freshest available period ({latest.period}) for current-"
            "period claims. A period marked unverified has no dated evidence "
            "of publication: do not present its figures as published, and do "
            "not substitute an older period without saying so explicitly."
            if latest else
            "No period has dated public evidence as of this date: report "
            "fundamentals as unavailable rather than citing an older period "
            "as if it were current."
        ),
    ]
    return "\n".join(lines)

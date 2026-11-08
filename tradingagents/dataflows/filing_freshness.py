"""SEC filing freshness gate (P0.1).

A fundamentals report is only as current as the latest authoritative filing
known at the analysis date — not the latest record a vendor happens to
serve. The BRK-B report of 2026-11-08 leaned on Q2 (period ended
2026-06-30) while the Q3 filing (period ended 2026-09-30) had already been
published on 2026-11-07: an older quarter presented as the current one.

The gate answers one question deterministically, for any ticker and any
fiscal calendar: given the filings known at ``analysis_as_of`` and the
period the analyst actually used, is the used period the freshest
authoritative one?

- ``CURRENT`` — the used period matches the latest known filing.
- ``STALE_FUNDAMENTALS`` — the used period is older than the latest known
  filing: the report's "current" fundamentals are not current.
- ``FUNDAMENTALS_UNAVAILABLE`` — no dated filing is known at the analysis
  date (or the used period is unknown): nothing authoritative to compare
  against, so a decision relying on fundamentals cannot be called verified.

No look-ahead: a filing dated after ``as_of_date`` was not known then and
never counts — that is what keeps backtests honest. A company release and
a SEC filing are different sources with different dates; either being
known makes the period known, and the verdict reports which.

Every record carries the fields the audit needs: ``filing_date``,
``accepted_at``, ``period_end``, ``form_type``, ``accession_number``,
source and retrieval timestamp. Publication date and accounting period
are distinct and never conflated.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date, datetime

# Verdict statuses, stable for the audit trail and the Execution Gate.
CURRENT = "CURRENT"
STALE_FUNDAMENTALS = "STALE_FUNDAMENTALS"
FUNDAMENTALS_UNAVAILABLE = "FUNDAMENTALS_UNAVAILABLE"

# Reason codes the Execution Gate and the audit carry verbatim.
FUNDAMENTALS_CURRENT = "FUNDAMENTALS_CURRENT"
FUNDAMENTALS_STALE = "FUNDAMENTALS_STALE"
FUNDAMENTALS_UNAVAILABLE = "FUNDAMENTALS_UNAVAILABLE"

# An announcement only proves a *newer* period is public when it cannot be
# the used period's own results release. The hard constraint: nobody can
# announce a period's results before that period has ended. So an
# announcement dated after the end of the quarter following ``used_end``
# must concern a later period. A shorter assumption (quarter, not year) is
# the conservative choice: it flags staleness only on evidence that is
# logically unambiguous, whatever the filer's fiscal calendar.
SUCCESSOR_PERIOD_MONTHS = 3
# Forms that report a fiscal period: their ``reportDate`` is the period end.
PERIODIC_FORMS = frozenset({
    "10-Q", "10-K", "10-K/A", "10-Q/A", "20-F", "20-F/A", "40-F", "40-F/A",
    "6-K", "8-K12B",
})
# Forms that may announce results without stating a fiscal period.
ANNOUNCEMENT_FORMS = frozenset({"8-K", "8-K/A"})


def _parse(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value).strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _add_months(day: date, months: int) -> date | None:
    """Add calendar months to a date, clamping to the last valid day.

    Used to compute the end of the quarter following a period end without
    assuming the filer's fiscal calendar: 2026-06-30 + 3 months is
    2026-09-30, so an announcement after that date cannot be Q2's own
    release. ``2026-01-31 + 1`` clamps to ``2026-02-28``.
    """
    if months == 0:
        return day
    month_index = day.month - 1 + months
    year = day.year + month_index // 12
    month = month_index % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    try:
        return date(year, month, min(day.day, last_day))
    except ValueError:  # pragma: no cover - defensive, year out of range
        return None


@dataclass(frozen=True)
class FilingRecord:
    """One authoritative publication of a fiscal period's figures.

    ``published_date`` is the company-release date; ``filing_date`` the SEC
    filing date; ``accepted_at`` the EDGAR acceptance timestamp when known.
    They are different sources with different dates and either being known
    makes the period known. A record with neither date is not evidence of
    anything and never counts as known. ``accession_number`` ties the
    record to the exact filing; ``retrieved_at`` records when this system
    fetched it (a retrieval stamp, not a publication claim).
    """

    period: str                       # "2026-Q3", "2025-FY"…
    period_end: str | None            # YYYY-MM-DD: the accounting period end
    published_date: str | None = None  # company release, YYYY-MM-DD
    filing_date: str | None = None     # SEC filing, YYYY-MM-DD
    accepted_at: str | None = None     # EDGAR acceptance, ISO-8601 when known
    form_type: str | None = None       # "10-Q", "10-K", "8-K"…
    accession_number: str | None = None
    source_type: str = "company_release"
    source_url: str | None = None
    retrieved_at: str | None = None
    extra: dict = field(default_factory=dict)

    @property
    def is_announcement(self) -> bool:
        """A results announcement that names no fiscal period end.

        An 8-K's ``reportDate`` is the event date, not a period end. Such a
        record proves a period was announced without claiming to know which
        one — enough to make an older period stale, never enough to name the
        newest period.
        """
        return self.period_end is None and bool(self.filing_date or self.published_date)

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

    def summary(self) -> dict:
        """Machine-readable provenance for the audit trail."""
        return {
            "period": self.period,
            "period_end": self.period_end,
            "published_date": self.published_date,
            "filing_date": self.filing_date,
            "accepted_at": self.accepted_at,
            "form_type": self.form_type,
            "accession_number": self.accession_number,
            "source_type": self.source_type,
            "source_url": self.source_url,
        }


def latest_known_filing(
    filings: list[FilingRecord], as_of_date: str
) -> FilingRecord | None:
    """The most recent **periodic** filing known at ``as_of_date``, or None.

    "Most recent" is by period end, then by the date that made it known.
    Filings dated after the analysis date are invisible here — that is the
    whole point: a backtest must not see a filing the market had not seen.

    Announcements are excluded: an 8-K names no fiscal period, so it can
    never be "the latest period". It still matters — see
    :func:`latest_known_announcement` and the freshness check, which uses it
    to recognise that a newer period has been published.
    """
    known = [f for f in filings if f.known_at(as_of_date) and not f.is_announcement]
    if not known:
        return None
    return max(
        known,
        key=lambda f: (f.period_end, f.filing_date or f.published_date or ""),
    )


def latest_known_announcement(
    filings: list[FilingRecord], as_of_date: str
) -> FilingRecord | None:
    """The most recent results announcement known at ``as_of_date``, or None."""
    known = [f for f in filings if f.known_at(as_of_date) and f.is_announcement]
    if not known:
        return None
    return max(known, key=lambda f: f.filing_date or f.published_date or "")


@dataclass(frozen=True)
class FreshnessVerdict:
    """Whether the fundamentals the analyst used are the freshest known."""

    status: str
    reason_code: str
    latest_known_period: str | None
    latest_known_period_end: str | None
    used_period: str | None
    used_period_end: str | None
    reason: str
    latest_filing: FilingRecord | None = None

    @property
    def blocks_decision(self) -> bool:
        """Stale or unavailable fundamentals must not feed an approval."""
        return self.status in (STALE_FUNDAMENTALS, FUNDAMENTALS_UNAVAILABLE)

    def summary(self) -> dict:
        return {
            "status": self.status,
            "reason_code": self.reason_code,
            "latest_known_period": self.latest_known_period,
            "latest_known_period_end": self.latest_known_period_end,
            "used_period": self.used_period,
            "used_period_end": self.used_period_end,
            "reason": self.reason,
            "latest_filing": (
                self.latest_filing.summary() if self.latest_filing else None
            ),
        }


def check_filing_freshness(
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
    announcement = latest_known_announcement(filings, as_of_date)
    used_end = _parse(used_period_end)

    # A results announcement dated after the period the report used proves a
    # newer period is public, even when the announcement names no fiscal
    # period end (an 8-K's reportDate is the event date). The older period is
    # stale on that evidence alone — no invented period is needed.
    #
    # The margin matters. A company releases a quarter one to two months
    # after it ends, so an announcement shortly after ``used_end`` is usually
    # that very period's own release and proves nothing about a successor.
    # Berkshire's 2026-08-11 8-K is precisely the Q2 release, and reading it
    # as staleness was a false positive found on the real filings. Staleness
    # is therefore flagged only when the announcement arrives after the end
    # of the *following* quarter — a date on which it can no longer be the
    # used period's own release.
    if announcement is not None and used_end is not None:
        announced_on = _parse(announcement.filing_date or announcement.published_date)
        successor_end = _add_months(used_end, SUCCESSOR_PERIOD_MONTHS)
        if (
            announced_on is not None
            and successor_end is not None
            and announced_on > successor_end
        ):
            newer_period_known = (
                latest is not None and latest.period_end is not None
                and _parse(latest.period_end) is not None
                and _parse(latest.period_end) > used_end
            )
            return FreshnessVerdict(
                status=STALE_FUNDAMENTALS,
                reason_code=FUNDAMENTALS_STALE,
                latest_known_period=latest.period if newer_period_known else None,
                latest_known_period_end=(
                    latest.period_end if newer_period_known else None
                ),
                used_period=used_period,
                used_period_end=f"{used_end:%Y-%m-%d}",
                reason=(
                    f"the report uses the period ended {used_end:%Y-%m-%d} "
                    f"but results for a later period were announced on "
                    f"{announced_on:%Y-%m-%d} "
                    f"({announcement.form_type} {announcement.accession_number or ''}"
                    f"). An older period must not be presented as current."
                ).strip(),
                latest_filing=latest if newer_period_known else None,
            )

    if latest is None:
        return FreshnessVerdict(
            status=FUNDAMENTALS_UNAVAILABLE,
            reason_code=FUNDAMENTALS_UNAVAILABLE,
            latest_known_period=None,
            latest_known_period_end=None,
            used_period=used_period,
            used_period_end=used_period_end,
            reason=(
                f"no authoritative filing is known as of {as_of_date}: "
                "fundamentals cannot be called current or verified"
            ),
        )

    if used_end is None:
        return FreshnessVerdict(
            status=FUNDAMENTALS_UNAVAILABLE,
            reason_code=FUNDAMENTALS_UNAVAILABLE,
            latest_known_period=latest.period,
            latest_known_period_end=latest.period_end,
            used_period=used_period,
            used_period_end=used_period_end,
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
            used_period_end=f"{used_end:%Y-%m-%d}",
            reason=(
                f"the used period ({used_end:%Y-%m-%d}) matches the latest "
                f"filing known at {as_of_date} ({latest.period}, ended "
                f"{latest.period_end})"
            ),
            latest_filing=latest,
        )

    return FreshnessVerdict(
        status=STALE_FUNDAMENTALS,
        reason_code=FUNDAMENTALS_STALE,
        latest_known_period=latest.period,
        latest_known_period_end=latest.period_end,
        used_period=used_period,
        used_period_end=f"{used_end:%Y-%m-%d}",
        reason=(
            f"the report uses the period ended {used_end:%Y-%m-%d} but a "
            f"later period ({latest.period}, ended {latest.period_end}) was "
            f"already known at {as_of_date}: an older quarter must not be "
            "presented as the current one"
        ),
        latest_filing=latest,
    )


# A period end appears in vendor output as an ISO date (2026-06-30) or as
# a quarter label (2026-Q2, Q2 2026). The regexes are structural: any
# ticker's report, no per-ticker rules.
_ISO_PERIOD_END = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_QUARTER_LABEL = re.compile(r"\b(\d{4})-Q([1-4])\b|\bQ([1-4])\s+(\d{4})\b")


def _quarter_end(year: int, quarter: int) -> str:
    """The calendar quarter end for a (year, quarter) pair."""
    end_month = {1: 3, 2: 6, 3: 9, 4: 12}[quarter]
    end_day = {3: 31, 6: 30, 9: 30, 12: 31}[end_month]
    return f"{year:04d}-{end_month:02d}-{end_day:02d}"


def used_period_end_from_report(report: str | None) -> str | None:
    """The period end a fundamentals report actually leaned on, or None.

    Reads the report text the run itself produced: the latest ISO date or
    quarter label it names as a period. Deterministic and conservative —
    an undatable report returns None, which the freshness gate treats as
    FUNDAMENTALS_UNAVAILABLE rather than guessing a period.
    """
    if not report:
        return None
    candidates: list[str] = []
    for match in _ISO_PERIOD_END.finditer(report):
        candidates.append(match.group(1))
    for match in _QUARTER_LABEL.finditer(report):
        if match.group(2):
            candidates.append(_quarter_end(int(match.group(1)), int(match.group(2))))
        else:
            candidates.append(_quarter_end(int(match.group(4)), int(match.group(3))))
    if not candidates:
        return None
    # The freshest period named is the one the report presents as current.
    return max(candidates)


def filings_from_submissions(payload: dict | None) -> list[FilingRecord]:
    """Filing records from SEC ``/submissions/CIK##########.json``.

    Two kinds of filing matter to the freshness gate, and they carry dates
    that mean different things:

    - **Periodic reports** (10-Q, 10-K, 20-F, 40-F…) — their ``reportDate``
      *is* the fiscal period end. These become ``FilingRecord``s with a
      ``period_end``.
    - **Results announcements** (an 8-K carrying item 2.02, "Results of
      Operations") — their ``reportDate`` is the *event* date, **not** a
      fiscal period end. Reading it as one would invent a period that does
      not exist (Berkshire's 2026-11-07 Q3 announcement carries
      ``reportDate`` 2026-11-07). These become announcement records with
      ``period_end=None``: they prove a period was announced without
      claiming to know which fiscal period it covers.

    Everything else — an insider Form 4 is the common case — is skipped.
    A Form 4's date is a transaction date; treating it as a fiscal period
    made 2026-10-30 look like the newest "period" for Berkshire, hiding the
    real staleness behind an unrelated filing.
    """
    recent = ((payload or {}).get("filings") or {}).get("recent") or {}
    if not recent:
        return []

    def _column(name: str) -> list:
        value = recent.get(name)
        return value if isinstance(value, list) else []

    accessions = _column("accessionNumber")
    forms = _column("form")
    filing_dates = _column("filingDate")
    report_dates = _column("reportDate")
    accepted = _column("acceptanceDateTime")
    documents = _column("primaryDocument")
    items = _column("items")

    def _at(column: list, index: int):
        return column[index] if index < len(column) else None

    records: list[FilingRecord] = []
    for i, accession in enumerate(accessions):
        form = (_at(forms, i) or "").upper()
        filing_date = _at(filing_dates, i)
        report_date = _at(report_dates, i)
        if not form or not filing_date:
            continue

        if form in PERIODIC_FORMS:
            # The period end is the report's own date, not a calendar rule.
            if not report_date:
                continue
            records.append(FilingRecord(
                period=report_date,
                period_end=report_date,
                published_date=None,
                filing_date=str(filing_date)[:10],
                accepted_at=_at(accepted, i) or None,
                form_type=form,
                accession_number=accession,
                source_type="sec_filing",
                source_url=_at(documents, i) or None,
            ))
            continue

        if form in ANNOUNCEMENT_FORMS and _is_results_announcement(_at(items, i)):
            records.append(FilingRecord(
                period="",                 # unknown by design, never invented
                period_end=None,
                published_date=str(filing_date)[:10],
                filing_date=str(filing_date)[:10],
                accepted_at=_at(accepted, i) or None,
                form_type=form,
                accession_number=accession,
                source_type="sec_filing",
                source_url=_at(documents, i) or None,
            ))
    return records


def _is_results_announcement(items) -> bool:
    """Whether an 8-K's item list includes 2.02 (Results of Operations).

    Item 2.02 is the one that announces earnings. Without it an 8-K reports
    something else entirely (a dividend, a governance change) and says
    nothing about a fiscal period having been published.
    """
    if not items:
        return False
    return any(part.strip().startswith("2.02") for part in str(items).split(","))


def merge_filings(records: list[FilingRecord]) -> list[FilingRecord]:
    """Collapse periodic records for the same period, keeping the earliest.

    Two records for one fiscal period (an 10-Q and its amendment, or a
    company release and the filing) describe one publication event for
    freshness purposes: the period became public on the earliest date.
    Keeping both would let the gate see two "different" filings for one
    period; keeping only the later would hide the release.

    Announcements are never merged: they name no period, so they are kept
    as they are, in filing order.
    """
    by_period: dict[str, FilingRecord] = {}
    announcements: list[FilingRecord] = []
    for record in records:
        if record.is_announcement or record.period_end is None:
            announcements.append(record)
            continue
        current = by_period.get(record.period_end)
        if current is None:
            by_period[record.period_end] = record
            continue
        current_key = (current.filing_date or current.published_date or "")
        record_key = (record.filing_date or record.published_date or "")
        if record_key < current_key:
            by_period[record.period_end] = record
    merged = [by_period[end] for end in sorted(by_period)]
    merged.extend(sorted(announcements,
                         key=lambda r: r.filing_date or r.published_date or ""))
    return merged


def filings_from_sec_edgar(ticker: str, as_of_date: str) -> list[FilingRecord]:
    """Filing records from SEC EDGAR, as known at ``as_of_date`` (no look-ahead).

    Two sources are merged, because neither is sufficient alone:

    - the **submission history** lists every filing, including the 8-K that
      announces results without tagging them in XBRL — the only way a period
      announced but not yet restated becomes visible to the gate;
    - **companyfacts** dates the periods that *are* tagged, which covers
      filers whose submissions payload is trimmed.

    Per period the earliest publication wins (``merge_filings``). A filing
    dated after ``as_of_date`` was not known then and is excluded. A ticker
    that is not a US filer, or an unreachable SEC, yields an empty list: the
    caller reports FUNDAMENTALS_UNAVAILABLE rather than inventing a filing.
    """
    from tradingagents.dataflows.vendors import sec_edgar

    try:
        cik = sec_edgar.cik_for(ticker)
        if cik is None:
            return []
    except Exception:  # noqa: BLE001 — an unreachable SEC is UNAVAILABLE, not a crash
        return []

    records: list[FilingRecord] = []

    # 1. The submission history: every filing, 8-K releases included.
    try:
        submissions = sec_edgar._cached_json(
            sec_edgar._SUBMISSIONS_URL.format(cik=cik), f"CIK{cik}-submissions.json"
        )
        records.extend(
            r for r in filings_from_submissions(submissions)
            if r.filing_date and r.filing_date <= as_of_date
        )
    except Exception:  # noqa: BLE001 — one source failing is not a verdict
        pass

    # 2. The tagged facts, which date the periods that carry XBRL.
    try:
        facts = sec_edgar._cached_json(
            sec_edgar._FACTS_URL.format(cik=cik), f"CIK{cik}.json"
        )
    except Exception:  # noqa: BLE001 — same rule
        facts = {}

    us_gaap = (facts.get("facts") or {}).get("us-gaap") or {}
    tagged: dict[str, dict] = {}   # period end -> earliest filing metadata
    for tag_data in us_gaap.values():
        for unit_values in (tag_data.get("units") or {}).values():
            for fact in unit_values:
                end, filed = fact.get("end"), fact.get("filed")
                if not end or not filed or str(filed) > as_of_date:
                    continue
                seen = tagged.get(end)
                if seen is None or str(filed) < seen["filed"]:
                    tagged[end] = {
                        "filed": str(filed),
                        "form": fact.get("form"),
                        "accn": fact.get("accn"),
                    }
    records.extend(
        FilingRecord(
            period=end,
            period_end=end,
            published_date=None,
            filing_date=meta["filed"],
            form_type=meta["form"],
            accession_number=meta["accn"],
            source_type="sec_filing",
        )
        for end, meta in sorted(tagged.items())
    )

    return merge_filings(records)

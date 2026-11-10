"""Market event claims (§4 of P2): a news figure is not a market fact.

The BRK-B reports of 2026-11-09 stated a +0.76% end-of-day move (570.89 to
575.25) while some articles described -1.01%. Both can be true: an article
written at 11:00 New York about "today's drop" measures an *intraday* window,
and comparing it to the session's close-to-close change compares two different
things.

The audit's rule was not "delete the article". A figure from a news source is
kept and labelled — it may be the only description of a window the price series
does not record at that granularity. What it may not do is *replace* the
certified end-of-day change. That is the defect this module prevents, and it is
the reason the verdicts are five rather than two:

``CONFIRMED``
    The article's window and comparison type match the benchmark snapshot, and
    the figure agrees with the certified change.
``HISTORICAL_CONTEXT``
    The article describes a past session, not the analysis date. True, and
    simply not evidence about today.
``TIME_WINDOW_MISMATCH``
    The figure is a real move, but over a window the benchmark does not measure
    (an intraday span against a close-to-close change). Not contradicted — not
    comparable.
``CONTRADICTED``
    Same window, same comparison type, different figure beyond tolerance. This
    is the only verdict that says the article is wrong.
``UNVERIFIED``
    No benchmark snapshot, no publication time, or no window: nothing to check
    it against. Never promoted to a fact.

**The benchmark is never replaced.** A caller that needs the end-of-day change
reads it from the certified bar; this module only says whether an article's
number may be quoted beside it, and with what label.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from tradingagents.dataflows.math.precision import PERCENT_QUANTUM, quantize


class ComparisonType(StrEnum):
    """What two prices are being compared as."""

    CLOSE_TO_CLOSE = "close_to_close"       # one session's close against the prior close
    OPEN_TO_CLOSE = "open_to_close"         # within one session
    INTRADAY = "intraday"                   # two instants inside a session
    UNKNOWN = "unknown"


class EventClaimVerdict(StrEnum):
    """Whether an article's move may be quoted as the session's change."""

    CONFIRMED = "CONFIRMED"
    HISTORICAL_CONTEXT = "HISTORICAL_CONTEXT"
    TIME_WINDOW_MISMATCH = "TIME_WINDOW_MISMATCH"
    CONTRADICTED = "CONTRADICTED"
    UNVERIFIED = "UNVERIFIED"


@dataclass(frozen=True)
class BenchmarkSnapshot:
    """The certified EOD change an article might be quoted against.

    Built from a confirmed bar: ``previous_close`` and ``close`` are the two
    settled prices the change is computed from, and ``session_date`` names the
    session they belong to. Everything here comes from the exchange series, not
    from prose.
    """

    session_date: str
    previous_close: float
    close: float
    snapshot_id: str | None = None
    exchange_timezone: str = "America/New_York"

    @property
    def change_pct(self) -> Decimal:
        """The signed close-to-close change, quantized like every other percent."""
        return quantize(
            (Decimal(str(self.close)) / Decimal(str(self.previous_close)) - 1) * 100,
            PERCENT_QUANTUM,
        )

    def summary(self) -> dict:
        return {
            "session_date": self.session_date,
            "previous_close": self.previous_close,
            "close": self.close,
            "change_pct": str(self.change_pct),
            "comparison_type": str(ComparisonType.CLOSE_TO_CLOSE),
            "snapshot_id": self.snapshot_id,
            "exchange_timezone": self.exchange_timezone,
        }


@dataclass(frozen=True)
class EventClaim:
    """A quantitative market claim read from a news article.

    Every field the audit asked for travels with it, so a reader can decide
    whether to trust the figure without opening the article: where it came from,
    when it was published and updated, which window its price covers, what kind
    of comparison it makes, and the benchmark it was checked against.
    """

    stated_pct: float
    # Provenance
    url: str | None = None
    provider: str | None = None
    published_at: str | None = None
    updated_at: str | None = None
    # What the figure measures
    window_start: str | None = None
    window_end: str | None = None
    comparison: ComparisonType = ComparisonType.UNKNOWN
    session_date: str | None = None
    # What it was checked against
    benchmark: BenchmarkSnapshot | None = None
    reconciliation_status: EventClaimVerdict = EventClaimVerdict.UNVERIFIED
    reason: str = ""
    tolerance_pct: float = 0.05

    @property
    def may_replace_eod_change(self) -> bool:
        """Whether this figure may stand in for the certified session change.

        Only a confirmed figure over the same window and comparison type. A
        ``TIME_WINDOW_MISMATCH`` is a legitimate observation of a different
        thing; it is still not the close-to-close change.
        """
        return self.reconciliation_status is EventClaimVerdict.CONFIRMED

    @property
    def quotable(self) -> bool:
        """Whether the figure may be shown at all, with its label.

        Everything except ``UNVERIFIED`` may be quoted *as what it is*. An
        unverified figure has nothing behind it and is not shown as evidence.
        """
        return self.reconciliation_status is not EventClaimVerdict.UNVERIFIED

    def summary(self) -> dict:
        return {
            "stated_pct": self.stated_pct,
            "url": self.url,
            "provider": self.provider,
            "published_at": self.published_at,
            "updated_at": self.updated_at,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "comparison": str(self.comparison),
            "session_date": self.session_date,
            "benchmark": (self.benchmark.summary()
                          if self.benchmark is not None else None),
            "reconciliation_status": str(self.reconciliation_status),
            "may_replace_eod_change": self.may_replace_eod_change,
            "quotable": self.quotable,
            "reason": self.reason,
        }


def reconcile_event_claim(claim: EventClaim,
                          benchmark: BenchmarkSnapshot | None = None) -> EventClaim:
    """Classify ``claim`` against ``benchmark``, returning a new claim.

    The order of the checks is the point: each verdict is narrower than the one
    before, so a figure is only called ``CONTRADICTED`` once it is known to be
    measuring the *same* thing. Calling an intraday move "wrong" because it
    disagrees with a close-to-close change would be the same mistake as calling
    a partial candle a close.
    """
    bench = benchmark or claim.benchmark
    if bench is None:
        return _with(claim, EventClaimVerdict.UNVERIFIED,
                     "no certified benchmark was supplied: the figure cannot be checked")

    def verdict(kind: EventClaimVerdict, reason: str) -> EventClaim:
        """The claim with this verdict and the benchmark it was judged against."""
        return _with(claim, kind, reason, bench)

    if claim.published_at is None:
        return verdict(EventClaimVerdict.UNVERIFIED,
                       "the article carries no publication time, so its window is unknown")

    # A figure about an earlier session is context, not evidence about today.
    if claim.session_date and claim.session_date != bench.session_date:
        return verdict(EventClaimVerdict.HISTORICAL_CONTEXT,
                       f"the article describes {claim.session_date}, not the "
                       f"analysed session {bench.session_date}")

    # Different comparison type: a real move over a window the benchmark does
    # not measure. This is the -1.01% against +0.76% case.
    if claim.comparison is not ComparisonType.CLOSE_TO_CLOSE:
        return verdict(
            EventClaimVerdict.TIME_WINDOW_MISMATCH,
            f"the figure is a {claim.comparison} change while the certified "
            "change is close-to-close: the two measure different windows",
        )

    if claim.window_start or claim.window_end:
        return verdict(EventClaimVerdict.TIME_WINDOW_MISMATCH,
                       "the figure names an explicit window, so it is not the session close")

    difference = abs(Decimal(str(claim.stated_pct)) - bench.change_pct)
    if difference <= Decimal(str(claim.tolerance_pct)):
        return verdict(EventClaimVerdict.CONFIRMED,
                       f"agrees with the certified close-to-close change "
                       f"{bench.change_pct}%")

    return verdict(
        EventClaimVerdict.CONTRADICTED,
        f"states {claim.stated_pct}% where the same window's certified change "
        f"is {bench.change_pct}%",
    )


def _with(claim: EventClaim, verdict: EventClaimVerdict, reason: str,
          benchmark: BenchmarkSnapshot | None = None) -> EventClaim:
    """The claim with a verdict, keeping every other field intact."""
    from dataclasses import replace

    return replace(claim, reconciliation_status=verdict, reason=reason,
                   benchmark=benchmark or claim.benchmark)


def reconcile_claims(claims: list[EventClaim],
                     benchmark: BenchmarkSnapshot | None = None) -> list[EventClaim]:
    """Classify a run's articles against one certified snapshot."""
    return [reconcile_event_claim(claim, benchmark) for claim in claims]


def eod_change_is_replaceable(claims: list[EventClaim]) -> bool:
    """Whether any article's figure may stand in for the certified change.

    Always ``False`` in practice: the benchmark is read from the confirmed bar,
    and an article is never a substitute for it. The helper exists so a caller
    does not have to re-derive the rule from the verdicts.
    """
    return any(c.may_replace_eod_change for c in claims) and any(
        c.reconciliation_status is not EventClaimVerdict.CONFIRMED for c in claims)

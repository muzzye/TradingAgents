"""Cross-agent consistency: deterministic checks over the run's own reports.

The BRK-B report of 2026-11-08 contained a contradiction no single agent could
see: the Sentiment Analyst wrote that "the market has already absorbed the Q3"
while the News Analyst's own causal check proved no post-event session had
traded. Each report was internally plausible; the contradiction lived between
them. This module is the deterministic reader that compares the reports the
run actually produced and flags the conflicts an LLM cannot be trusted to
notice about its own output.

Two checks, both pure functions over the run's state:

1. **Causal consistency** — a report that claims the market reacted to an
   event is contradicted when the causal guard's verdict says no post-event
   session exists. The claim patterns are structural (a reaction verb plus a
   price/absorption object), never per-ticker.
2. **Numeric consistency** — a percentage move stated in one report is
   recomputed from the closes the verified snapshot reports; a stated figure
   that disagrees with the arithmetic beyond a tolerance is flagged.

The verdicts feed the audit record and the Execution Gate's data-quality
input. They never rewrite a report: the analysts' text is preserved, the
conflict is recorded beside it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# The causal-guard statuses this module reads.
NOT_YET_OBSERVABLE = "NOT_YET_OBSERVABLE"
OBSERVABLE = "OBSERVABLE"

# Report keys read, in a stable order for the audit trail.
REPORT_KEYS = ("market_report", "sentiment_report", "news_report", "fundamentals_report")

# A reaction claim: a market-reaction verb near an absorption/price object.
# Structural, language-level patterns — no ticker, no company name.
_REACTION_CLAIM = re.compile(
    r"\b(?:market|stock|shares?|price)\b[^.?!]{0,80}?\b"
    r"(?:absorb\w*|digest\w*|priced? in|react\w*|respond\w*|discount\w*|"
    r"incorporat\w*|reflect\w*|moved on|rallied on|fell on|dropped on|jumped on)\b",
    re.IGNORECASE,
)

# A claim that the reaction has *already happened* (perfect/complete aspect).
_ALREADY_DONE = re.compile(
    r"\b(?:already|has been|have been|was|were|is now|now)\b", re.IGNORECASE
)

# A percentage move with its number: "-0.25%", "2.3 percent", "+1,5%".
_PCT_MOVE = re.compile(
    r"([+-]?\d[\d.,]*)\s*(?:%|percent|per cent)", re.IGNORECASE
)


@dataclass(frozen=True)
class ConsistencyIssue:
    """One cross-report conflict, machine-readable for the audit trail."""

    code: str
    severity: str                    # "error" | "warning"
    report_key: str                  # which report carries the offending claim
    message: str
    excerpt: str = ""                # the matched text, trimmed

    @property
    def is_error(self) -> bool:
        return self.severity == "error"


@dataclass
class ConsistencyVerdict:
    """All cross-report findings for one run, with the decision-level status."""

    issues: list[ConsistencyIssue] = field(default_factory=list)

    def add(self, issue: ConsistencyIssue) -> None:
        self.issues.append(issue)

    @property
    def blocks_decision(self) -> bool:
        """True when any error-severity conflict exists: the synthesis must not
        present a conclusion resting on a claim another report disproves."""
        return any(i.is_error for i in self.issues)

    @property
    def error_codes(self) -> tuple[str, ...]:
        return tuple(i.code for i in self.issues if i.is_error)

    def summary(self) -> dict:
        """Machine-readable summary for the audit trail."""
        return {
            "total_issues": len(self.issues),
            "errors": sum(1 for i in self.issues if i.is_error),
            "warnings": sum(1 for i in self.issues if not i.is_error),
            "codes": [i.code for i in self.issues],
        }


def _excerpts(text: str, pattern: re.Pattern) -> list[str]:
    """The matched spans of ``text``, trimmed for the audit trail."""
    out = []
    for m in pattern.finditer(text or ""):
        excerpt = " ".join(m.group(0).split())
        if excerpt and excerpt not in out:
            out.append(excerpt)
    return out


def _claim_windows(text: str, pattern: re.Pattern) -> list[str]:
    """Each match with surrounding context, so an aspect marker just outside
    the verb span ("already absorbed", "has now been priced in") is seen."""
    windows = []
    for m in pattern.finditer(text or ""):
        window = text[max(0, m.start() - 40):m.end() + 40]
        flattened = " ".join(window.split())
        if flattened and flattened not in windows:
            windows.append(flattened)
    return windows


def check_causal_consistency(
    reports: dict[str, str],
    post_event_status: str | None,
) -> ConsistencyVerdict:
    """Reaction claims vs the causal guard's verdict.

    When the guard says ``NOT_YET_OBSERVABLE`` — no session has traded since
    the event was published — any report claiming the market already reacted
    is a temporal contradiction: the claim cannot be true yet. When the
    status is ``OBSERVABLE`` or unknown, no verdict is made: the guard, not
    this module, owns the causal question.
    """
    verdict = ConsistencyVerdict()
    if post_event_status != NOT_YET_OBSERVABLE:
        return verdict
    for key in REPORT_KEYS:
        text = reports.get(key) or ""
        for window in _claim_windows(text, _REACTION_CLAIM):
            # "priced in" / "already absorbed" style claims assert completion;
            # a bare "may react" conditional does not. The already-done marker
            # inside the window decides.
            if not _ALREADY_DONE.search(window):
                continue
            verdict.add(ConsistencyIssue(
                code="CROSS_AGENT_REACTION_UNOBSERVABLE",
                severity="error",
                report_key=key,
                message=(
                    f"the {key.replace('_', ' ')} claims the market already "
                    f"reacted to the event, but no post-event session has "
                    f"traded (causal status {NOT_YET_OBSERVABLE}); the claim "
                    f"is temporally false"
                ),
                excerpt=window[:200],
            ))
    return verdict


def check_numeric_consistency(
    stated_pct: float | None,
    computed_pct: float | None,
    *,
    tolerance_pct: float = 0.05,
    report_key: str = "market_report",
) -> ConsistencyVerdict:
    """A stated percentage move vs the one the verified closes compute.

    Both sides must exist to compare; either missing is no verdict (the
    snapshot module owns "did the number come from real data"). A stated
    figure beyond ``tolerance_pct`` percentage points from the computed one
    is an error: the report's arithmetic disagrees with its own data.
    """
    verdict = ConsistencyVerdict()
    if stated_pct is None or computed_pct is None:
        return verdict
    delta = abs(stated_pct - computed_pct)
    if delta > tolerance_pct:
        verdict.add(ConsistencyIssue(
            code="CROSS_AGENT_NUMERIC_MISMATCH",
            severity="error",
            report_key=report_key,
            message=(
                f"a stated move of {stated_pct:+.2f}% disagrees with the "
                f"verified closes' {computed_pct:+.2f}% "
                f"(difference {delta:.2f} pp > {tolerance_pct} pp tolerance)"
            ),
        ))
    return verdict


def check_reports(
    reports: dict[str, str],
    *,
    post_event_status: str | None = None,
    stated_pct: float | None = None,
    computed_pct: float | None = None,
) -> ConsistencyVerdict:
    """Run every cross-report check; the combined verdict for the audit."""
    combined = ConsistencyVerdict()
    combined.issues.extend(
        check_causal_consistency(reports, post_event_status).issues
    )
    combined.issues.extend(
        check_numeric_consistency(
            stated_pct, computed_pct, report_key="market_report"
        ).issues
    )
    return combined

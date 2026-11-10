"""SEC revenue against what an analyst claims (FIX #1.1).

FIX #1 made the filing's consolidated revenue reach the registry as `VERIFIED`.
Runtime verification then found the other half missing: when a report states a
*different* consolidated revenue, the filing's value is kept — correctly — and
the disagreement is recorded nowhere. A reader cannot tell agreement from
contradiction.

This module compares the certified metric against explicit claims in the
analysts' reports and returns a structured finding for each contradiction.

Four rules keep it from firing on things that are not contradictions:

**Only an explicit level claim counts.** "Revenue was 74,322 million" is a
claim on the level. "Revenue grew 11.2%" is a rate, and a segment or a component
figure is a different quantity; none of them is compared to the total.

**Only the statement's own period counts.** A figure introduced as a prior
year's — "Q3 2024 revenue was …" — is context, not a claim about this quarter.

**A component labelled as a component is not a claim on the total.** When the
sentence says contract or segment revenue, the figure is excluded even though
the number may equal the total's component.

**No certified metric means no comparison.** Without a verified SEC fact there
is nothing to contradict, and inventing a finding would be worse than silence.

The certified `MetricRecord` is never modified: the finding sits beside it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# The finding code. Reuses the arithmetic family's naming so the audit reads
# consistently; nothing existing described a level mismatch against a filing.
CODE = "SEC_REVENUE_CLAIM_MISMATCH"
METRIC_ID = "consolidated_revenue"
SEVERITY = "high"

# Words that make a figure a *different* quantity rather than a claim on the
# consolidated total. A sentence containing one of these before the number
# describes a slice, and is not compared.
#
# The Italian words are here for the same reason the English ones are: the
# report's output language is configurable, and a segment figure written in
# Italian is no more a claim on the total than one written in English.
_COMPONENT_MARKERS = re.compile(
    r"(contract\w*\s+with\s+customers?|from\s+contracts?"
    r"|segment|division|subsidiary|product\s+line"
    r"|insurance\s+segment|railroad|energy\s+segment"
    r"|da\s+contratt\w*\s+con\s+clienti|contratti?\s+con\s+clienti"
    r"|segment\w*|divisione|sussidiaria|linea\s+di\s+prodotto)",
    re.IGNORECASE,
)

# Words that place the figure in a different period than the analysis.
# Words that place a figure in a period the analysis is not about, without
# naming the year: "prior year", "last year", "a year ago". The year itself is
# never hardcoded — a static list is wrong the moment the clock moves past it,
# and it silently stops recognising anything newer. A *named* period (Q3 2025,
# FY2024, "trimestre del 2024") is read and compared arithmetically instead.
_HISTORICAL_WORDS = re.compile(
    r"(prior\s+year|last\s+year|previous\s+year|year[- ]ago"
    r"|anno\s+precedente|esercizio\s+precedente|anno\s+scorso)",
    re.IGNORECASE,
)

# A period a sentence names: a quarter, a fiscal year, or a bare year, in the
# spellings the reports and their tables use.
_NAMED_PERIOD = re.compile(
    r"\b(?:q([1-4])\s*[/-]?\s*(\d{4})|(\d{4})\s*[/-]?\s*q([1-4])"
    r"|(fy)\s*(\d{4})|(\d{4}))\b",
    re.IGNORECASE,
)

# The scale words, in both languages the reports are written in. `mln`/`mld`
# are the Italian abbreviations; `M$`/`m$` name millions of dollars.
_SCALE = (r"billion|million|bn|mn|b|m"
          r"|miliardi|miliardo|milioni|milione|mld|mln|m\$")

# A level claim on revenue: a labelled revenue figure with a scale word. The
# scale is required — a bare number beside the word "revenue" is too weak to
# call a claim on the total.
#
# The label alternation covers the English and Italian labels the reports use,
# and the separator allows a Markdown table's pipes so `| Ricavi | 74.322 | mlm |`
# is read the same way as a sentence.
_LEVEL_CLAIM = re.compile(
    r"(?:total\s+revenue|consolidated\s+revenue|revenues?\s+(?:was|were|of|at|came\s+to|totall?ed)"
    r"|revenues?\s*[:=]"
    r"|ricavi\s+consolidati|ricavi\s+totali|ricavi|fatturato)"
    # The connector allows a sentence ("del Q3 sono stati 74.322"), a table's
    # pipes ("| Ricavi | 74.322 | mln |") and the plain ":" or "=" form. It
    # admits digits — "Q3 2026" sits between the label and the figure — but
    # never crosses a sentence break.
    r"[^.\n]{0,40}?"
    # A table cell puts the scale in the next column: "| 74.322 | mln |".
    r"(\d[\d,\.]*)[\s|]{0,8}(" + _SCALE + r")\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RevenueClaimFinding:
    """One contradiction between the filing and a report, ready for the audit."""

    code: str
    metric_id: str
    sec_value: float
    claimed_value: float
    period_end: str | None
    unit: str
    agent: str
    accession: str | None
    description: str
    severity: str = SEVERITY
    blocks: bool = True

    def summary(self) -> dict:
        return {
            "code": self.code,
            "metric_id": self.metric_id,
            "sec_value": self.sec_value,
            "claimed_value": self.claimed_value,
            "period_end": self.period_end,
            "unit": self.unit,
            "agent": self.agent,
            "accession": self.accession,
            "description": self.description,
            "severity": self.severity,
            "blocks": self.blocks,
        }


_BILLIONS = frozenset({"billion", "bn", "b", "miliardi", "miliardo", "mld"})
_MILLIONS = frozenset({"million", "mn", "m", "milioni", "milione", "mln",
                       "m$"})


def _parse_figure(raw: str, scale_word: str = "") -> float | None:
    """A stated number, reading both the English and the Italian convention.

    The two are ambiguous in the same characters, and the difference is a
    factor of a thousand:

    ``105,647``   English: 105647 (comma groups thousands)
    ``105.647``   Italian: 105647 (dot groups thousands)
    ``74,322``    Italian: 74.322 (comma is the decimal separator)
    ``359,5599``  Italian: 359.5599 (four decimals — never a thousands group)

    The rule that separates them: a separator followed by exactly three digits
    is a thousands group, *unless* the string also carries the other separator
    (in which case that one is the thousands group and this one is decimal).
    Anything else — one, two or four digits after the comma — is a decimal.
    A dot followed by four or more digits is a decimal too.
    """
    text = raw.strip()
    has_comma, has_dot = "," in text, "." in text

    if has_comma and has_dot:
        # Both present: whichever comes last is the decimal separator.
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif has_comma:
        head, _, tail = text.rpartition(",")
        # Three digits after the comma is normally a thousands group, but not
        # when the scale word makes that reading implausible: "74,322 miliardi"
        # as 74,322 billion would be 74 trillion, four orders of magnitude above
        # any company's revenue. The scale is what disambiguates, and it is
        # available here because the caller passes it.
        # A thousands reading is implausible with a *billions* word: 74,322
        # billion would be 74 trillion. With "million" it is the natural
        # reading (105,647 million = 105.6bn), so the scale decides.
        thousands_reading = len(tail) == 3 and scale_word not in _BILLIONS
        text = (text.replace(",", "") if thousands_reading
                else f"{head or '0'}.{tail}")
    elif has_dot:
        head, _, tail = text.rpartition(".")
        if len(tail) == 3:
            # 105.647 — Italian thousands grouping.
            text = text.replace(".", "")
        # Otherwise leave it: "74.322" with two decimals stays a decimal.

    try:
        return float(text)
    except ValueError:
        return None


def _scale_value(raw: str, scale: str) -> float | None:
    """A stated figure in base units, honouring the millions/billions word."""
    word = (scale or "").lower().strip()
    value = _parse_figure(raw, word)
    if value is None:
        return None
    if word in _BILLIONS:
        return value * 1_000_000_000
    if word in _MILLIONS:
        return value * 1_000_000
    return value


def _certified_revenue(registry: Any):
    """The certified `consolidated_revenue` record, or ``None``.

    Only a usable record qualifies: a refused or not-computable metric is not a
    fact, and comparing a report against it would invent a contradiction.
    """
    if registry is None:
        return None
    try:
        record = registry.get(METRIC_ID)
    except Exception:  # noqa: BLE001 — an unreadable registry certifies nothing
        return None
    if record is None or not record.is_usable:
        return None
    return record


def _certified_period(record) -> tuple[int | None, int | None]:
    """The quarter and year the certified figure belongs to.

    Read from the record's own `period_end`, so the comparison follows the
    MetricRecord rather than a list of years someone has to maintain. A record
    with no period yields ``(None, None)``, and every named period is then
    treated as comparable — the metric's own period is stated in the finding, so
    a reader can judge it.
    """
    end = getattr(record, "period_end", None)
    if not isinstance(end, str) or len(end) < 7:
        return None, None
    try:
        year = int(end[:4])
        month = int(end[5:7])
    except ValueError:
        return None, None
    # A fiscal quarter ends in month 3, 6, 9 or 12; anything else is an annual
    # or off-cycle period whose quarter is not derivable.
    quarter = (month + 2) // 3 if month in (3, 6, 9, 12) else None
    return quarter, year


def _named_period(context: str) -> tuple[int | None, int | None, bool]:
    """The (quarter, year, explicit) a sentence names, or all-None.

    ``explicit`` says whether the sentence named a period at all: a figure with
    no period beside it is a claim on the current one, and is still checked —
    that is the case the check exists for.
    """
    match = _NAMED_PERIOD.search(context)
    if not match:
        return None, None, False
    quarter = match.group(1) or match.group(4)
    year = match.group(2) or match.group(3) or match.group(6) or match.group(7)
    return (int(quarter) if quarter else None,
            int(year) if year else None,
            True)


def _period_is_comparable(context: str, record) -> bool:
    """Whether a figure stated in ``context`` is about the certified period.

    Three outcomes, and the middle one is why this is arithmetic rather than a
    word list:

    - the sentence names no period → the figure is a claim on the current one,
      so it is compared (a report that omits the quarter is still talking about
      it);
    - the sentence names the certified period → compared;
    - the sentence names a different period → not compared. A correct prior-year
      figure must not be reported as contradicting this year's.
    """
    if _HISTORICAL_WORDS.search(context):
        return False

    claimed_quarter, claimed_year, explicit = _named_period(context)
    if not explicit:
        return True

    certified_quarter, certified_year = _certified_period(record)
    if certified_year is None:
        # No certified period to compare against; the finding records whichever
        # period the report named, so nothing is hidden.
        return True
    if claimed_year is not None and claimed_year != certified_year:
        return False
    return not (claimed_quarter is not None and certified_quarter is not None
                and claimed_quarter != certified_quarter)


def reconcile_revenue_claims(registry: Any, reports: dict[str, str | None],
                             *, tolerance_pct: float = 0.5) -> list[RevenueClaimFinding]:
    """Findings for every report that contradicts the certified revenue.

    ``reports`` maps an agent name to its text. ``tolerance_pct`` absorbs
    rounding in a correctly quoted figure — a report that says 105,6 bn against
    105,647 m is agreeing, not contradicting.
    """
    record = _certified_revenue(registry)
    if record is None:
        return []

    sec_value = float(record.value)
    provenance = record.operands.get("revenue_provenance")
    if isinstance(provenance, str):
        # The record serialises operands for JSON; read the fields back.
        accession = _field(provenance, "accession")
    elif isinstance(provenance, dict):
        accession = provenance.get("accession")
    else:
        accession = None
    period_end = record.period_end

    findings: list[RevenueClaimFinding] = []
    for agent, text in (reports or {}).items():
        if not isinstance(text, str) or not text:
            continue
        for match in _LEVEL_CLAIM.finditer(text):
            # The label that identified the claim is part of the sentence that
            # qualifies it: "ricavi da contratti con clienti" carries its own
            # "contratti", which appears *inside* the match, not before it.
            context = text[max(0, match.start() - 120):match.end()]
            if _COMPONENT_MARKERS.search(context):
                continue
            if not _period_is_comparable(context, record):
                continue
            claimed = _scale_value(match.group(1), match.group(2))
            if claimed is None:
                continue
            if abs(claimed - sec_value) <= abs(sec_value) * (tolerance_pct / 100):
                continue
            findings.append(RevenueClaimFinding(
                code=CODE, metric_id=METRIC_ID, sec_value=sec_value,
                claimed_value=claimed, period_end=period_end,
                unit=getattr(record, "unit", "USD"), agent=agent,
                accession=accession,
                description=(
                    f"the {agent} report states consolidated revenue of "
                    f"{claimed:,.0f} {getattr(record, 'unit', 'USD')} for "
                    f"{period_end or 'the period'}, while the filing states "
                    f"{sec_value:,.0f}"),
            ))
    return findings


def _field(serialized: str, name: str) -> str | None:
    """Read one field out of a serialized provenance string."""
    match = re.search(rf"'{name}':\s*'([^']*)'", serialized)
    return match.group(1) if match else None

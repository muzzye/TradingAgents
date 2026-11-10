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
_COMPONENT_MARKERS = re.compile(
    r"(contract\w*\s+with\s+customers?|from\s+contracts?"
    r"|segment|division|subsidiary|product\s+line"
    r"|insurance\s+segment|railroad|energy\s+segment)",
    re.IGNORECASE,
)

# Words that place the figure in a different period than the analysis.
_HISTORICAL_MARKERS = re.compile(
    r"(prior\s+year|last\s+year|previous\s+year|year[- ]ago"
    r"|(?:q[1-4]|fy)\s*20(?:1\d|2[0-4]))",
    re.IGNORECASE,
)

# A level claim on revenue: a labelled revenue figure with a scale word. The
# scale is required — a bare number beside the word "revenue" is too weak to
# call a claim on the total.
_LEVEL_CLAIM = re.compile(
    r"(?:total\s+revenue|consolidated\s+revenue|revenues?\s+(?:was|were|of|at|came\s+to|totall?ed)"
    r"|revenues?\s*[:=])"
    r"[^.\d\n]{0,30}?(\d[\d,\.]*)\s*(billion|million|bn|mn|b|m)\b",
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


def _scale_value(raw: str, scale: str) -> float | None:
    """A stated figure in base units, honouring the millions/billions word."""
    try:
        value = float(raw.replace(",", ""))
    except ValueError:
        return None
    word = (scale or "").lower()
    if word in ("billion", "bn", "b"):
        return value * 1_000_000_000
    if word in ("million", "mn", "m"):
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
            context = text[max(0, match.start() - 120):match.start()]
            if _COMPONENT_MARKERS.search(context):
                continue
            if _HISTORICAL_MARKERS.search(context):
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

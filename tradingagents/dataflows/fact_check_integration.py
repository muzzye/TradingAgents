"""FIX #6 — wire the Fact Checker into the production decision path.

The checker has existed since Phase 5 and the graph has never called it:
`FactCheckReport` appears in `audit.py` and `debate.py` only as a type, so
`AuditRecord.fact_check` was always `None`. This module is the missing link, and
it is deliberately thin — it adapts, it does not re-implement.

Two decisions carry most of the weight, and both were learned by getting them
wrong first:

**Materiality.** A sentence carrying a number is not automatically a material
claim. "Q3 buyback of approximately $9.6 billion" is a figure about a buyback,
and citing the run's only certified metric (`distance_from_sma200`) because it
happened to be there refuted a sentence that never referred to it — contract
§5.9 forbids exactly that. A claim here is *material* only when it states a
value about a metric this run certified; anything else is recorded as
non-blocking context, which is the honest description.

**Evidence.** Only metrics the engine stood behind become facts. A `REFUSED`
or `NOT_COMPUTABLE` metric is precisely the case where the engine declined to
certify a number, so it must not become evidence a claim can cite. The registry
is derived from the run's own metrics, so evidence and run belong together by
construction (invariant I6).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from tradingagents.dataflows.fact_checker import (
    Claim,
    ClaimClassification,
    FactCheckReport,
    check_claims,
)
from tradingagents.dataflows.provenance import (
    VERIFIED,
    Fact,
    ProvenanceRegistry,
)

logger = logging.getLogger(__name__)

MAX_CLAIMS_PER_REPORT = 40

# A sentence is a candidate only when it carries a financial assertion. Bare
# prose is not a claim and must not be dressed as one.
_NUMBER_ASSERTION = re.compile(
    r"(\$?\s?[-+]?\d[\d,]*(?:\.\d+)?\s*"
    r"(?:%|x\b|billion|million|trillion|bn|mn|miliardi|milioni)"
    r"|\bP/?E\b|\bP/?B\b)",
    re.IGNORECASE)

_OPINION_MARKERS = re.compile(
    r"\b(?:expect|expected|forecast|project|anticipate|should|will|may|could|"
    r"likely|believe|estimate|atteso|previsto|stimiamo|potrebbe|dovrebbe|"
    r"riteniamo)\b",
    re.IGNORECASE)
_HYPOTHESIS_MARKERS = re.compile(
    r"\b(?:assum|assunzione|hypothes|hypothetical|scenario|ipotizz|"
    r"if\s+the|se\s+il|qualora)\b",
    re.IGNORECASE)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


@dataclass
class ClaimCollection:
    """What the collector found, including what it could not check."""

    claims: list[Claim] = field(default_factory=list)
    unchecked_reports: list[str] = field(default_factory=list)
    # Sentences that carried a figure but asserted nothing about a certified
    # metric. Recorded for the audit, never blocking: the system has no
    # evidence about them, and inventing a contradiction would be worse than
    # saying so.
    context_statements: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.claims


def build_evidence_registry(registry, snapshot_id: str) -> ProvenanceRegistry:
    """A `ProvenanceRegistry` holding the metrics this run certified."""
    evidence = ProvenanceRegistry(snapshot_id=snapshot_id or "unknown")
    if registry is None:
        return evidence

    now = datetime.now(UTC)
    try:
        records = list(registry._records.values())  # noqa: SLF001 — read-only
    except AttributeError:
        return evidence

    for record in records:
        if not getattr(record, "is_usable", False):
            continue
        value = getattr(record, "value", None)
        if value is None:
            continue
        try:
            evidence.add(Fact(
                fact_id=getattr(record, "metric", ""),
                value=float(value),
                unit=getattr(record, "unit", "") or "",
                period=None,
                period_end=getattr(record, "period_end", None),
                # The provenance layer's own vocabulary: a metric is a
                # derivation, not an external publication.
                source_type="derived_calculation",
                source_uri=None,
                published_at=now,
                retrieved_at=now,
                snapshot_id=evidence.snapshot_id,
                verification_status=VERIFIED,
                notes=f"certified by {getattr(record, 'formula_id', '')}",
            ))
        except Exception:  # noqa: BLE001 — one bad record must not lose the rest
            logger.warning("metric %r could not become evidence",
                           getattr(record, "metric", "?"), exc_info=True)
    return evidence


def _classification_for(sentence: str) -> ClaimClassification:
    """What kind of statement this is, from its own wording — conservatively."""
    if _HYPOTHESIS_MARKERS.search(sentence):
        return ClaimClassification.HYPOTHESIS
    if _OPINION_MARKERS.search(sentence):
        return ClaimClassification.FORECAST
    return ClaimClassification.FACT


# How a report writes each metric. The registry's names are snake_case
# identifiers; prose says "Trailing P/E", "P/B", "consolidated revenue". Without
# this map nothing ever matched and every sentence fell through to context,
# which is the same as not checking at all.
_METRIC_PHRASES = {
    "pe_trailing": (r"trailing\s+p/?e", r"p/?e\s+trailing", r"p/?e\s+ratio",
                    r"price[-\s]to[-\s]earnings"),
    "price_to_book": (r"p/?b\b", r"price[-\s]to[-\s]book"),
    "consolidated_revenue": (r"total\s+revenue", r"consolidated\s+revenue",
                             r"ricavi\s+consolidati", r"\brevenues?\b"),
    "revenue_growth": (r"revenue\s+growth", r"crescita\s+dei\s+ricavi"),
    "eps_gaap": (r"\beps\b", r"earnings\s+per\s+share", r"utile\s+per\s+azione"),
    "eps_ttm": (r"eps\s*\(?ttm\)?", r"eps\s+trailing"),
    "book_value_per_share_B": (r"book\s+value\s+per\s+share", r"\bbvps\b",
                               r"patrimonio\s+netto\s+per\s+azione"),
    "session_return": (r"session\s+return", r"variazione\s+di\s+seduta"),
    "distance_from_sma50": (r"sma\s*50", r"50[-\s]day"),
    "distance_from_sma200": (r"sma\s*200", r"200[-\s]day"),
    "distance_from_high": (r"52[-\s]week\s+high", r"massimo\s+annuale"),
}


def _metric_named(sentence: str, metric: str) -> bool:
    """Whether the sentence is talking about this metric.

    Checked against the phrasings reports actually use, then against the
    identifier itself. A metric with no phrase map falls back to its
    underscore-to-space form, which at least matches the literal name.
    """
    text = sentence.lower()
    for pattern in _METRIC_PHRASES.get(metric, ()):
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return metric.replace("_", " ") in text or metric in text


def collect_claims(reports: dict[str, str | None],
                   evidence: ProvenanceRegistry) -> ClaimCollection:
    """Turn report prose into claims, and separate material from context.

    A sentence becomes a *claim* only when it names a metric this run
    certified. Anything else stays in ``context_statements``: reported, never
    blocking, because the system holds no evidence about it either way.
    """
    collection = ClaimCollection()
    available = sorted(getattr(evidence, "facts", {}) or {})

    for agent, text in (reports or {}).items():
        if not isinstance(text, str) or not text.strip():
            continue
        produced = 0
        for raw in _SENTENCE_SPLIT.split(text):
            sentence = raw.strip()
            if not sentence or produced >= MAX_CLAIMS_PER_REPORT:
                continue
            if not _NUMBER_ASSERTION.search(sentence):
                continue
            cited = tuple(name for name in available
                          if name and _metric_named(sentence, name))
            if not cited:
                # No certified metric is named: this is context, not a claim
                # the system can be wrong about.
                collection.context_statements.append(sentence)
                continue
            classification = _classification_for(sentence)
            collection.claims.append(Claim(
                claim_id=f"{agent}:{produced}",
                statement=sentence,
                classification=classification,
                supporting_fact_ids=cited,
                # Only an assertion *of fact* about a certified figure is
                # material. A labelled forecast about it is reported, not
                # blocking: the contract treats a prediction as an opinion.
                blocking=(classification is ClaimClassification.FACT),
            ))
            produced += 1
        if produced == 0:
            collection.unchecked_reports.append(agent)
    return collection


def check_production_claims(reports: dict[str, str | None],
                            registry,
                            *,
                            snapshot_id: str,
                            post_event_status: str | None = None,
                            ) -> tuple[FactCheckReport, ClaimCollection]:
    """The adapter: evidence → claims → verdicts.

    Raises on a checker failure rather than returning a benign report: the
    caller must be able to fail closed, and a silent empty report would be the
    implicit PASS invariant I5 forbids.
    """
    evidence = build_evidence_registry(registry, snapshot_id)
    collection = collect_claims(reports, evidence)
    if collection.is_empty:
        return FactCheckReport(), collection
    report = check_claims(collection.claims, evidence,
                          post_event_status=post_event_status)
    return report, collection


def claims_for_audit(report: FactCheckReport,
                     collection: ClaimCollection) -> dict:
    """The audit shape: every verdict, plus the coverage the checker had."""
    return {
        "checked": True,
        "claims_total": len(collection.claims),
        "claims_unchecked_reports": list(collection.unchecked_reports),
        "context_statements": list(collection.context_statements),
        "verdicts": [
            {
                "claim_id": v.claim_id,
                "statement": v.statement,
                "classification": str(v.classification),
                "status": str(v.status),
                "reason_code": str(v.reason_code),
                "supporting_fact_ids": list(v.supporting_fact_ids),
                "blocking": v.blocking,
            }
            for v in report.verdicts
        ],
        "blocking_claim_ids": report.blocking_claim_ids,
        "unverified_blocking_claims": report.unverified_blocking_claim_ids,
        "refuted_claim_ids": report.refuted_claim_ids,
    }

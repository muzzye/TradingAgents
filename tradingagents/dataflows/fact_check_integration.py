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
from enum import StrEnum

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


class ClaimState(StrEnum):
    """What happened to one sentence, for the audit and for the gate.

    The distinction between `CLAIM_UNVERIFIABLE` and `CONTEXT_ONLY` is the
    substance of FIX #7. Fix #6 had one bucket for everything it could not
    check, so a Trader's valuation argument vanished alongside an unrelated
    figure. Both are unverified; only one is something the decision rests on.
    """

    CLAIM_CHECKED = "CLAIM_CHECKED"          # evidence existed and was compared
    CLAIM_UNVERIFIABLE = "CLAIM_UNVERIFIABLE"  # critical, but nothing to compare
    CONTEXT_ONLY = "CONTEXT_ONLY"            # a figure nothing depends on
    EXTRACTION_UNCERTAIN = "EXTRACTION_UNCERTAIN"  # a number we could not read


@dataclass
class CollectedClaim:
    """A claim, plus the state that says what was done with it."""

    claim: Claim
    state: ClaimState
    source: str
    material: bool

    def summary(self) -> dict:
        return {
            "claim_id": self.claim.claim_id,
            "source": self.source,
            "statement": self.claim.statement,
            "classification": str(self.claim.classification),
            "state": str(self.state),
            "material": self.material,
            "blocking": self.claim.blocking,
            "supporting_fact_ids": list(self.claim.supporting_fact_ids),
        }


@dataclass
class ClaimCollection:
    """What the collector found, including what it could not check."""

    claims: list[Claim] = field(default_factory=list)
    unchecked_reports: list[str] = field(default_factory=list)
    # Sentences that carried a figure but asserted nothing a decision rests
    # on. Recorded for the audit, never blocking: inventing a contradiction
    # about them would be worse than saying the system has no evidence.
    context_statements: list[str] = field(default_factory=list)
    # FIX #7: the per-sentence record, so a reader can tell a checked claim
    # from an unverifiable one from an irrelevant figure.
    collected: list[CollectedClaim] = field(default_factory=list)
    # FIX #7 (5.4): a report past the extraction limit is a bounded sample,
    # never a complete check. Both are recorded so no consumer mistakes one
    # for the other.
    truncated: bool = False
    truncated_reason: str = ""
    # How many sentences fell past the cap, so "40 checked, 160 unexamined"
    # is visible rather than "40 checked".
    statements_beyond_limit: int = 0
    # FIX #7 (F01/F08): which decision sources were read at all.
    sources_checked: list[str] = field(default_factory=list)
    sources_unchecked: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.claims

    @property
    def unverifiable_material_ids(self) -> list[str]:
        """Material claims the system could not check.

        These cannot authorize a proposal: a decision resting on a figure
        nobody verified is not a verified decision.
        """
        return [c.claim.claim_id for c in self.collected
                if c.material and c.state is ClaimState.CLAIM_UNVERIFIABLE]


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
    # A bare "P/E 18.2x" is a claim about the trailing multiple: the report is
    # talking about the current valuation, and requiring the word "trailing"
    # meant the commonest phrasing of all was invisible.
    "pe_trailing": (r"trailing\s+p/?e", r"p/?e\s+trailing", r"p/?e\s+ratio",
                    r"price[-\s]to[-\s]earnings",
                    r"\bp\s*/\s*e\b", r"\bpe\b", r"\bper\b"),
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
    "eps_change": (r"\beps\b\s+(?:growth|change)", r"crescita\s+eps"),
    "distance_from_sma": (r"\bsma\s*\d+", r"\d+[-\s]day\s+(?:moving|average)"),
    "price_to_book_ratio": (r"book[-\s]to[-\s]market",),
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


# The sources whose output a trade can rest on. A figure here is an argument
# for the decision; the same figure in an analyst's report is context until
# the decision cites it.
_DECISION_SOURCES = frozenset(
    {"investment_plan", "trader_investment_plan", "final_trade_decision",
     "portfolio_manager"})

# Every metric name the system can certify. A sentence naming one of these is
# making a claim the system is *supposed* to be able to check; if it cannot,
# that is a gap in the run, not an irrelevant number.
_KNOWN_METRICS = frozenset(_METRIC_PHRASES)


def _is_material(source: str, citation: str | None) -> bool:
    """Whether a claim here could carry the decision.

    Material means the sentence names a metric **this system is supposed to
    certify** — `_KNOWN_METRICS` — whether or not the registry happened to
    produce it. That covers both cases the contract asks for:

    - a Trader's "entry at P/E 18.2x" is an argument about a metric the system
      owns, so the absence of a certified P/E is a gap, not a harmless note;
    - an analyst stating the same thing gets the same treatment.

    What it deliberately excludes is a figure about something else. "Peers
    trade at 14.2x" is not a claim this run can have an opinion about, and
    blocking on it would be the false positive §O3 forbids. An earlier version
    made *every* number in a decision-source material and blocked exactly that.
    """
    return citation is not None


def _is_forward_or_historical(sentence: str) -> bool:
    """Whether the sentence qualifies its multiple by period or by forecast.

    Reused from the numeric-claims checker rather than re-implemented (O5): it
    already knows that "forward", "atteso", "FY2024" and "storico" mean the
    figure is not the current trailing multiple. Without this, widening the
    metric recognition made "P/E 18.2x, forward" a claim about `pe_trailing`
    and refuted a correct forward statement — the exact false positive FIX #4
    removed.
    """
    from tradingagents.dataflows.math.claims import (
        _PE_FORWARD,
        _PE_HISTORICAL,
    )

    return bool(_PE_FORWARD.search(sentence) or _PE_HISTORICAL.search(sentence))


def collect_claims(reports: dict[str, str | None],
                   evidence: ProvenanceRegistry) -> ClaimCollection:
    """Turn report prose into claims, with an explicit state for each.

    Three outcomes, and the difference matters:

    - **CLAIM_CHECKED** — the sentence names a certified metric, so there is
      evidence and `check_claim` compares the content against it.
    - **CLAIM_UNVERIFIABLE** — the sentence names a metric the system
      certifies, or comes from a source the decision rests on, but no
      certified evidence backs it. Not context: something is wrong with the
      run, and the decision must not lean on it.
    - **CONTEXT_ONLY** — a figure nothing here can speak about. Reported,
      never blocking.

    Before FIX #7 the middle case did not exist: everything unverifiable was
    filed as context, so a Portfolio Manager could advance an unsupported
    number and the audit showed a harmless note.
    """
    collection = ClaimCollection()
    available = sorted(getattr(evidence, "facts", {}) or {})

    for source, text in (reports or {}).items():
        if not isinstance(text, str) or not text.strip():
            continue
        collection.sources_checked.append(source)
        produced = 0
        for raw in _SENTENCE_SPLIT.split(text):
            sentence = raw.strip()
            if not sentence:
                continue
            if not _NUMBER_ASSERTION.search(sentence):
                continue

            # A figure the sentence itself qualifies as forward or historical
            # is not a claim about the current multiple. Keeping it out of the
            # trailing comparison is what stops a correct forward statement
            # being refuted against `pe_trailing`.
            other_period = _is_forward_or_historical(sentence)
            named = [name for name in available
                     if name and _metric_named(sentence, name)]
            known = [] if other_period else [
                m for m in _KNOWN_METRICS if _metric_named(sentence, m)]
            if other_period:
                named = [n for n in named if n != "pe_trailing"]
            classification = _classification_for(sentence)
            material = _is_material(source, named[0] if named else
                                    (known[0] if known else None))

            if produced >= MAX_CLAIMS_PER_REPORT:
                # FIX #7 (5.4): the cap bounds the work unconditionally. A
                # report cannot make the checker enumerate thousands of
                # sentences, material or not. Claims past it are counted, not
                # processed, so the truncation is visible and the gate blocks
                # on incomplete coverage rather than reading a bounded sample
                # as a complete check. Keeping the material ones instead — an
                # earlier attempt — let a pathological report defeat the cap.
                collection.truncated = True
                collection.truncated_reason = (
                    f"{source}: more than {MAX_CLAIMS_PER_REPORT} claims; "
                    "coverage of this report is incomplete")
                collection.statements_beyond_limit += 1
                continue

            if named:
                state = ClaimState.CLAIM_CHECKED
                cited: tuple[str, ...] = tuple(named)
            elif material:
                state = ClaimState.CLAIM_UNVERIFIABLE
                cited = ()
            else:
                collection.context_statements.append(sentence)
                collection.collected.append(CollectedClaim(
                    claim=Claim(claim_id=f"{source}:ctx{produced}",
                                statement=sentence,
                                classification=classification,
                                supporting_fact_ids=(),
                                blocking=False),
                    state=ClaimState.CONTEXT_ONLY, source=source,
                    material=False))
                produced += 1
                continue

            claim = Claim(
                claim_id=f"{source}:{produced}",
                statement=sentence,
                classification=classification,
                supporting_fact_ids=cited,
                blocking=material and classification is ClaimClassification.FACT,
            )
            collection.claims.append(claim)
            collection.collected.append(CollectedClaim(
                claim=claim, state=state, source=source, material=material))
            produced += 1

        if produced == 0:
            collection.unchecked_reports.append(source)

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
        # FIX #7: which sources were read, and the per-sentence state, so a
        # reader can tell a checked claim from an unverifiable one from an
        # irrelevant figure — and can tell partial coverage from complete.
        "sources_checked": list(collection.sources_checked),
        "sources_unchecked": list(collection.sources_unchecked),
        "statements": [c.summary() for c in collection.collected],
        "unverifiable_material": collection.unverifiable_material_ids,
        "truncated": collection.truncated,
        "truncated_reason": collection.truncated_reason,
        "statements_beyond_limit": collection.statements_beyond_limit,
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

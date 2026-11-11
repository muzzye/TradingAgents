"""Independent Fact Checker (Phase 5, contract section 6).

An obligatory step between analysis and synthesis: every material claim an
analyst or the Portfolio Manager makes is classified, checked against the
run's verified facts, and either upheld or refuted with a machine-readable
reason. A refuted *blocking* claim imposes a BLOCK on the decision — the
Fact Checker can contradict anyone, including the Portfolio Manager.

The checker introduces no data of its own: it reasons only over the claims
presented and the provenance registry's facts (Phase 1). Every verdict is
auditable — claim, classification, supporting fact ids, status, reason.

Classifications follow the contract's minimum set. ``REFUTED`` is a status,
not a classification: a claim that was checked and contradicted keeps its
original classification and gains the refuted status plus a reason code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

from tradingagents.dataflows.causal_guard import NOT_YET_OBSERVABLE
from tradingagents.dataflows.provenance import ProvenanceRegistry


class ClaimClassification(StrEnum):
    """What kind of statement the claim is (contract's minimum set)."""

    FACT = "FACT"
    DERIVED_CALCULATION = "DERIVED_CALCULATION"
    CAUSAL_INFERENCE = "CAUSAL_INFERENCE"
    HYPOTHESIS = "HYPOTHESIS"
    FORECAST = "FORECAST"
    UNSUPPORTED = "UNSUPPORTED"


class ClaimStatus(StrEnum):
    """The check's outcome for a claim."""

    SUPPORTED = "SUPPORTED"
    UNVERIFIED = "UNVERIFIED"
    REFUTED = "REFUTED"


class ReasonCode(StrEnum):
    """Machine-readable reasons, stable for reports and the audit trail."""

    OK = "OK"
    NO_SUPPORTING_FACTS = "NO_SUPPORTING_FACTS"
    SUPPORTING_FACTS_UNVERIFIED = "SUPPORTING_FACTS_UNVERIFIED"
    SUPPORTING_FACTS_QUARANTINED = "SUPPORTING_FACTS_QUARANTINED"
    EVENT_AFTER_LAST_MARKET_SESSION = "EVENT_AFTER_LAST_MARKET_SESSION"
    POST_EVENT_PRICES_UNAVAILABLE = "POST_EVENT_PRICES_UNAVAILABLE"
    CONTRADICTED_BY_REGISTRY = "CONTRADICTED_BY_REGISTRY"
    HYPOTHESIS_STATED_AS_FACT = "HYPOTHESIS_STATED_AS_FACT"
    # FIX #6: a labelled opinion never becomes a certified fact.
    NOT_A_FACT = "NOT_A_FACT"


@dataclass(frozen=True)
class Claim:
    """One material claim as presented by an analyst or manager."""

    claim_id: str
    statement: str
    classification: ClaimClassification
    supporting_fact_ids: tuple[str, ...] = ()
    blocking: bool = False


@dataclass(frozen=True)
class ClaimVerdict:
    """The Fact Checker's verdict on one claim."""

    claim_id: str
    statement: str
    classification: ClaimClassification
    supporting_fact_ids: tuple[str, ...]
    status: ClaimStatus
    reason_code: ReasonCode
    blocking: bool

    @property
    def blocks_decision(self) -> bool:
        """Only a refuted *blocking* claim stops the decision (contract §6)."""
        return self.blocking and self.status is ClaimStatus.REFUTED


@dataclass
class FactCheckReport:
    """All verdicts for one run, with the derived decision-level status."""

    verdicts: list[ClaimVerdict] = field(default_factory=list)

    def add(self, verdict: ClaimVerdict) -> None:
        self.verdicts.append(verdict)

    @property
    def blocking_claim_ids(self) -> list[str]:
        return [v.claim_id for v in self.verdicts if v.blocks_decision]

    @property
    def refuted_claim_ids(self) -> list[str]:
        return [v.claim_id for v in self.verdicts if v.status is ClaimStatus.REFUTED]

    @property
    def unverified_blocking_claim_ids(self) -> list[str]:
        """Blocking claims the evidence could not verify (FIX #6, F03).

        Kept separate from a refutation so the audit shows which happened: a
        contradiction is a different finding from a gap.
        """
        return [v.claim_id for v in self.verdicts
                if v.blocking and v.status is ClaimStatus.UNVERIFIED]

    @property
    def blocks_decision(self) -> bool:
        """True when a material claim is refuted *or* left unverified.

        The synthesis must not present an unqualified conclusion over a
        refutation (contract §6), and a critical claim the evidence could not
        verify cannot contribute to a proposal either (F03). Both stop the
        order; the audit distinguishes them.
        """
        return bool(self.blocking_claim_ids) or bool(
            self.unverified_blocking_claim_ids)

    def summary(self) -> dict:
        """Machine-readable summary for the audit trail and the report."""
        return {
            "total_claims": len(self.verdicts),
            "supported": sum(1 for v in self.verdicts
                             if v.status is ClaimStatus.SUPPORTED),
            "unverified": sum(1 for v in self.verdicts
                              if v.status is ClaimStatus.UNVERIFIED),
            "refuted": len(self.refuted_claim_ids),
            "blocking_claims": self.blocking_claim_ids,
            "unverified_blocking_claims": self.unverified_blocking_claim_ids,
        }


_NUMBER_IN_TEXT = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")
_TEXT_SCALES = (
    ("trillion", 1e12), ("tn", 1e12), ("t", 1e12),
    ("miliardi", 1e9), ("miliardo", 1e9), ("billion", 1e9), ("bn", 1e9), ("b", 1e9),
    ("milioni", 1e6), ("milione", 1e6), ("million", 1e6), ("mn", 1e6), ("m", 1e6),
    ("migliaia", 1e3), ("thousand", 1e3), ("k", 1e3),
)
_FACT_UNIT_SCALES = {
    "usd_trillion": 1e12, "usd_billion": 1e9, "usd_million": 1e6,
    "usd_thousand": 1e3, "trillion": 1e12, "billion": 1e9, "million": 1e6,
}
# A sentence rounding to one decimal, or abbreviating, is the same quantity.
# 0.5% distinguishes that from a different number; it is the same rule the
# numeric-claims checker applies to a multiple.
_CONTENT_TOLERANCE = 0.005


def _stated_value(statement: str) -> float | None:
    """The first number a sentence states, scaled by a following word."""
    match = _NUMBER_IN_TEXT.search(statement or "")
    if not match:
        return None
    try:
        value = float(match.group(0).replace(",", ""))
    except ValueError:
        return None
    tail = (statement[match.end():match.end() + 20] or "").lower()
    for word, scale in _TEXT_SCALES:
        if re.match(rf"\s*{re.escape(word)}\b", tail):
            return value * scale
    return value


def _fact_value(fact) -> float | None:
    """The fact's own number in base units, scaled by its unit.

    A non-finite value is not a quantity. `NaN != NaN` in every comparison, so
    a NaN fact would sail past the contradiction test and certify whatever it
    was cited for — a fact the engine cannot reason about is not evidence.
    """
    import math

    raw = getattr(fact, "value", None)
    if isinstance(raw, bool) or raw is None:
        return None
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    unit = str(getattr(fact, "unit", "") or "").lower().strip()
    return number * _FACT_UNIT_SCALES.get(unit, 1.0)


_DIMENSIONLESS_UNITS = frozenset(
    {"multiple", "percent", "percentage_points", "pure", "ratio", "x"})


def _states_dimensionless(statement: str) -> bool:
    """Whether the sentence states a multiple or a percentage."""
    return bool(re.search(r"\d\s*(?:x\b|%)", statement or "", re.IGNORECASE))


def _contradicts(statement: str, fact) -> bool:
    """Whether the sentence's own figure disagrees with the fact's figure.

    Compared only when both sides have a number and the fact is a plain
    quantity. A percentage or a multiple is a different kind of measurement
    from a currency amount, so a `percent` fact is never compared to a
    sentence's dollar figure — that comparison would be a category error, not
    a contradiction.
    """
    stated = _stated_value(statement)
    actual = _fact_value(fact)
    if stated is None or actual is None:
        return False

    unit = str(getattr(fact, "unit", "") or "").lower().strip()
    if unit in _DIMENSIONLESS_UNITS:
        # A multiple or a percentage is comparable to the sentence's own
        # figure only when the sentence states one of the same kind. "P/E
        # 18.2x" against a 25.4x fact is a contradiction; "P/E of 18.2" and
        # "the stock rose 18.2%" are not the same measurement, so the
        # dimensionless unit requires a dimensionless figure in the text.
        if not _states_dimensionless(statement):
            return False
    elif _states_dimensionless(statement) and "%" in statement:
        # A currency fact against a percentage is a category error, not a
        # contradiction: the sentence is talking about something else.
        return False

    if actual == 0:
        return stated != 0
    return abs(stated - actual) / abs(actual) > _CONTENT_TOLERANCE


def check_claim(
    claim: Claim,
    registry: ProvenanceRegistry,
    *,
    post_event_status: str | None = None,
) -> ClaimVerdict:
    """Check one claim against the run's verified facts.

    The rules, in order:

    - A causal-inference claim about a market reaction is refuted when the
      causal guard says the post-event prices are not yet observable
      (``EVENT_AFTER_LAST_MARKET_SESSION`` / ``POST_EVENT_PRICES_UNAVAILABLE``).
    - A claim with no supporting fact ids cannot be more than a hypothesis:
      presented as FACT or DERIVED_CALCULATION it is refuted as
      ``NO_SUPPORTING_FACTS``; presented as HYPOTHESIS/FORECAST it is
      ``UNVERIFIED`` (an opinion, allowed as long as it is labelled one).
    - Supporting facts that are not in the registry, or are not verified,
      downgrade the claim: missing ids refute, unverified/quarantined ids
      mark the claim UNVERIFIED.
    - Verified supporting facts uphold the claim.
    """
    # Causal claims checked against the causal guard's verdict.
    if claim.classification is ClaimClassification.CAUSAL_INFERENCE:
        if post_event_status == NOT_YET_OBSERVABLE:
            return ClaimVerdict(
                claim_id=claim.claim_id, statement=claim.statement,
                classification=claim.classification,
                supporting_fact_ids=claim.supporting_fact_ids,
                status=ClaimStatus.REFUTED,
                reason_code=ReasonCode.EVENT_AFTER_LAST_MARKET_SESSION,
                blocking=claim.blocking,
            )
        if post_event_status is None:
            return ClaimVerdict(
                claim_id=claim.claim_id, statement=claim.statement,
                classification=claim.classification,
                supporting_fact_ids=claim.supporting_fact_ids,
                status=ClaimStatus.UNVERIFIED,
                reason_code=ReasonCode.POST_EVENT_PRICES_UNAVAILABLE,
                blocking=claim.blocking,
            )

    # Support requirements by classification.
    # FIX #6 (contract §3): absence of support is UNVERIFIED, not REFUTED.
    #
    # "Un riferimento a un Fact ID inesistente non dimostra, da solo, che la
    # frase sia falsa: distingue l'assenza del supporto dalla contraddizione
    # del contenuto." Refuting a fact-shaped claim because nobody cited
    # evidence conflated "we have not checked this" with "this is wrong", and
    # once the checker ran over real reports every unannotated sentence looked
    # like a contradiction.
    #
    # The safety the old behaviour reached for is preserved in the report: a
    # *blocking* claim that is not SUPPORTED still stops the decision, so an
    # unverified critical figure cannot authorize an order. It now says the
    # honest reason — the evidence is missing, not the claim false.
    if not claim.supporting_fact_ids:
        if claim.classification in (ClaimClassification.FACT,
                                    ClaimClassification.DERIVED_CALCULATION):
            return ClaimVerdict(
                claim_id=claim.claim_id, statement=claim.statement,
                classification=claim.classification,
                supporting_fact_ids=(),
                status=ClaimStatus.UNVERIFIED,
                reason_code=ReasonCode.NO_SUPPORTING_FACTS,
                blocking=claim.blocking,
            )
        # A labelled hypothesis or forecast is an opinion: unverified, not
        # refuted — but a *blocking* hypothesis stated without support is
        # the HYPOTHESIS_STATED_AS_FACT failure the contract calls out.
        return ClaimVerdict(
            claim_id=claim.claim_id, statement=claim.statement,
            classification=claim.classification,
            supporting_fact_ids=(),
            status=ClaimStatus.UNVERIFIED,
            reason_code=(ReasonCode.HYPOTHESIS_STATED_AS_FACT if claim.blocking
                         else ReasonCode.NO_SUPPORTING_FACTS),
            blocking=claim.blocking,
        )

    # Supporting facts must exist in the registry and be verified.
    missing = [fid for fid in claim.supporting_fact_ids if registry.get(fid) is None]
    if missing:
        # The registry holds no such fact: nothing supports the sentence and
        # nothing contradicts it. Same distinction as above.
        return ClaimVerdict(
            claim_id=claim.claim_id, statement=claim.statement,
            classification=claim.classification,
            supporting_fact_ids=claim.supporting_fact_ids,
            status=ClaimStatus.UNVERIFIED,
            reason_code=ReasonCode.NO_SUPPORTING_FACTS,
            blocking=claim.blocking,
        )
    not_verified = [
        fid for fid in claim.supporting_fact_ids
        if registry.get(fid) is not None and not registry.get(fid).is_usable
    ]
    if not_verified:
        statuses = {registry.get(fid).verification_status for fid in not_verified}
        reason = (ReasonCode.SUPPORTING_FACTS_QUARANTINED if "quarantined" in statuses
                  else ReasonCode.SUPPORTING_FACTS_UNVERIFIED)
        return ClaimVerdict(
            claim_id=claim.claim_id, statement=claim.statement,
            classification=claim.classification,
            supporting_fact_ids=claim.supporting_fact_ids,
            status=ClaimStatus.UNVERIFIED,
            reason_code=reason,
            blocking=claim.blocking,
        )

    # FIX #6 (A07-A09): the cited facts are verified, but that certifies the
    # *evidence*, not the sentence. A labelled hypothesis, forecast or
    # unsupported claim is an opinion: facts about its topic do not make a
    # projection true, and a forecast is unobservable by construction.
    if claim.classification in (ClaimClassification.HYPOTHESIS,
                                ClaimClassification.FORECAST,
                                ClaimClassification.UNSUPPORTED):
        return ClaimVerdict(
            claim_id=claim.claim_id, statement=claim.statement,
            classification=claim.classification,
            supporting_fact_ids=claim.supporting_fact_ids,
            status=ClaimStatus.UNVERIFIED,
            reason_code=ReasonCode.NOT_A_FACT,
            blocking=claim.blocking,
        )

    # FIX #6 (I2): and a factual claim must agree with the evidence it cites.
    # A verified fact id does not certify any sentence naming it — a sentence
    # stating 74,322 million while citing a 105,647 million fact is
    # contradicted by its own support.
    # A cited fact whose value is not a readable number cannot support a
    # quantitative claim: there is nothing to compare it to. Recorded as
    # unverified rather than supported, so a broken fact is never evidence.
    unreadable = [
        fid for fid in claim.supporting_fact_ids
        if registry.get(fid) is not None and _fact_value(registry.get(fid)) is None
        and _NUMBER_IN_TEXT.search(claim.statement or "") is not None
    ]
    if unreadable:
        return ClaimVerdict(
            claim_id=claim.claim_id, statement=claim.statement,
            classification=claim.classification,
            supporting_fact_ids=claim.supporting_fact_ids,
            status=ClaimStatus.UNVERIFIED,
            reason_code=ReasonCode.SUPPORTING_FACTS_UNVERIFIED,
            blocking=claim.blocking,
        )

    for fact_id in claim.supporting_fact_ids:
        fact = registry.get(fact_id)
        if fact is not None and _contradicts(claim.statement, fact):
            return ClaimVerdict(
                claim_id=claim.claim_id, statement=claim.statement,
                classification=claim.classification,
                supporting_fact_ids=claim.supporting_fact_ids,
                status=ClaimStatus.REFUTED,
                reason_code=ReasonCode.CONTRADICTED_BY_REGISTRY,
                blocking=claim.blocking,
            )

    return ClaimVerdict(
        claim_id=claim.claim_id, statement=claim.statement,
        classification=claim.classification,
        supporting_fact_ids=claim.supporting_fact_ids,
        status=ClaimStatus.SUPPORTED,
        reason_code=ReasonCode.OK,
        blocking=claim.blocking,
    )


def check_claims(
    claims: list[Claim],
    registry: ProvenanceRegistry,
    *,
    post_event_status: str | None = None,
) -> FactCheckReport:
    """Check every claim; the report carries the decision-level verdict."""
    report = FactCheckReport()
    for claim in claims:
        report.add(check_claim(claim, registry, post_event_status=post_event_status))
    return report

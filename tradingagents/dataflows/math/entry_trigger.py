"""Whether a plan's entry condition is satisfied *now* (FIX #5).

The Execution Gate has always taken ``trigger_met`` as a caller's boolean. The
graph never supplied one, so the default ``False`` meant the production path
could only ever produce ``WAIT``: `PROPOSE_PAPER_ORDER` was unreachable no
matter how good the data. This module is the missing half — a deterministic
evaluator that reads the plan's own entry condition and answers it against the
verified market snapshot.

Three things it deliberately does *not* do:

- it does not assume direction. ``Entry 540.00`` alone says nothing about
  whether the plan wants to buy a break above 540 or a dip below it. An
  unstated mode is ``AMBIGUOUS`` and yields no proposal;
- it does not accept a price that the registry would refuse. The same
  ``price_is_qualified`` rule that guards a valuation multiple guards the
  trigger: an indicator or an unverified quote cannot open a position;
- it does not treat a stale snapshot as a live quote. The bar's own timestamp
  has to belong to the analysis date, or the answer is ``NOT_EVALUABLE``.

``WAIT`` is an answer, not a failure: a plan whose condition is simply not met
yet is valid and simply early.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from tradingagents.dataflows.math.price_source import price_from_snapshot


class EntryMode(StrEnum):
    """How a plan intends to get in. Absent means the plan did not say."""

    # Buy when the price trades at or above the level (a break above a ceiling).
    BREAKOUT = "BREAKOUT"
    # Buy when the price trades at or below the level (a limit buy into a dip).
    PULLBACK = "PULLBACK"
    # The plan states an entry but not the comparison it implies.
    AMBIGUOUS = "AMBIGUOUS"


class TriggerOutcome(StrEnum):
    """What the evaluator concluded, and therefore what the gate may do."""

    MET = "MET"                    # condition satisfied -> may propose
    NOT_MET = "NOT_MET"            # valid plan, simply not yet -> WAIT
    NOT_EVALUABLE = "NOT_EVALUABLE"  # no usable price or stamp -> WAIT
    AMBIGUOUS = "AMBIGUOUS"        # mode unstated -> WAIT
    EXPIRED = "EXPIRED"            # the condition's own validity has passed


@dataclass(frozen=True)
class TriggerVerdict:
    """The deterministic answer, with everything needed to audit it."""

    outcome: TriggerOutcome
    met: bool
    mode: EntryMode
    reason: str
    # Evidence: what was compared, and where the number came from.
    entry_level: float | None = None
    observed_price: float | None = None
    observed_kind: str | None = None
    observed_status: str | None = None
    observed_timestamp: str | None = None
    session_date: str | None = None
    valid_until: str | None = None

    def summary(self) -> dict:
        """The shape the AuditRecord stores."""
        return {
            "met": self.met,
            "outcome": str(self.outcome),
            "mode": str(self.mode),
            "reason": self.reason,
            "entry_level": self.entry_level,
            "observed_price": self.observed_price,
            "observed_kind": self.observed_kind,
            "observed_status": self.observed_status,
            "observed_timestamp": self.observed_timestamp,
            "session_date": self.session_date,
            "valid_until": self.valid_until,
        }


def _fail(outcome: TriggerOutcome, mode: EntryMode, reason: str, *,
          entry_level: float | None = None,
          observed: dict | None = None,
          session_date: str | None = None,
          valid_until: str | None = None) -> TriggerVerdict:
    observed = observed or {}
    return TriggerVerdict(
        outcome=outcome, met=False, mode=mode, reason=reason,
        entry_level=entry_level,
        observed_price=observed.get("value"),
        observed_kind=observed.get("kind"),
        observed_status=observed.get("validation_status"),
        observed_timestamp=observed.get("timestamp"),
        session_date=session_date,
        valid_until=valid_until,
    )


def evaluate_entry_trigger(
    *,
    entry: float | None,
    mode: EntryMode | str | None,
    direction: str = "LONG",
    snapshot: dict | None,
    trade_date: str | None = None,
    valid_until: str | None = None,
) -> TriggerVerdict:
    """Whether the plan's entry condition holds, decided from verified inputs.

    ``snapshot`` is the run's ``verified_market_snapshot``: the same dict the
    analysts read and the registry certifies against. Nothing is parsed from
    prose here; the plan's numbers were already extracted deterministically.
    """
    resolved_mode = _coerce_mode(mode)

    if entry is None:
        return _fail(TriggerOutcome.NOT_EVALUABLE, resolved_mode,
                     "the plan states no entry level to evaluate")

    if resolved_mode is EntryMode.AMBIGUOUS:
        return _fail(
            TriggerOutcome.AMBIGUOUS, resolved_mode,
            "the plan states an entry level but not whether it buys a break "
            "above it or a dip below it; the direction of the comparison is "
            "not stated and must not be guessed",
            entry_level=entry)

    # The condition's own validity, when the plan states one.
    if valid_until and trade_date and str(trade_date) > str(valid_until):
        return _fail(TriggerOutcome.EXPIRED, resolved_mode,
                     f"the entry condition expired on {valid_until}",
                     entry_level=entry, valid_until=valid_until)

    if direction not in ("LONG", "SHORT"):
        return _fail(TriggerOutcome.NOT_EVALUABLE, resolved_mode,
                     f"unknown position direction {direction!r}",
                     entry_level=entry)

    price = price_from_snapshot(snapshot)
    if price is None:
        return _fail(
            TriggerOutcome.NOT_EVALUABLE, resolved_mode,
            "the snapshot carries no price to compare against the entry level",
            entry_level=entry)

    # The same rule that guards a valuation multiple guards the trigger: an
    # indicator is not a quote, and an unverified one cannot open a position.
    from tradingagents.dataflows.math.run_registry import MARKET_PRICE_KINDS

    kind = str(price.get("kind") or "").lower()
    status = str(price.get("validation_status") or "").upper()
    if kind not in MARKET_PRICE_KINDS:
        return _fail(
            TriggerOutcome.NOT_EVALUABLE, resolved_mode,
            ("the observed figure is not a market price: an indicator or an "
             "unclassified value cannot open a position"),
            entry_level=entry, observed=price, session_date=trade_date)
    if status != "VERIFIED":
        return _fail(
            TriggerOutcome.NOT_EVALUABLE, resolved_mode,
            f"the observed price is {status or 'unverified'}, not VERIFIED",
            entry_level=entry, observed=price, session_date=trade_date)

    # A snapshot must belong to the analysis date: an older bar is history, not
    # a current quote, and comparing it to today's entry would invent a signal.
    stamp = price.get("timestamp") or price.get("session_date")
    if not stamp:
        return _fail(
            TriggerOutcome.NOT_EVALUABLE, resolved_mode,
            "the observed price carries no timestamp, so it cannot be shown "
            "to be current",
            entry_level=entry, observed=price, session_date=trade_date)
    if trade_date and str(trade_date)[:10] not in str(stamp):
        return _fail(
            TriggerOutcome.NOT_EVALUABLE, resolved_mode,
            (f"the snapshot is from {stamp} but the analysis is for "
             f"{trade_date}: a stale quote is not a current one"),
            entry_level=entry, observed=price, session_date=trade_date)

    # A caller-supplied `price_provenance` passes through `price_from_snapshot`
    # unchanged, so the figure may live in `value` (what `qualified_price`
    # writes) or only in the snapshot's own `close`. Fall back rather than
    # assuming one shape, and refuse if neither yields a number.
    raw_value = price.get("value")
    if raw_value is None:
        raw_value = snapshot.get("close") if isinstance(snapshot, dict) else None
        if raw_value is None and isinstance(snapshot, dict):
            raw_value = snapshot.get("price")
    try:
        observed = float(raw_value)
    except (TypeError, ValueError):
        return _fail(
            TriggerOutcome.NOT_EVALUABLE, resolved_mode,
            "the snapshot's price is not a number that can be compared",
            entry_level=entry, observed=price, session_date=trade_date)
    met = _comparison_holds(resolved_mode, direction, observed, float(entry))
    if not met:
        return _fail(
            TriggerOutcome.NOT_MET, resolved_mode,
            _not_met_reason(resolved_mode, direction, observed, float(entry)),
            entry_level=entry, observed=price, session_date=trade_date)

    verdict = TriggerVerdict(
        outcome=TriggerOutcome.MET, met=True, mode=resolved_mode,
        reason=_met_reason(resolved_mode, direction, observed, float(entry)),
        entry_level=entry, observed_price=observed,
        observed_kind=price.get("kind"),
        observed_status=price.get("validation_status"),
        observed_timestamp=price.get("timestamp"),
        session_date=trade_date, valid_until=valid_until,
    )
    return verdict


def _coerce_mode(mode: EntryMode | str | None) -> EntryMode:
    if isinstance(mode, EntryMode):
        return mode
    if mode is None:
        return EntryMode.AMBIGUOUS
    try:
        return EntryMode(str(mode).upper())
    except ValueError:
        return EntryMode.AMBIGUOUS


def _comparison_holds(mode: EntryMode, direction: str,
                      price: float, entry: float) -> bool:
    """The comparison the mode names, interpreted for the position's side.

    A ``BREAKOUT`` buys strength: for a LONG it needs price >= entry, for a
    SHORT (a breakdown) price <= entry. A ``PULLBACK`` buys weakness, so the
    two invert. The mode describes the *shape* of the entry; the direction says
    which side of the level that shape sits on.
    """
    if mode is EntryMode.BREAKOUT:
        return price >= entry if direction == "LONG" else price <= entry
    if mode is EntryMode.PULLBACK:
        return price <= entry if direction == "LONG" else price >= entry
    return False


def _met_reason(mode: EntryMode, direction: str, price: float,
                entry: float) -> str:
    op = ">=" if (mode is EntryMode.BREAKOUT) == (direction == "LONG") else "<="
    return f"price {price:.2f} {op} entry {entry:.2f} for a {mode} {direction}"


def _not_met_reason(mode: EntryMode, direction: str, price: float,
                    entry: float) -> str:
    op = "<" if (mode is EntryMode.BREAKOUT) == (direction == "LONG") else ">"
    return (f"price {price:.2f} {op} entry {entry:.2f}: the {mode} {direction} "
            f"condition is not met yet")

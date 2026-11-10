"""Order authorisation (§Task C of P5): one quantity, four limits, no invention.

`size_with_exposure_cap` already answers "how many shares does the risk budget
and the exposure cap buy?". This module answers the rest of the question the
audit asks, because an order is authorised by the *most binding* of four limits,
not two:

``risk_limited_qty``        the stop-distance budget, stressed for gap and slippage
``exposure_limited_qty``    the per-symbol share of equity, net of what is held
``cash_limited_qty``        the cash actually available, net of commissions
``position_limited_qty``    the units actually held, for a reduction

The last two were the gap. Without a cash limit a plan could be authorised that
the account cannot pay for; without a position limit a REDUCE_LONG of "40% of
the book" could be sized against a book that is smaller than the engine's idea
of it. Both are now explicit and the binding one is named.

**Nothing is invented.** Every limit is either given by the caller or the
quantity is zero with a reason. A missing `available_cash` is not "infinite
cash", and a missing held quantity is not zero — it is unknown, and unknown
blocks.

Commissions and slippage are costs, and they are applied where they change the
answer: commissions reduce the cash available before the cash limit is
computed, slippage widens the stop distance before the risk limit is.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum


class SizingReason(StrEnum):
    """Why an authorised quantity is what it is, or why there is none."""

    OK = "OK"
    RISK_BUDGET_MISSING = "RISK_BUDGET_MISSING"
    PORTFOLIO_EQUITY_MISSING = "PORTFOLIO_EQUITY_MISSING"
    AVAILABLE_CASH_MISSING = "AVAILABLE_CASH_MISSING"
    POSITION_UNKNOWN = "POSITION_UNKNOWN"
    INVALID_ENTRY = "INVALID_ENTRY"
    INVALID_STOP = "INVALID_STOP"
    INVALID_DIRECTION = "INVALID_DIRECTION"
    TARGET_ALLOCATION_EXCEEDED = "TARGET_ALLOCATION_EXCEEDED"
    NON_POSITIVE_SIZE = "NON_POSITIVE_SIZE"
    CASH_EXCEEDED = "CASH_EXCEEDED"


@dataclass(frozen=True)
class CommissionPolicy:
    """What an order costs before the first share is bought.

    Both parts are per-order, which is what makes a *quantity* decision depend
    on them: a fixed fee of 5 currency units on a 40-unit notional is a 12.5%
    cost, and sizing as though it were free overstates the position the account
    can afford.
    """

    fixed: float = 0.0
    per_share: float = 0.0
    minimum: float = 0.0

    def for_quantity(self, qty: int, price: float) -> float:
        """The cost of trading ``qty`` at ``price``."""
        variable = max(0.0, self.fixed) + max(0.0, self.per_share) * max(0, qty)
        return max(variable, max(0.0, self.minimum))


@dataclass(frozen=True)
class AuthorizedSizing:
    """The quantity the book may trade, and which limit decided it."""

    qty: int
    entry: float
    stop: float
    risk_per_share: float
    atr_multiple: float | None
    position_risk: float
    notional: float
    portfolio_weight: float | None
    commission: float
    binding_constraint: str | None
    limits: dict[str, int | None] = field(default_factory=dict)
    reasons: tuple[SizingReason, ...] = field(default_factory=tuple)

    @property
    def authorized(self) -> bool:
        """Whether an order may be created for this quantity.

        A zero quantity is not an authorisation, whatever the reason.
        """
        return self.qty > 0 and not self.reasons

    def summary(self) -> dict:
        return {
            "qty": self.qty,
            "authorized": self.authorized,
            "entry": self.entry,
            "stop": self.stop,
            "risk_per_share": self.risk_per_share,
            "atr_multiple": self.atr_multiple,
            "position_risk": self.position_risk,
            "notional": self.notional,
            "portfolio_weight": self.portfolio_weight,
            "commission": self.commission,
            "binding_constraint": self.binding_constraint,
            "limits": self.limits,
            "reasons": [str(r) for r in self.reasons],
        }


def engine_stressed_risk(result) -> float:
    """The per-share risk the engine sized against, recovered from its result.

    ``size_with_exposure_cap`` reports ``planned_risk`` and ``qty``, so the
    per-share figure is their quotient — derived rather than recomputed, which
    keeps this module from owning a second copy of the formula.
    """
    if result.qty > 0 and result.planned_risk > 0:
        return result.planned_risk / result.qty
    return 0.0


def _finite(value: float | None) -> bool:
    return value is not None and math.isfinite(value)


def authorized_size(
    *,
    entry: float | None,
    stop: float | None,
    atr: float | None,
    risk_budget: float | None,
    portfolio_equity: float | None,
    available_cash: float | None,
    direction: str = "LONG",
    max_symbol_exposure: float | None = None,
    target_weight: float | None = None,
    existing_notional: float = 0.0,
    held_quantity: float | None = None,
    gap_atr: float = 0.0,
    slippage_pct: float = 0.0,
    commission: CommissionPolicy | None = None,
    min_stop_distance: float = 0.0,
) -> AuthorizedSizing:
    """The quantity an order may carry: ``min`` of the four limits.

    ``held_quantity`` caps a reduction at what the book actually holds — the
    rule the audit states for reducing a long. When it is ``None`` the caller is
    opening, and no position limit applies.

    ``target_weight`` is the share of equity the strategy wants in this
    instrument after the trade. It is enforced *after* the exposure cap: a target
    below the cap tightens it, a target above it changes nothing, because the
    per-symbol policy is the floor.
    """
    fees = commission or CommissionPolicy()
    limits: dict[str, int | None] = {}

    # --- the inputs an authorisation cannot be imagined without -------------
    if not _finite(entry) or entry <= 0:
        return _blocked(SizingReason.INVALID_ENTRY, entry or 0.0, stop or 0.0,
                        limits, risk_budget)
    assert entry is not None
    if not _finite(stop) or stop <= 0:
        return _blocked(SizingReason.INVALID_STOP, entry, stop or 0.0,
                        limits, risk_budget)
    assert stop is not None

    upper = direction.upper()
    if upper not in ("LONG", "SHORT"):
        return _blocked(SizingReason.INVALID_DIRECTION, entry, stop, limits,
                        risk_budget)
    # Direction coherence: a long's stop is below the entry, a short's above.
    if upper == "LONG" and stop >= entry:
        return _blocked(SizingReason.INVALID_STOP, entry, stop, limits, risk_budget)
    if upper == "SHORT" and stop <= entry:
        return _blocked(SizingReason.INVALID_STOP, entry, stop, limits, risk_budget)

    risk_per_share = abs(entry - stop)
    if min_stop_distance > 0 and risk_per_share < min_stop_distance:
        return _blocked(SizingReason.INVALID_STOP, entry, stop, limits, risk_budget)

    if not _finite(risk_budget) or risk_budget <= 0:
        return _blocked(SizingReason.RISK_BUDGET_MISSING, entry, stop, limits,
                        risk_budget)
    assert risk_budget is not None

    # --- risk and exposure limits: the engine's own function ---------------
    # Delegated, not reimplemented. `size_with_exposure_cap` is the path the
    # Execution Gate already calls, and a second copy of the stop/exposure
    # arithmetic would be a second authorisation path — exactly what §Task E of
    # P5 forbids. This module adds the cash and position limits it lacks.
    from tradingagents.dataflows.risk_engine import (
        RiskReason,
        size_with_exposure_cap,
    )

    engine = size_with_exposure_cap(
        entry=entry, stop=stop, atr=atr or 0.0, risk_budget=risk_budget,
        equity=portfolio_equity, max_symbol_exposure=max_symbol_exposure,
        direction=upper, cash=None,
        existing_notional=existing_notional, gap_atr=gap_atr,
        slippage_pct=slippage_pct,
    )
    if RiskReason.PORTFOLIO_CONTEXT_MISSING in engine.reasons:
        return _blocked(SizingReason.PORTFOLIO_EQUITY_MISSING, entry, stop,
                        limits, risk_budget)
    if RiskReason.EXPOSURE_CAP_INVALID in engine.reasons:
        return _blocked(SizingReason.TARGET_ALLOCATION_EXCEEDED, entry, stop,
                        limits, risk_budget)
    if RiskReason.RISK_BUDGET_MISSING in engine.reasons:
        return _blocked(SizingReason.RISK_BUDGET_MISSING, entry, stop,
                        limits, risk_budget)

    stressed_risk = engine_stressed_risk(engine)
    qty_risk = (int(math.floor(risk_budget / stressed_risk))
                if stressed_risk > 0 else 0)
    limits["risk_limited_qty"] = qty_risk
    qty_exposure: int | None = None
    if max_symbol_exposure is not None and portfolio_equity and portfolio_equity > 0:
        room = portfolio_equity * max_symbol_exposure - max(0.0, existing_notional)
        qty_exposure = max(0, int(math.floor(room / entry)))
        limits["exposure_limited_qty"] = qty_exposure
        # The engine's own answer is the floor of the two limits.
        limits["engine_qty"] = engine.qty

    # --- target allocation: a stricter, strategy-chosen ceiling -------------
    if target_weight is not None:
        if not _finite(portfolio_equity) or portfolio_equity <= 0:  # type: ignore[operator]
            return _blocked(SizingReason.PORTFOLIO_EQUITY_MISSING, entry, stop,
                            limits, risk_budget)
        target_notional = portfolio_equity * target_weight * (1)  # type: ignore[operator]
        room = target_notional - max(0.0, existing_notional)
        qty_target = max(0, int(math.floor(room / entry)))
        limits["target_limited_qty"] = qty_target
        qty_exposure = (qty_target if qty_exposure is None
                        else min(qty_exposure, qty_target))

    # --- cash limit: what the account can actually pay, fees included -------
    # An unknown cash balance is not an unlimited one. The task lists
    # "available cash" among the inputs an authorisation requires, so absent
    # cash blocks rather than silently dropping the limit.
    if available_cash is None or not _finite(available_cash) or available_cash < 0:
        return _blocked(SizingReason.AVAILABLE_CASH_MISSING, entry, stop,
                        limits, risk_budget)
    qty_cash: int | None = None
    if available_cash is not None:
        cash = float(available_cash)
        # Solve qty * entry + commission(qty) <= cash for the largest integer.
        per_unit = entry + max(0.0, fees.per_share)
        fixed = max(0.0, fees.fixed)
        if per_unit <= 0:
            qty_cash = 0
        else:
            qty_cash = max(0, int(math.floor((cash - fixed) / per_unit)))
            # The minimum fee can undercut the estimate for tiny sizes.
            while qty_cash > 0 and qty_cash * entry + fees.for_quantity(qty_cash, entry) > cash:
                qty_cash -= 1
        limits["cash_limited_qty"] = qty_cash

    # --- position limit: never trade more than the book holds ---------------
    qty_position: int | None = None
    if held_quantity is not None:
        if not _finite(held_quantity):
            return _blocked(SizingReason.POSITION_UNKNOWN, entry, stop, limits,
                            risk_budget)
        qty_position = max(0, int(math.floor(abs(float(held_quantity)))))
        limits["position_limited_qty"] = qty_position

    # --- the answer: the most binding limit --------------------------------
    candidates = [(name, value) for name, value in (
        ("RISK_BUDGET", qty_risk),
        ("EXPOSURE_CAP", qty_exposure),
        ("TARGET_ALLOCATION", limits.get("target_limited_qty")),
        ("CASH", qty_cash),
        ("POSITION", qty_position),
    ) if value is not None]

    if not candidates:
        return _blocked(SizingReason.RISK_BUDGET_MISSING, entry, stop, limits,
                        risk_budget, risk_per_share)

    binding_name, qty = min(candidates, key=lambda pair: pair[1])
    if qty <= 0:
        reason = {
            "CASH": SizingReason.CASH_EXCEEDED,
            "EXPOSURE_CAP": SizingReason.TARGET_ALLOCATION_EXCEEDED,
            "TARGET_ALLOCATION": SizingReason.TARGET_ALLOCATION_EXCEEDED,
            "POSITION": SizingReason.NON_POSITIVE_SIZE,
        }.get(binding_name, SizingReason.NON_POSITIVE_SIZE)
        return _blocked(reason, entry, stop, limits, risk_budget, risk_per_share,
                        binding=binding_name)

    notional = qty * entry
    fee = fees.for_quantity(qty, entry)
    planned_risk = qty * (stressed_risk or risk_per_share)
    weight = (notional / portfolio_equity
              if _finite(portfolio_equity) and portfolio_equity else None)
    atr_multiple = (risk_per_share / atr
                    if _finite(atr) and atr and atr > 0 else None)

    return AuthorizedSizing(
        qty=qty, entry=entry, stop=stop, risk_per_share=risk_per_share,
        atr_multiple=atr_multiple, position_risk=planned_risk,
        notional=notional, portfolio_weight=weight, commission=fee,
        binding_constraint=binding_name, limits=limits,
    )


def _blocked(reason: SizingReason, entry: float, stop: float,
             limits: dict[str, int | None], risk_budget: float | None,
             risk_per_share: float | None = None,
             *, binding: str | None = None) -> AuthorizedSizing:
    """A zero quantity with the reason, never a guess at a plausible size."""
    risk_per_share = (risk_per_share if risk_per_share is not None
                      else abs(entry - stop) if entry and stop else 0.0)
    return AuthorizedSizing(
        qty=0, entry=entry, stop=stop, risk_per_share=risk_per_share,
        atr_multiple=None, position_risk=0.0, notional=0.0,
        portfolio_weight=None, commission=0.0,
        binding_constraint=binding, limits=limits, reasons=(reason,),
    )

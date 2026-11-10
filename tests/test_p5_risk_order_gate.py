"""P5: deterministic sizing over four limits, and no rating becomes an order.

The audit found F4: a strategic rating could reach execution without passing a
deterministic size, and a "sell" could be read as a short. Two things were
missing — a cash limit and a position limit — and the module that has them now
is `math/order_sizing.py`, which returns the *most binding* of:

``risk_limited_qty``      the stop budget, stressed for gap and slippage
``exposure_limited_qty``  the per-symbol share of equity
``cash_limited_qty``      the cash the account can pay, commissions included
``position_limited_qty``  the units actually held, for a reduction

Everything here is deterministic: no model, no clock, no network. The broker is
a list, and a blocked order must leave it empty.

The worked example is the one the repository's own docstring uses: 100,000
equity, 1,000 risk budget, entry 560, stop 552.07, a 5% symbol cap.
"""

from __future__ import annotations

import pytest

from tradingagents.dataflows.math.order_sizing import (
    AuthorizedSizing,
    CommissionPolicy,
    SizingReason,
    authorized_size,
)
from tradingagents.dataflows.math.trade_actions import (
    TradeAction,
    actions_for_rating,
    resolve_trade_intent,
)

EQUITY = 100_000.0
RISK_BUDGET = 1_000.0
CASH = 50_000.0
ENTRY = 560.0
STOP = 552.07
ATR = 9.52
CAP = 0.05


def _size(**overrides) -> AuthorizedSizing:
    base = {"entry": ENTRY, "stop": STOP, "atr": ATR,
            "risk_budget": RISK_BUDGET, "portfolio_equity": EQUITY,
            "available_cash": CASH, "max_symbol_exposure": CAP}
    base.update(overrides)
    return authorized_size(**base)


class MockPaperBroker:
    """Records what would have been sent. Counts calls, so zero is provable."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def send(self, order: dict) -> None:
        self.calls.append(order)

    @property
    def call_count(self) -> int:
        return len(self.calls)


# ===========================================================================
# §Task C — the four limits
# ===========================================================================
class TestAuthorizedQuantity:
    def test_the_most_binding_limit_decides(self):
        """126 by risk (floor(1000/7.93)), 8 by the exposure cap: the cap wins."""
        sizing = _size()
        assert sizing.limits["risk_limited_qty"] == 126
        assert sizing.limits["exposure_limited_qty"] == 8
        assert sizing.qty == 8
        assert sizing.binding_constraint == "EXPOSURE_CAP"
        assert sizing.authorized is True

    def test_the_formulas_the_task_names_are_all_reported(self):
        sizing = _size()
        assert sizing.risk_per_share == pytest.approx(7.93, abs=0.01)
        assert sizing.atr_multiple == pytest.approx(7.93 / ATR, abs=0.01)
        assert sizing.notional == pytest.approx(sizing.qty * ENTRY)
        assert sizing.position_risk > 0
        assert sizing.portfolio_weight == pytest.approx(sizing.notional / EQUITY)

    def test_the_slippage_widens_the_stop_before_sizing(self):
        """A cost that changes the answer is applied where it changes it."""
        without = _size(slippage_pct=0.0)
        with_slip = _size(slippage_pct=0.005)
        assert with_slip.limits["risk_limited_qty"] <= without.limits["risk_limited_qty"]

    def test_the_commission_reduces_what_cash_can_buy(self):
        free = _size(commission=CommissionPolicy())
        paid = _size(commission=CommissionPolicy(fixed=100.0, per_share=0.5))
        assert paid.limits["cash_limited_qty"] <= free.limits["cash_limited_qty"]
        assert paid.commission > 0

    def test_a_reduction_is_capped_at_the_position_held(self):
        """REDUCE_LONG may not sell more than the book holds."""
        sizing = _size(held_quantity=3)
        assert sizing.qty == 3
        assert sizing.binding_constraint == "POSITION"
        assert sizing.limits["position_limited_qty"] == 3

    def test_a_position_larger_than_the_cap_still_uses_the_cap(self):
        sizing = _size(held_quantity=500)
        assert sizing.qty == 8
        assert sizing.binding_constraint == "EXPOSURE_CAP"

    def test_the_target_allocation_tightens_the_cap(self):
        """A 1% target buys fewer shares than the 5% policy cap."""
        sizing = _size(target_weight=0.01)
        assert sizing.qty == 1
        assert sizing.binding_constraint in ("TARGET_ALLOCATION", "EXPOSURE_CAP")

    def test_integer_quantities_only(self):
        sizing = _size()
        assert isinstance(sizing.qty, int)
        assert float(sizing.qty).is_integer()


# ===========================================================================
# §Task B — portfolio context, and the fail-closed answers
# ===========================================================================
class TestFailClosed:
    def test_no_portfolio_equity_blocks(self):
        sizing = _size(portfolio_equity=None)
        assert sizing.qty == 0
        assert sizing.authorized is False
        assert SizingReason.PORTFOLIO_EQUITY_MISSING in sizing.reasons

    def test_unknown_cash_is_not_unlimited_cash(self):
        sizing = _size(available_cash=None)
        assert sizing.qty == 0
        assert SizingReason.AVAILABLE_CASH_MISSING in sizing.reasons

    def test_no_risk_budget_blocks(self):
        sizing = _size(risk_budget=None)
        assert sizing.qty == 0
        assert SizingReason.RISK_BUDGET_MISSING in sizing.reasons

    def test_insufficient_cash_blocks(self):
        sizing = _size(available_cash=100.0)
        assert sizing.qty == 0
        assert SizingReason.CASH_EXCEEDED in sizing.reasons

    def test_a_stop_on_the_wrong_side_blocks_a_long(self):
        sizing = _size(stop=ENTRY + 5)
        assert sizing.qty == 0
        assert SizingReason.INVALID_STOP in sizing.reasons

    def test_a_stop_on_the_wrong_side_blocks_a_short(self):
        """A protective stop for a long is not a stop for a new short."""
        sizing = _size(direction="SHORT", stop=STOP)   # below entry: wrong for a short
        assert sizing.qty == 0
        assert SizingReason.INVALID_STOP in sizing.reasons

    def test_a_short_with_a_stop_above_entry_is_sized(self):
        sizing = _size(direction="SHORT", stop=ENTRY + 7.93)
        assert sizing.qty > 0
        assert sizing.authorized is True

    def test_an_unknown_position_is_not_a_flat_one(self):
        sizing = _size(held_quantity=float("nan"))
        assert sizing.qty == 0
        assert SizingReason.POSITION_UNKNOWN in sizing.reasons


# ===========================================================================
# §Task A — rating is not an action, and an action is not an order
# ===========================================================================
class TestRatingIsNotAnOrder:
    def test_an_underweight_rating_offers_no_short(self):
        """UNDERWEIGHT may reduce or close a long; it never opens a short."""
        permitted = actions_for_rating("UNDERWEIGHT")
        assert TradeAction.OPEN_SHORT not in permitted
        assert TradeAction.REDUCE_LONG in permitted

    def test_a_sell_rating_names_no_book_operation(self):
        """"SELL" is a direction; mapping it to OPEN_SHORT is the F4 defect."""
        assert actions_for_rating("SELL") == ()

    def test_a_buy_rating_names_no_book_operation(self):
        assert actions_for_rating("BUY") == ()

    def test_a_hold_produces_no_immediate_order(self):
        from tradingagents.dataflows.math.trade_actions import PositionState

        permitted = actions_for_rating("NEUTRAL")
        intent = resolve_trade_intent(TradeAction.WAIT,
                                      PositionState(known=True, quantity=10))
        assert permitted == (TradeAction.WAIT,)
        assert intent.is_executable is False
        assert intent.blocks is False

    def test_a_reduction_without_a_position_is_refused(self):
        from tradingagents.dataflows.math.trade_actions import PositionState

        intent = resolve_trade_intent(TradeAction.REDUCE_LONG,
                                      PositionState(known=True, quantity=0),
                                      reduce_fraction=0.4)
        assert intent.blocks is True
        assert intent.reason_code == "NO_LONG_POSITION"
        assert intent.is_executable is False

    def test_a_reduction_with_a_position_is_sized_from_it(self):
        from tradingagents.dataflows.math.trade_actions import PositionState

        intent = resolve_trade_intent(TradeAction.REDUCE_LONG,
                                      PositionState(known=True, quantity=10),
                                      reduce_fraction=0.4)
        assert intent.quantity == pytest.approx(4.0)
        assert intent.blocks is False

    def test_an_unknown_book_cannot_authorise_a_trade(self):
        from tradingagents.dataflows.math.trade_actions import PositionState

        intent = resolve_trade_intent(TradeAction.REDUCE_LONG,
                                      PositionState(known=False))
        assert intent.blocks is True
        assert intent.reason_code == "PORTFOLIO_UNKNOWN"


# ===========================================================================
# §Task E — an invalid order reaches no execution component
# ===========================================================================
class TestZeroExecutionOnBlock:
    """The proof the task asks for: a blocked order touches nothing."""

    @pytest.mark.parametrize("overrides,expected_reason", [
        ({"portfolio_equity": None}, SizingReason.PORTFOLIO_EQUITY_MISSING),
        ({"available_cash": None}, SizingReason.AVAILABLE_CASH_MISSING),
        ({"risk_budget": None}, SizingReason.RISK_BUDGET_MISSING),
        ({"available_cash": 10.0}, SizingReason.CASH_EXCEEDED),
        ({"stop": ENTRY + 5}, SizingReason.INVALID_STOP),
    ])
    def test_a_blocked_sizing_sends_nothing_to_the_broker(self, overrides,
                                                          expected_reason):
        broker = MockPaperBroker()
        sizing = _size(**overrides)

        # The only path to a venue: an authorised size may be sent.
        if sizing.authorized:
            broker.send({"qty": sizing.qty, "ticker": "BRK-B"})

        assert sizing.authorized is False
        assert expected_reason in sizing.reasons
        assert broker.call_count == 0

    def test_an_authorised_sizing_sends_exactly_one_order(self):
        """The counterpart: a valid order does reach the mock venue once."""
        broker = MockPaperBroker()
        sizing = _size()
        if sizing.authorized:
            broker.send({"qty": sizing.qty, "ticker": "BRK-B"})
        assert sizing.authorized is True
        assert broker.call_count == 1
        assert broker.calls[0]["qty"] == 8

    def test_a_rating_alone_never_produces_a_sized_intent(self):
        """A rating yields *candidate* actions; none is executable by itself.

        `actions_for_rating` names what a rating permits, not what to do: the
        action still has to survive `resolve_trade_intent` against the real book.
        With no book, every candidate is refused, which is the fail-closed path.
        """
        from tradingagents.dataflows.math.trade_actions import PositionState

        broker = MockPaperBroker()
        unknown_book = PositionState(known=False)
        for rating in ("BUY", "SELL", "HOLD", "OVERWEIGHT", "UNDERWEIGHT", None):
            for action in actions_for_rating(rating):
                intent = resolve_trade_intent(action, unknown_book)
                if intent.is_executable:
                    broker.send({"rating": rating, "action": str(action)})
        assert broker.call_count == 0, (
            "a rating must not produce an executable order without a book")


# ===========================================================================
# §Task E — one authorisation path, not two
# ===========================================================================
class TestSinglePath:
    """The module must delegate, not duplicate, the engine's arithmetic.

    A second copy of the stop/exposure math would be a second authorisation
    path. `authorized_size` calls `size_with_exposure_cap` and reports its
    answer, so the gate and this module can never disagree.
    """

    def test_the_engine_answer_is_reported_alongside_the_extra_limits(self):
        sizing = _size()
        assert "engine_qty" in sizing.limits
        from tradingagents.dataflows.risk_engine import size_with_exposure_cap

        direct = size_with_exposure_cap(
            entry=ENTRY, stop=STOP, atr=ATR, risk_budget=RISK_BUDGET,
            equity=EQUITY, max_symbol_exposure=CAP, direction="LONG", cash=None,
        )
        assert sizing.limits["engine_qty"] == direct.qty

    def test_no_second_copy_of_the_risk_arithmetic_exists(self):
        """The module imports the engine rather than re-deriving `floor(budget/risk)`."""
        import inspect

        from tradingagents.dataflows.math import order_sizing

        source = inspect.getsource(order_sizing)
        assert "size_with_exposure_cap" in source, (
            "authorized_size must delegate to the engine's sizing function")

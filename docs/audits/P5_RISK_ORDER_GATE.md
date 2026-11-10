# P5 — Deterministic position sizing and order authorisation (F4)

Scope: `fix/p5-risk-order-gate`, branched from `custom` at `7721e83`
(phases 1–4 merged).

## 1. What already existed

`risk_engine.py` had `size_position`, `size_with_exposure_cap`,
`validate_stop_side`, `stress_exit` and the tranche/aggregate risk functions.
`math/trade_actions.py` already separated the rating from the action: an
`UNDERWEIGHT` rating offers `REDUCE_LONG`/`CLOSE_LONG`/`WAIT` and never
`OPEN_SHORT`, and a bare `SELL` or `BUY` returns no action at all. The Execution
Gate and the idempotent `PaperOrder` were in place.

Tasks A, D and E were largely covered. Two limits were missing, which is F4.

## 2. Root cause

`size_with_exposure_cap` enforces **two** limits: the stop-distance budget and
the per-symbol exposure cap. The task requires four:

| Limit | Was it enforced? |
|---|---|
| `risk_limited_qty` | yes, with gap and slippage |
| `exposure_limited_qty` | yes |
| `cash_limited_qty` | **no** — cash was an all-or-nothing check after sizing |
| `position_limited_qty` | **no** — `resolve_trade_intent` caps a reduction, but the sizing path did not |

A plan could therefore be authorised whose notional the account could not pay
for (the cash check fired only once a quantity existed, and only when `cash` was
passed), and a reduction had no limit rooted in the held quantity at the sizing
stage.

## 3. Changes

| File | Change |
|---|---|
| `math/order_sizing.py` | New. `authorized_size()`, `CommissionPolicy`, `AuthorizedSizing`, `SizingReason`. Four limits, the minimum, and the binding one named. Delegates the risk/exposure arithmetic to `risk_engine.size_with_exposure_cap`. |
| `tests/test_p5_risk_order_gate.py` | 32 tests with a mock broker. |

No second engine: the module imports the existing one. A test
(`test_no_second_copy_of_the_risk_arithmetic_exists`) asserts that the
delegation is in the source, so a future rewrite cannot quietly duplicate it.

## 4. The order contracts

**Strategic rating** (level 1) — unchanged, `--` not a trade action:
`BUY`, `HOLD`, `SELL`, `OVERWEIGHT`, `UNDERWEIGHT`.

**Trade proposal** (level 2) — `math/trade_actions.TradeAction`:
`OPEN_LONG`, `REDUCE_LONG`, `CLOSE_LONG`, `OPEN_SHORT`, `REDUCE_SHORT`,
`CLOSE_SHORT`, `WAIT`.

**Authorized order** (level 3) — `AuthorizedSizing`, the quantity the code
validated:

```python
AuthorizedSizing(
    qty, entry, stop, risk_per_share, atr_multiple,
    position_risk, notional, portfolio_weight, commission,
    binding_constraint, limits, reasons,
)
```

`authorized` is `qty > 0 and not reasons` — a zero quantity is never an
authorisation.

## 5. Worked example

100,000 equity · 1,000 risk budget · 50,000 cash · entry 560 · stop 552.07 ·
ATR 9.52 · 5% symbol cap · 5.00 fixed commission:

```console
qty: 8   binding: EXPOSURE_CAP
limits: risk_limited_qty=121  exposure_limited_qty=8
        cash_limited_qty=89   engine_qty=8
risk_per_share: 7.93   atr_multiple: 0.833
notional: 4480.0   portfolio_weight: 0.0448   commission: 5.0
```

The risk budget alone would buy 121 shares — about 67,800 USD, 68% of the
portfolio in one ticker. The exposure cap binds at 8. That is the policy working,
and the audit now names it.

## 6. Fail-closed proof (the real gate, not a stub)

```console
WITH portfolio  -> action: PROPOSE_PAPER_ORDER | order: True
NO portfolio    -> action: BLOCK | order: False
   reasons: ['RISK_PLAN_BLOCKED', 'PORTFOLIO_RISK_BUDGET_MISSING']
```

And on the sizing module directly:

| Situation | Result |
|---|---|
| no equity | `PORTFOLIO_EQUITY_MISSING`, qty 0 |
| no cash | `AVAILABLE_CASH_MISSING`, qty 0 |
| cash 100 | `CASH_EXCEEDED`, qty 0 |
| no risk budget | `RISK_BUDGET_MISSING`, qty 0 |
| stop above entry on a LONG | `INVALID_STOP`, qty 0 |
| stop below entry on a SHORT | `INVALID_STOP`, qty 0 |
| unknown held quantity | `POSITION_UNKNOWN`, qty 0 |

In every one of those cases the mock broker's call count is asserted to be
**zero**.

## 7. Test results

```console
$ pytest tests/test_p5_risk_order_gate.py -q
32 passed

$ pytest tests/ -q
4 failed, 2411 passed, 12 skipped, 100 subtests passed
```

The four failures are **pre-existing**: `test_financial_math_engine.py::TestProperties`
needs the `hypothesis` package, not installed.

Covered: HOLD with no order, REDUCE_LONG with and without a position, SELL not
turned into a SHORT, a valid LONG stop, a wrong-side SHORT stop, missing
portfolio context, insufficient capital, target allocation exceeded, quantity
above the limit, trigger not met, unverified critical metric, a valid order
authorised, an invalid order blocked, and zero broker calls on every block.

`ruff check .`: clean.

## 8. What this does not do

The repository contains an **authorisation gate and a paper-order ledger**, not a
broker integration. No order was sent anywhere — the mock broker in the tests is
a list. Nothing here claims broker execution was verified.

The LLM strategy logic and the risk-engine formulas are unchanged; this adds the
cash and position limits around them.

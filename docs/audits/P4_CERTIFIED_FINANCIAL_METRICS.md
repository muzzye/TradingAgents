# P4 — Certified valuation and deterministic math (F2, F3)

Scope: `fix/p4-certified-financial-metrics`, branched from `custom` at `2752284`
(phases 1–3 merged).

## 1. What already existed

The math engine was not missing. `custom` already had `math/valuation.py`
(`earnings_per_share`, `price_to_book`, `book_value_per_share`,
`dual_class_book_value_per_share`), `dataflows/valuation.py`
(`reconcile_valuation`), the Numeric Claims Validator (`math/claims.py`), the
`MetricRegistry` and `annualize()` — verified: `annualize(0.78, 4) = 3.1200`,
exactly the task's regression figure.

No new engine was written. Three gaps remained.

## 2. Root cause of the gaps

### G1 — the two P/B forms were one metric (`pb_aggregate` was an operand)

`market_cap / equity` appeared only as `operands["market_cap_over_equity"]`
inside the per-share record. Registering one figure is how **1.62x and 1.7x
could both be quoted as "the" P/B of a run**: the aggregate form had no record of
its own to compare against, and no tolerance was being applied because nothing
was comparing them.

### G2 — the report was not built from the records

`write_report_tree` wrote the agents' prose into sections. The registry's
`MetricRecord`s reached the audit JSON but never the report, so a table in a
report could state 1.7x while the registry said 1.6041x. `attach_registry_status`
stored only a *summary* (`metrics_total`, `metrics_usable`), not the records.

### G3 — an annualisation was never checked

`annualize()` existed and was correct; nothing compared a stated annual rate to
it.

## 3. Changes

| File | Change |
|---|---|
| `math/run_registry.py` | `pb_aggregate` as its own metric (`market_cap_div_attributable_equity`); `pb_reconciliation` recording the two forms' agreement; `RunInputs.market_cap_reported` and `.equity_period_end`; both read from state. Fixed `_register_eps_reconciliation` ignoring `claimed_inputs`. |
| `dataflows/certified_report.py` | New. `render_metric_table`, `certified_table_markdown`, `append_certified_table` — the tables built from records. |
| `dataflows/gate_integration.py` | `attach_registry_status` now also puts the registry itself on the state. |
| `tradingagents/reporting.py` | `write_report_tree` inserts the certified table after the header. |
| `math/period_labels.py` | `check_annualization`, `AnnualizationVerdict`. |
| `tests/test_p4_certified_metrics.py` | 23 tests, including the real graph path. |

## 4. Metrics generated and their formula IDs

| Metric | Formula ID |
|---|---|
| `pb_aggregate` | `market_cap_div_attributable_equity` |
| `price_to_book` | `price_div_bvps` |
| `pb_reconciliation` | `pb_forms_agreement` |
| `book_value_per_share_B` | `equity_div_class_b_equivalent_shares` |
| `eps_gaap` | `net_income_div_shares_gaap` |
| `pe_trailing` | `price_div_eps_trailing_gaap` |
| `consolidated_revenue` | `sec_filed_fact` |

## 5. Example — a certified P/B

The task's fixture, through the **real graph path** (`record_decision` →
`_evaluate_reliability` → registry → report):

```console
equity 761,368,000,000 · market cap 1,231,440,000,000 · price 553.46 · A:B 1:1500

  pb_aggregate        1.6174    VERIFIED  period=2026-09-30
  price_to_book       1.6174    VERIFIED
  pb_reconciliation   0.0010 %  VERIFIED
```

`1,231.44 / 761.368 = 1.6174` — matching the task's indicative figure.

## 6. Example — the disagreement, recorded not averaged

A run whose class-B price (576.74) implies a different market cap than the report
states (1,231.44bn):

```console
  pb_aggregate        1.6174    VERIFIED
  price_to_book       1.6854    VERIFIED
  pb_reconciliation   4.0374 %  REFUSED
    "the two P/B formulas differ by 4.04%: the aggregate and per-share forms do
     not rest on the same snapshot, so neither may be quoted as the definitive P/B"
```

This is the 1.62x / 1.7x case: the two numbers exist and are shown, and the
registry says they do not agree.

## 7. Example — a rejected EPS

```console
  eps_gaap      —    UNSUPPORTED
    "the trailing EPS was reported but the weighted-average share count is not
     available, so it cannot be reconciled against the filing"
  pe_trailing   —    UNSUPPORTED
```

And a reconciled one:

```console
  eps_gaap      39.76   VERIFIED      (weighted_average_shares 1.437bn)
```

The vendor figure is never certified merely because it came from Yahoo: without
the denominator it is `UNSUPPORTED`, and a P/E built on it inherits that state.

## 8. Example — an intercepted claim

A model-authored EPS:

```console
  eps_gaap   39.76   usable=False   source=LLM_CLAIMED
    "the trailing EPS is the model's own assertion, not a figure from the filing"
```

An annualisation:

```console
  check_annualization(3.12, quarterly_pct=0.78, periods_per_year=4)
    -> CONSISTENT, computed 3.1200%
  check_annualization(1.2,  quarterly_pct=0.78)
    -> FORMULA_MISSING, blocks=True
  check_annualization(1.2,  quarterly_pct=0.78, periods_per_year=4)
    -> MISMATCH, "the stated 1.2% does not follow from 0.78% x 4 = 3.1200%"
```

## 9. Test results

```console
$ pytest tests/test_p4_certified_metrics.py -q
23 passed

$ pytest tests/ -q
4 failed, 2381 passed, 12 skipped, 100 subtests passed
```

The four failures are **pre-existing**: `test_financial_math_engine.py::TestProperties`
needs `hypothesis`, which is not installed.

Covered: aggregate P/B, per-share P/B, A/B conversion, temporal mismatch, EPS
reconciled, EPS unverified, buyback annualisation, incompatible claim,
propagation to the Portfolio Manager, and an executable decision blocked by
invalid metrics.

`ruff check .`: clean.

## 10. Not changed

The LLM strategy logic, the risk engine and the Execution Gate are untouched.
No broker operation; no order was created or sent.

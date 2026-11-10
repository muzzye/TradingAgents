# P2 — SEC XBRL semantic reconciliation (F1)

Scope: `fix/p2-sec-revenue-semantics`, branched from `custom` at `0541d4c`.
Corrects F1: a revenue *component* presented as the consolidated total.

## 1. What was already correct

The resolver in `tradingagents/dataflows/vendors/sec_edgar.py` already ordered
candidate tags by **meaning** before spelling, through
`vendors/concept_resolution.py`. For Berkshire Q3 2026 that ordering alone
returns the consolidated figure:

```console
BOTH_SCOPES -> {'2025-09-30': 94,972,000,000  '2026-09-30': 105,647,000,000}
```

Both periods come from `Revenues`, both `VERIFIED`. The audit's original
symptom — 74,322 presented as the total — did not reproduce on this payload.

So the fix is not the ordering. Two gaps remained, and each is a way the same
defect can still reach a report.

## 2. Root cause of the remaining gaps

### G1 — a row could mix scopes across periods

`_as_of_with_provenance` filled a row period by period: it took the first tag
(by preference order) that reported *any* period, then let a different tag fill
the periods the first one lacked. Reproduced:

```console
# consolidated filed for Q3 2026, only the component filed for Q3 2025
row -> {'2025-09-30': 60,000,000,000  [contract]  '2026-09-30': 105,647,000,000  [consolidated]}
YoY printed: 76.08%      (the consolidated history says 11.24%)
```

One row measured two different things and the growth across it was meaningless.

### G2 — the concept never reached the certified metric

`TagProvenance` carried concept, scope, accession, period, unit and dimensions.
It did **not** carry the CIK, the scale, the availability date or a validation
status, and nothing passed it into `RunInputs`. So a number derived from a
component and a number derived from the consolidated total entered the metric
registry identically: both `VERIFIED` if the arithmetic worked. Promoting 74,322
to `consolidated_revenue` was therefore possible by supplying it.

## 3. Changes

| File | Change |
|---|---|
| `vendors/concept_resolution.py` | `TagProvenance` gains `cik`, `scale`, `available_at`, `origin` and a `validation_status` property (`VERIFIED` only for a consolidated, non-dimensioned concept). `summary()` reports them. |
| `vendors/sec_edgar.py` | `_as_of_with_provenance` computes the periods each scope reports **before** taking values, then drops a narrower tag that reports a period the consolidated line also reports. `cik` is threaded from `_statement`. Provenance records the filing date as `available_at`. |
| `dataflows/math/run_registry.py` | `RunInputs.revenue_provenance`; a `consolidated_revenue` metric with period and provenance; `_revenue_source_of` refuses to certify a figure whose `validation_status` is `UNVERIFIED`. |
| `tests/fixtures/sec_revenue_payloads.py` | Ten frozen payloads, one hazard each. |
| `tests/test_p2_sec_revenue_semantics.py` | 22 tests over the fixtures. |

### The rule, and why it is derived rather than listed

A tag is a *component of a row* only when a broader tag reports the **same
period**. This is what the payload can prove:

- **Berkshire**: `Revenues` and `RevenueFromContractWithCustomerExcludingAssessedTax`
  both report Q3 2026, so the latter is a component and is dropped from the row.
- **Apple**: `Revenues` stopped in 2015, `RevenueFromContractWith...` began in
  2018, so they never overlap and the second is the row's own history, kept.

A hardcoded classification cannot express this: the *same* XBRL tag is Apple's
whole top line and Berkshire's component. An earlier attempt in this branch used
a fixed `_REQUIRED_SCOPE = {"Revenue": "consolidated"}` and broke Apple; two
existing tests caught it
(`test_the_quarter_is_not_confused_with_the_year_to_date`,
`test_older_periods_fall_back_to_the_tag_the_filer_used_then`). It was removed in
favour of the overlap rule.

## 4. Example — the certified metric with its XBRL provenance

```python
registry = build_run_registry(RunInputs(
    revenue_consolidated=105_647_000_000.0,
    revenue_prior=94_972_000_000.0,
    revenue_provenance={
        "concept": "Revenues",
        "cik": "0001067983",
        "accession": "0001067983-26-000042",
        "period_start": "2026-07-01", "period_end": "2026-09-30",
        "unit": "USD", "scale": 1, "origin": "sec_companyfacts",
        "available_at": "2026-10-25", "validation_status": "VERIFIED",
    },
), run_id="brkb-q3")
```

```console
consolidated_revenue: 105,647,000,000  VERIFIED  usable=True   period 2026-09-30
revenue_growth:       11.24 %          usable=True
```

## 5. Example — the promotion refused

The same path with the component, as the defective extractor would have supplied
it:

```python
registry = build_run_registry(RunInputs(
    revenue_consolidated=74_322_000_000.0,
    revenue_provenance={"concept": "RevenueFromContractWithCustomerExcludingAssessedTax",
                        "validation_status": "UNVERIFIED"},
), run_id="brkb-contract")
```

```console
consolidated_revenue: 74,322,000,000  usable=False  source=LLM_CLAIMED
revenue_growth:                       usable=False
```

The value stays in the registry — the audit can show *what* was offered and
*why* it was refused — and `is_usable` is false, so no report can quote it as the
consolidated total and no gate can certify a decision on it.

## 6. Test results

```console
$ pytest tests/test_p2_sec_revenue_semantics.py -q
22 passed

$ pytest tests/test_p2_financial_data_integrity.py tests/test_sec_edgar.py \
         tests/test_p4_e2e_brkb.py tests/test_p4_negative.py -q
86 passed, 2 skipped
```

The two skips need the real SEC cache (`/root/.tradingagents/...`), absent here;
those assertions are covered by the frozen fixtures.

```console
$ pytest tests/ -q
4 failed, 2325 passed, 12 skipped, 100 subtests passed
```

The four failures are **pre-existing**: `test_financial_math_engine.py::TestProperties`
needs the `hypothesis` package, which is not installed. Verified against clean
`custom` with `git stash` — the same four fail with `ModuleNotFoundError`.

`ruff check` on the changed files: clean.

## 7. Not changed

The AI strategies, the risk engine and the Execution Gate are untouched. The
gate still fails closed: the only new behaviour it sees is a metric that is now
*less* likely to be certified, never more.

No live broker access; no order was created or sent.

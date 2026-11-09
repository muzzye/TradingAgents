# Data Lineage Findings — BRK-B

Six findings, each with a first defective point and evidence. Causes are
classified only where code and execution evidence support them; anything else is
marked `INSUFFICIENT_EVIDENCE`.

Suggested fixes are **described, not implemented**.

---

## F1 — Consolidated revenue replaced by a subset

| Field | Value |
|---|---|
| **Severity** | P0 — wrong financial statement |
| **Metric** | Q3 2026 consolidated revenue |
| **First defective point** | `tradingagents/dataflows/vendors/sec_edgar.py:58` |
| **Mechanism** | `sec_edgar.py:167` (`_as_of`) |
| **Cause** | `METRIC_DEFINITION_MISMATCH` |

The income-statement tag list puts the narrower concept first:

```python
("Revenue", ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
             "SalesRevenueNet")),
```

and the resolver keeps the first tag that reports a period:

> `# Tags are tried in order and a period keeps the first one that reports it`

Both figures exist in the filing for Q3 2026; the extractor takes 74,322 and
never consults 105,647.

**Evidence:** live SEC payload shows both values for `2026-07-01..2026-09-30`;
report states "record trimestrale a 74,3 mld (+16,6% a/a)"; consolidated truth is
105,647 vs 94,972 = +11.24%.

**Suggested correction (not implemented):** order the tags broadest-first
(`Revenues`, `SalesRevenueNet`, then the contract family), or record which tag
produced each figure so the consumer can judge. Either way the concept name must
survive to the prompt.

---

## F2 — P/B authored by the model, four inconsistent values

| Field | Value |
|---|---|
| **Severity** | P0 — a valuation multiple with no computation behind it |
| **Metric** | Price-to-book |
| **First defective point** | the analyst prompt: no valuation module runs before it |
| **Cause** | `LLM_NUMERIC_HALLUCINATION`; the 0.001x also `CLASS_SHARE_MISMATCH` |

`reconcile_valuation` (`dataflows/valuation.py`) has no caller outside tests:

```
$ grep -rn "reconcile_valuation(" --include=*.py . | grep -v "def \|test"
(no output)
```

The report contains `0,00116`, `~1,35x`, `~1,7x` and `~1,73x`. None reproduces
from a single consistent input pair. The 0.001x is ≈1.604/1500 — the A/B ratio
applied in the wrong direction.

**Suggested correction (not implemented):** compute the multiple in code before
the analyst runs and pass it as a `MetricRecord`; refuse a class-mismatched
comparison at that boundary.

---

## F3 — EPS TTM accepted without GAAP reconciliation

| Field | Value |
|---|---|
| **Severity** | P1 — a vendor figure treated as a certified fact |
| **Metric** | EPS TTM |
| **First defective point** | `dataflows/vendors/yahoo/fundamentals.py` (the vendor field, unnamed in the artefacts) |
| **Cause** | `INSUFFICIENT_EVIDENCE` for provenance; the absence of certification is confirmed |

`earnings_per_share()` exists, records its basis and formula, and is never
called. The report repeats vendor "Discordanze vendor vs SEC" — the model
*noticed* an inconsistency in prose and proceeded anyway.

Implied weighted-average shares 1,938,732,394 are consistent with 39.76; the
period-end basis would give 36.40.

**Suggested correction (not implemented):** capture the vendor field name and
the accompanying share count; reconcile against the filing's weighted-average
count before the figure is usable in a multiple.

---

## F4 — Position sizing never computed

| Field | Value |
|---|---|
| **Severity** | P0 — no order could be quantified |
| **Metric** | ATR / stop / quantity |
| **First defective point** | `agents/trader/trader.py` — size expressed as a percentage range |
| **Cause** | `VALIDATOR_NOT_CALLED` |

The trader writes `"+4-7pp sull'allocazione standard"` and `"110-120%"`. The
audit JSON records `qty_proposed: null`, `planned_risk: null`,
`exposure_capped_qty: null`. The gate blocked, but for
`DATA_QUALITY_BLOCKED`/`ENTRY_TRIGGER_NOT_MET`, **not** for a missing quantity.

The report's own words show the semantic hazard: *"Overweight equivale a Buy"* —
a strategic rating read as an instruction.

**Suggested correction (not implemented):** require `risk_budget` and `equity`
for any action that would create an order, and let a missing one block with its
own code rather than being masked by another reason.

---

## F5 — Intraday candle presented as a settled close

| Field | Value |
|---|---|
| **Severity** | P0 — every indicator and the entry decision rest on a moving price |
| **Metric** | Last bar |
| **First defective point** | `tradingagents/dataflows/vendors/yahoo/ohlcv.py` — `Close` accepted verbatim |
| **Cause** | `INTRADAY_EOD_MISMATCH` |

The file already documents the hazard at l.152:

> `Yahoo publishes a partial daily candle during market hours, whose` ``Close``
> `is not the closing price, and row inspection cannot tell it from a final one`

The mitigation is a cache TTL, which bounds staleness but cannot make a partial
candle final. All three report times (12:37, 13:24, 15:01 ET) fall inside the
NYSE session, which closes at 16:00.

The report calls it *"l'ultima chiusura verificata"* and derives +24.2%, +15.83%
and every indicator from it.

**Suggested correction (not implemented):** attach an `is_final` flag to the
bar at the vendor boundary and propagate it into the prompt and the audit; let a
confirmed-close strategy refuse a non-final bar.

---

## F6 — Every run now blocks on an unverifiable claim

| Field | Value |
|---|---|
| **Severity** | P0 — the system cannot complete an analysis |
| **Metric** | all labelled figures |
| **First defective point** | `graph/trading_graph.py::_evaluate_reliability` — calls `evaluate_decision` without `metric_registry` |
| **Cause** | `VALIDATOR_BYPASSED` (inverted: the guard fires on absence of data) |

**This is not one of the report's defects — it is introduced by the current
`custom`.** Reproduced exactly as the graph calls it:

```
evaluate_decision("BRK-B", "2026-11-09", state, snapshot_id="probe", trigger_met=True)
-> action: BLOCK
-> reasons: ['DATA_QUALITY_BLOCKED', 'NUMERIC_CLAIM_UNVERIFIABLE']
```

With `registry=None`, every labelled figure becomes `NOT_CHECKABLE`, and
`ReportValidation.blocks_decision` treats a `MISMATCH` as blocking — but the
claims checker marks unverifiable *computed* claims blocking too, so any report
containing a labelled percentage blocks.

**Evidence:** `grep -n "metric_registry=" tradingagents/graph/trading_graph.py`
returns nothing; the probe above.

**Suggested correction (not implemented):** either populate the registry before
the gate runs, or treat "no registry" as "nothing was checked" (reported, not
blocking) as `ReportValidation` was designed to do — the two behaviours
currently disagree.

---

## Findings without sufficient evidence

| Item | Why |
|---|---|
| Which of the four P/B values came from which reasoning step | no intermediate artefacts retained |
| Exact vendor field behind 39.76 | payload not captured |
| Whether the math tools were called at 21:01 | modules not loaded (E1); no tool-call log |
| The 14:05 / 18:37 / 19:24 runs | overwritten by the 21:01 run (§2.1 of the main audit) |

---

## Priority for a targeted fix

1. **F6** — the system currently cannot produce an analysis at all.
2. **F1** — a wrong financial statement reaches the report.
3. **F5** — every indicator and the entry rest on a moving price.
4. **F4** — nothing is quantifiable.
5. **F2**, **F3** — the model authors figures that should be computed.

F6 and F1 are both one-line defects in named functions. F2–F5 need the engine
wired into the run path, which the module structure already supports.

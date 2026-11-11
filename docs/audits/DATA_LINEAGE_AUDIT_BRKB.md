# TradingAgents Data Lineage Audit — BRK-B

**Audit type:** read-only forensic analysis
**Commit analysed:** `f2e7da0a0641ad7cd004259ffc168a773c53a1a7` (`origin/custom`, merge of PR #22)
**Date:** 2026-11-09
**No application code was modified.**

---

## 1. Executive summary

Five metrics were traced from the provider to the final decision. The findings
fall into three groups: defects present in the reports under audit, one defect
introduced by the code now on `custom`, and one class of question the available
evidence cannot answer.

**The arithmetic in the audited reports was produced by the language model, not
by code.** `tradingagents/dataflows/math/` exists and is well tested (120 tests
pass), but the path that produced the 9 November reports did not execute it: the
running service had been started 20 hours before the merge, so the process held
the pre-merge modules in memory. This is proven in §2 and is the single most
important fact in the audit — every metric defect below follows from it.

Confirmed causes:

| # | Metric | First defective point | Cause class |
|---|---|---|---|
| F1 | Q3 revenue 74,322 | `dataflows/vendors/sec_edgar.py:58` — tag priority order | `METRIC_DEFINITION_MISMATCH` |
| F2 | P/B (0.001x / 1.35 / 1.60 / 1.73) | No valuation code in the run path at all | `LLM_NUMERIC_HALLUCINATION` |
| F3 | EPS TTM 39.76 | Vendor figure passed through; no GAAP reconciliation | `INSUFFICIENT_EVIDENCE` (vendor field not captured) |
| F4 | ATR / sizing | `agents/trader/trader.py` — quantities expressed as percentages | `NONE_WIRED` → sizing never computed |
| F5 | Intraday close 576.74 | `dataflows/vendors/yahoo/ohlcv.py` — partial candle accepted as `Close` | `INTRADAY_EOD_MISMATCH` |

One defect **introduced by the current code** (not present in the audited
reports):

| # | Symptom | Point | Cause class |
|---|---|---|---|
| F6 | Every run blocks with `NUMERIC_CLAIM_UNVERIFIABLE` | `gate_integration.py` — no registry supplied | `VALIDATOR_BYPASSED` (inverted: over-blocking) |

---

## 2. Commit, configuration and — crucially — what code actually ran

| Item | Value | Evidence |
|---|---|---|
| Analysed commit | `f2e7da0` | `git rev-parse HEAD` in the audit worktree |
| Service process start | **2026-11-08 23:24:19 CET** | `ps -eo pid,lstart,cmd` → PID 1437188 |
| Merges of PR #21/#22 | **2026-11-09 ≈20:50 CET** | `gh run list` timestamps |
| Last run under audit | **2026-11-09 21:01:35 CET** | `stat reliability_audit/audit_2026-11-09.json` |

### E1 — The 21:01 run executed pre-merge code

The process that served the 21:01 analysis started at 23:24 the previous day,
more than 20 hours before the math engine was merged. CPython does not reload
modules that are already imported, so that process could not have executed
`dataflows/math/` code.

Independent corroboration: the audit JSON written at 21:01 contains **no**
`technical_audit` key.

```
$ python3 -c "import json; d=json.load(open('.../audit_2026-11-09.json')); print(d.get('technical_audit'))"
None
```

`technical_audit` is emitted unconditionally by the current `AuditRecord.to_dict`;
its absence proves the writing process did not have that code.

### 2.1 Snapshot availability — a hard limit on this audit

Only **one** execution per calendar date survives. The four reports named in the
task (14:05, 18:37, 19:24, 21:01) all wrote to the same paths, and each overwrote
its predecessor:

```
logs/BRK-B/reliability_audit/audit_2026-11-09.json          (21:01 only)
logs/BRK-B/TradingAgentsStrategy_logs/full_states_log_2026-11-09.json  (21:01 only)
```

Neither `run_id` nor `generated_at` is part of the filename, so superseded runs
are unrecoverable. **An exact historical reproduction of the 14:05, 18:37 and
19:24 runs is therefore not possible.** Where the task asks about those
timestamps specifically, this audit reports `NOT_OBSERVABLE`.

---

## 3. Architecture actually found

The pipeline that ran differs from the pipeline in the repository. What exists
today:

```
yfinance / SEC EDGAR / FRED / Polymarket
  -> dataflows/{yahoo,sec_edgar,...}        raw fetch + cache
  -> dataflows/validator.py, arithmetic.py  unit / OHLCV / percentage checks
  -> dataflows/math/*                       engine (NOT in the 21:01 run)
  -> agents/{analysts,researchers,...}      LLM interpretation
  -> dataflows/gate_integration.py          Execution Gate
  -> dataflows/execution_gate.py            authorisation
  -> (no broker adapter exists)
```

What ran at 21:01:

```
yfinance / SEC EDGAR / ...
  -> dataflows/{yahoo,sec_edgar,...}
  -> agents/*                               LLM computes every derived figure
  -> dataflows/gate_integration.py -> execution_gate.py
```

The `dataflows/math/` stage was absent. That is the whole of the lineage
problem in one line.

### 3.1 Control status matrix (§12)

| Control | Implemented | Invoked | Effective | Evidence |
|---|---|---|---|---|
| Financial Math Engine (`dataflows/math/`) | yes, 15 modules | **in the 21:01 run: no** | **no** | E1 — process start vs merge time |
| Metric Registry (`math/registry.py`) | yes | **no** — `record_decision` never supplies one | **no** | E6: `grep -n "metric_registry=" trading_graph.py` returns nothing |
| Numeric Claims Validator (`math/claims.py`, `report_validation.py`) | yes | yes, but with `registry=None` | **inverted** | F6 — blocks every run |
| Financial Reconciliation (`math/definitions.py`, `sec_reconciliation.py`) | yes | **no** in gate path; `data_quality_node` wires it only when a provider is injected | **partial** | `_reconcile_sec_concepts` returns `None` without `sec_facts_provider` |
| Market Session Validator (`math/session.py`, `exchange_calendar.py`) | yes | **only in `gate_integration.py`**, after synthesis | **no** on the price used | F5 |
| Risk Engine (`risk_engine.py`) | yes | yes | **no** — `qty_proposed: null` | audit JSON `risk` block |
| Execution Gate (`execution_gate.py`) | yes | yes | **yes** | 21:01 audit: `action: BLOCK` |

---

## 4. Lineage of the five metrics

Full transition matrix in `DATA_LINEAGE_MATRIX.md`. Summary of where meaning is
lost:

| Metric | Raw | First loss of definition | Reaches the decision as |
|---|---|---|---|
| Revenue Q3 | 105,647 (Revenues) **and** 74,322 (contract) both present | `sec_edgar.py:58` picks the first tag in the list | 74,322, called "record trimestrale" |
| P/B | price, equity, share counts | no valuation module in the path | 0.001x / 1.35x / 1.73x, all LLM-authored |
| EPS TTM | vendor + filing | vendor field accepted without GAAP reconciliation | 39.76, origin not captured |
| ATR / size | ATR 9.524 | `trader.py` writes "+4-7pp" instead of a quantity | `qty_proposed: null` |
| Bar | partial candle | `ohlcv.py:152` acknowledges the hazard, does not guard it | "ultima chiusura verificata 576,74" |

---

## 5. Audit A — consolidated Q3 revenue

**Answer to the task's question: the incorrect value is born in the XBRL
extractor's tag priority, not in the provider and not in the LLM.** The LLM then
faithfully repeated what the parser gave it.

The filing contains both figures for the same quarter:

| Concept | Q3 2026 value | Scope |
|---|---|---|
| `Revenues` | **105,647,000,000** | consolidated total |
| `RevenueFromContractWithCustomerExcludingAssessedTax` | 74,322,000,000 | contract revenue, a subset |

`dataflows/vendors/sec_edgar.py:58` declares them in this order:

```python
("Revenue", ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
             "SalesRevenueNet")),
```

and `_as_of` (same file, l.167) resolves in order:

> `# Tags are tried in order and a period keeps the first one that reports it`

So the narrower concept wins and the broader one is never consulted. The comment
explains the intent — filers rename lines over the years, and values are never
summed across tags — but the ordering treats a *subset* as a rename of the
total. Nothing downstream can recover the distinction: by the time the analyst
sees the number, the concept name is gone.

Empirical confirmation against the live SEC payload:

```
$ python3 (temporary script, outside the repository)
RevenueFromContractWithCustomerExcludingAssessedTax    74,322,000,000
Revenues                                              105,647,000,000
SalesRevenueNet                                              ABSENT
```

Answers to the twelve sub-questions: endpoint `companyfacts`; CIK 0001067983;
filing 10-Q accepted 2026-11-09; concept as above; no dimensions (the narrower
figure is undimensioned contract revenue, not a segment); period 2026-07-01 →
2026-09-30; USD, base units; the difference is the non-contract businesses
(railroad, insurance, utilities); no normalizer transformation; the
Fundamentals Analyst received 74,322 as "Revenue"; the Research Manager and the
final decision inherited it.

Cause: **`METRIC_DEFINITION_MISMATCH`**, first observable point
`sec_edgar.py:58` (with `_as_of` l.167 as the mechanism).

---

## 6. Audit B — Price-to-Book and share classes

Four different P/B values appear in the surviving report:

```
P/B (Class B) | ~1,73x
P/B (Class B, calcolato) | ~1,73x
P/B 0,00116
P/B ~1,7x
```

**None of them was computed by code.** `reconcile_valuation` — the function that
would produce a defensible P/B — has **no caller outside the test suite**:

```
$ grep -rn "reconcile_valuation(" --include=*.py . | grep -v "def \|test"
(no output)
```

The valuation tool (`calculate_price_to_book`) exists and is bound to the
fundamentals analyst (`analyst_execution.py:52`), so an agent *may* call it. In
the 21:01 run the process did not have that module at all (E1), so it could not
have.

Classification of the 1.73x, as the task requests — the value cannot be derived
from any single consistent pair of inputs, which is itself the finding:

| Hypothesis | Verdict | Why |
|---|---|---|
| Stale book value per share | plausible | no period is recorded for the BVPS used |
| Updated market cap with prior equity | plausible | the report cites no dates |
| Wrong class conversion | **demonstrable in the 0.001x case** | 0.00116 is ~1/1500 of 1.73 — the A/B ratio applied twice |
| Wrong equivalent share count | plausible | |
| LLM calculation | **confirmed for all four** | no code path could have produced them |
| Vendor pre-computed value | **confirmed for 0.00116** | vendor `bookValue` semantics |
| Python calculation | **no** | E1: the module was not loaded |

The two formulas the task asks to compare separately —
`market_cap / attributable_equity` and `price_B / bvps_B` — agree only when the
share class basis matches. The 0.001x value is the arithmetic signature of a
Class-A figure compared against a Class-B price: 1500× off.

Cause: **`LLM_NUMERIC_HALLUCINATION`** for the arithmetic, with
`CLASS_SHARE_MISMATCH` as the mechanism behind the 0.001x specifically.

---

## 7. Audit C — EPS TTM

**Who produces 39.76, and who certifies it?** The vendor supplies it; **nobody
certifies it**. `earnings_per_share()` exists in `math/valuation.py` and records
its basis and formula, but it is not called in the run path (E1). The number
reaches the Portfolio Manager as vendor-sourced text with no GAAP basis, no share
count and no period attached.

Reconciliation against the task's figures:

| Input | Value | Status |
|---|---|---|
| Net income TTM | ~77,084,000,000 | stated in the report |
| EPS TTM | 39.76 | vendor |
| Implied weighted-average shares | 1,938,732,394 | **derived by this audit**, not by the code |
| Period-end Class-B-equivalent shares | 2,117,500,000 | from the SEC filing |
| EPS if period-end shares were used | 36.40 | arithmetic shows the trap is ~9% |

The implied count is a plausible weighted average and consistent with 39.76, so
the vendor EPS is probably GAAP. It cannot be *proven* from the artefacts: the
report does not record which vendor field was read, nor the diluted share count
that accompanied it.

The report itself notices something is wrong and mentions a vendor "Discordanze
vendor vs SEC: il vendor riporta Revenue TTM 395,4 mld (ricostruibile dalla
serie trimestrale in ~270 mld)" — the model detected a vendor inconsistency in
prose and carried on, which is precisely what the reconciliation layer exists to
prevent and did not, because it was not loaded.

Cause: **`INSUFFICIENT_EVIDENCE`** for the exact provenance; the *absence of
certification* is confirmed.

---

## 8. Audit D — ATR, stop and position sizing

| Field | Value | Producer |
|---|---|---|
| Market price | 576.74 | vendor, partial bar (see §9) |
| Entry price | 576.0 | **LLM** (`trader_investment_plan`) |
| Stop loss | 555.0 | **LLM** |
| ATR | 9.524 | vendor indicator, passed through |
| ATR multiple | not stated | — |
| Risk per share | *never computed* | `qty_proposed: null` |
| Risk budget | not supplied | ε |
| Portfolio equity | not supplied | ε |
| Quantity requested | *absent* | — |
| Quantity authorised | *absent* | — |

The trader's plan states size as a percentage of a notional allocation:

```
**Position Sizing**: Tranche 1 subito: +4-7pp sull'allocazione standard
(~1/3 dell'incremento verso il sovrapeso 110-120%). Tranche 2 su 561...
```

`+4-7pp` is a range, not a number, and it is relative to an allocation the
system does not know. The audit JSON confirms the consequence:

```json
"risk": {"stop_model": "UNVALIDATED", "qty_proposed": null,
         "planned_risk": null, "exposure_capped_qty": null}
```

So the four formulas the task lists (`risk_per_share`, `stop_distance_atr`,
`position_risk`, `notional`) were **not evaluated at all**. They are implemented
in `risk_engine.py` and never reached, because sizing requires
`risk_budget`/`equity` which `record_decision` does not supply.

Order semantics: no trade-action vocabulary existed at 21:01. The trader's
`SELL`/`OVERWEIGHT` wording is a strategic rating; `trade_actions.py` (which
would refuse to turn a SELL into an `OPEN_SHORT`) is on `custom` but was not
loaded. The report's own text shows the risk plainly: *"Overweight equivale a
Buy"* — a rating is being read as an instruction, which is the precise defect
the vocabulary addresses.

The gate did block: `action: BLOCK`, reasons
`['DATA_QUALITY_BLOCKED', 'ENTRY_TRIGGER_NOT_MET']`. It blocked for missing data
quality and an unmet trigger — **not** for a missing quantity. Zero orders
reached any ledger, and no broker adapter exists in the repository to reach.

Cause: **sizing not computed** (`NONE_WIRED` in the task's terms); consequences
`VALIDATOR_NOT_CALLED`.

---

## 9. Audit E — intraday bar versus EOD close

The three report times, converted to the exchange's own zone:

| Report (Europe/Rome) | New York | NYSE session |
|---|---|---|
| 18:37 | 12:37 | **OPEN** (closes 16:00) |
| 19:24 | 13:24 | **OPEN** |
| 21:01 | 15:01 | **OPEN** (59 min before the close) |

All three ran while the market was trading. The surviving report calls the
price *"l'ultima chiusura verificata è 576,74"* and states *"OHLC coincidono con
il CSV: Open 569,99 / High 576,885 / Low 562,45 / Close 576,74"* — a partial
candle presented as a settled close.

**The responsible component is not the LLM.** It is
`dataflows/vendors/yahoo/ohlcv.py`, which accepts the vendor's `Close` verbatim.
That file already documents the hazard at line 152:

> `Yahoo publishes a partial daily candle during market hours, whose` ``Close``
> `is not the closing price, and row inspection cannot tell it from a final one`

The mitigation present is a cache TTL (`OHLCV_CACHE_TTL_SECONDS`), which limits
staleness but cannot make a partial candle final.

Answers to the ten questions: prices come from yfinance daily bars (the report
names a CSV); they are delayed daily candles, not realtime quotes; the bar is
dated to the current session; the vendor exposes no `is_complete`; **the system
never determines that a bar is final** in this path; indicators are recomputed
mid-session from the partial bar; the Metric Registry did not exist in the run;
the renderer does use the word `Close` for a provisional value;
`check_event_pricing` exists on `custom` but was not loaded, and its
`OBSERVABLE_INTRADAY` verdict postdates the run; the final decision did use
intraday data in an EOD framing.

Cause: **`INTRADAY_EOD_MISMATCH`**, first observable point
`vendors/yahoo/ohlcv.py` (the `Close` column of a current-day download).

---

## 10. LLM input and output

The analysts receive **prose blocks, not metrics with identifiers**. No
`MetricRecord`, no `formula_id`, no validation status reaches any agent prompt in
this run. Consequence: the model cannot distinguish a certified figure from one
it invented a paragraph earlier.

Classification of the values observed:

| Value | Class |
|---|---|
| 105,647 / 74,322 | `SOURCE FACT` (both from the filing; the *choice* between them was made by code) |
| `Revenues` / `BVPS` from SEC | `SOURCE FACT` |
| 1.35x, 1.73x, 0.00116x, -37%, -10.4%, +58% | `LLM-GENERATED CLAIM` |
| 39.76 | `SOURCE FACT` (vendor) with `UNKNOWN` certification |
| "+4-7pp" | `LLM ESTIMATE` |
| 576.74 as "verified close" | `SOURCE FACT`, mislabelled as to its status |

The point where an LLM number becomes a verified datum: **there is none in
code.** It happens by repetition — a figure stated in the fundamentals report is
re-quoted by the Research Manager, the Trader and the Portfolio Manager, and each
restatement reads as corroboration. The 1.73x P/B travels the whole chain this
way.

---

## 11. Tests: what exists and what it proves

Run in the audit worktree, no application code touched:

| File | Tests | Result | Covers |
|---|---|---|---|
| `tests/test_deterministic_math.py` | 46 | **PASS** | cases A–E arithmetic, tool contracts |
| `tests/test_sec_reconciliation.py` | 16 | **PASS** | concept scope, span matching |
| `tests/test_trade_actions.py` | 22 | **PASS** | rating vs action, reductions |
| `tests/test_execution_chokepoint.py` | 14 | **PASS** | single order producer |
| `tests/test_exchange_calendar.py` | 22 | **PASS** | holidays, early closes |
| **Total** | **120** | **PASS** | |

**Their limitation, stated plainly:** they prove the modules work in isolation.
They do not prove the modules are *reached* by a run — and E1 shows that in the
audited execution they were not. A green suite over an unloaded module is a
green suite over nothing.

There is no test asserting that `record_decision` supplies a registry, or that
the SEC tag priority prefers the consolidated total. Those are the two gaps that
allowed F1 and F6.

---

## 12. Findings and classification

See `DATA_LINEAGE_FINDINGS.md` for the full table. Confirmed causes only:

- **F1** `METRIC_DEFINITION_MISMATCH` — `sec_edgar.py:58`, tag order.
- **F2** `LLM_NUMERIC_HALLUCINATION` — no valuation code in path; 0.001x is
  additionally `CLASS_SHARE_MISMATCH`.
- **F3** `INSUFFICIENT_EVIDENCE` — vendor field not captured; certification
  absent, which is confirmed.
- **F4** `VALIDATOR_NOT_CALLED` — sizing never computed, `qty_proposed: null`.
- **F5** `INTRADAY_EOD_MISMATCH` — `ohlcv.py`, partial candle accepted.
- **F6** `VALIDATOR_BYPASSED` — **current code**: without a registry every
  labelled figure is unverifiable and every run blocks.

### The propagation chain, as asked

First observable cause for the revenue defect: **the XBRL extractor's tag
priority**. Everything after it is faithful repetition — no normalizer changes
the value, no validator contradicts it, no renderer alters it. That is why the
number arrived intact at the decision: nothing in the chain was looking at what
the number *meant*.

---

## 13. Not demonstrable with the available evidence

- Exact reproduction of the 14:05, 18:37 and 19:24 runs: overwritten (§2.1).
- The vendor field behind EPS 39.76: no payload captured.
- Which of the four P/B values came from which reasoning step: the report
  contains all four with no intermediate artefacts.
- Whether the agents actually called the math tools: no tool-call log retained
  for the 21:01 run, and the modules were not loaded.
- The exact `Close` timestamp returned by yfinance at 15:01 ET: the CSV is not
  archived.

## 14. Instrumentation that would close those gaps (proposed, not implemented)

1. Persist each run to a `run_id`-keyed directory instead of a date-keyed one.
2. Record the module `__hash__`/commit of the running process in the audit.
3. Capture the raw vendor payload (or a hash plus the field names read) per run.
4. Log tool calls with their arguments and returned records.
5. Assert in a test that `record_decision` supplies a registry — the gap behind
   F6.

**No recommendation here has been implemented.**

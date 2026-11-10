# P6 — End-to-end reliability certification

Scope: `test/p6-e2e-release-verification`, branched from `custom` at `296d4f7`
(phases 1–5 merged).

Certifies that the fixes of phases 1–5 are operative **together**. It does not
re-test each phase's internals — those suites exist — but the seams between
them, which is where a fix that works in isolation can still fail to reach the
pipeline.

## 1. What already existed before this phase

Most of P6's tasks were delivered by earlier phases:

| Task | Where it already was |
|---|---|
| 2 — end-to-end test | `tests/test_p4_e2e_brkb.py` (13 tests over the real graph) |
| 3 — reproducibility | `tests/test_p4_determinism.py` (five repeats, one digest) |
| 5 — audit per run | `tradingagents/dataflows/run_record.py` (run ids, retention, redaction) |
| 6 — CI | the matrix job runs `pytest -q` over `testpaths = ["tests"]`, so every suite runs |
| 7 — deployment verification | `docs/DEPLOYMENT_VERIFICATION.md` |

Writing them again would have produced a second, divergent set of checks. This
phase closes the three gaps that remained.

## 2. Gaps closed

### G1 — the snapshot was not complete

`fixtures/brkb_2026_11_09.py` had SEC figures, prices and indicators, but no
risk policy, no structured news and no EPS pair. The brief asks for all of them,
and a test that invents them locally runs a different policy from the one the
audit examined.

Added:

- `RISK_POLICY` — equity, cash, risk budget, exposure cap, slippage, commission,
  gap stress and the minimum stop, as one stated policy.
- `NEWS_ARTICLES` — two articles with publication and update times, a comparison
  type and a price window. One was written mid-session (intraday), one the next
  morning (close-to-close).
- `EPS_TTM`, `WEIGHTED_AVERAGE_SHARES`, `NET_INCOME_TTM` — the EPS and its
  denominator as separate values, because using the period-end count instead of
  the weighted average is a different figure.
- `EOD_CLOSE` / `EOD_PREVIOUS_CLOSE` — the settled closes (575.25 / 570.89),
  distinct from the intraday price (576.74). The audit's +0.76% follows only
  from these; the intraday price gives +1.02%, which is a different statement.
- `LLM_ANSWERS` — fixed narratives, so a run completes without a provider and
  the reproducibility tests compare computations rather than prose.

### G2 — the run record had no tool-call audit

`RunRecord` recorded what each agent *concluded*, not what the run *read*. §Task
5 asks for the tool-call audit explicitly, and without it a figure in a report
cannot be traced to the call that produced it — the chain the lineage audit
could not reconstruct.

Added `tool_calls` to `RunRecord` and `tool_calls_from_messages()`, which walks a
message history and records each call's name, redacted arguments, id and whether
a `ToolMessage` answered it. The graph populates it from `final_state["messages"]`.

### G3 — the newest suites were not in the dedicated e2e job

The matrix job runs everything, so no test was unrun. But the `e2e-reliability`
job — the required check named in the branch protection ruleset — selected an
explicit file list that stopped at phase 4. `test_p6_e2e_certification.py`,
`test_p5_risk_order_gate.py` and `test_p4_certified_metrics.py` are now in it, so
a P0 failure in them fails that check by name rather than only the matrix.

## 3. Tests

`tests/test_p6_e2e_certification.py` — 32 tests:

| Task | Coverage |
|---|---|
| 1 | the snapshot carries every input; news has timestamps; LLM answers fixed; the instant is intraday |
| 2 | revenue 105,647 not 74,322; growth +11.24%; both P/B forms with operands; the model's 1.73x never certified; EPS certified or explained; bar not EOD; news reconciled; quantity from the risk engine; gate deterministic |
| 3 | certified metrics identical across five runs; quantity, limits and gate verdict identical; only the strategic prose may vary |
| 4 | wrong revenue; incompatible P/B; irreconcilable EPS; wrong annualisation; contradicted news price; intraday bar as EOD; missing registry; missing portfolio; wrong stop side; quantity beyond the limits |
| 5 | the record carries every named field; two runs on one day do not overwrite; no credential in a record; the tool-call extractor |

## 4. Coverage of the audit's findings

| Finding | Fixed in | Certified here by |
|---|---|---|
| F1 revenue component as total | P2 | `test_consolidated_revenue_…`, `test_revenue_growth_…` |
| F2 model-authored figures | P4 | `test_the_model_authored_pb_never_becomes_the_certified_one` |
| F3 EPS without a denominator | P4 | `test_eps_is_certified_or_says_why_not` |
| F4 sizing and authorisation | P5 | `test_the_quantity_comes_from_the_risk_engine` |
| F5 partial candle as a close | P3 | `test_the_bar_is_not_certified_as_an_end_of_day_close` |
| F6 registry not reaching the gate | P1 | `test_a_missing_registry_blocks`, `TestReproducibility` |

## 5. Runtime evidence

```
commit on disk / in the module:  296d4f7
build_id:                        296d4f7@2026-11-10T12:20:18+00:00
validators all loaded:           True
math_engine loaded:              True
```

A real `record_decision` over the frozen fixture:

```
audit files: 1 -> audit_2026-11-09_20261110T122034Z-BRK-B-8fc45f19.json
gate action: BLOCK
auth:        BLOCKED
reasons:     ['RISK_PLAN_BLOCKED', 'PORTFOLIO_RISK_BUDGET_MISSING',
              'ENTRY_TRIGGER_NOT_MET']
run records: 1   has tool_calls key: True
run_id:      20261110T122034Z-BRK-B-8fc45f19
```

The block is correct: that state carries no portfolio, so no quantity can be
determined. With a book supplied the same pipeline proposes a paper order (see
P5's evidence).

## 6. Test results

```console
$ pytest tests/test_p6_e2e_certification.py -q
32 passed

$ pytest tests/ -q
4 failed, 2445 passed, 12 skipped, 100 subtests passed

$ pytest -q -p no:randomly <the CI e2e selection> 
218 passed, 2 skipped
```

The four failures are **pre-existing**: `test_financial_math_engine.py::TestProperties`
needs the `hypothesis` package, not installed on this machine.

`ruff check .`: clean.

## 7. What is not claimed

- **No broker execution was verified.** The repository has an authorisation gate
  and a paper-order ledger; there is no venue integration. A mock broker in the
  tests is a list.
- **No production deployment and no service restart.** Task 7's procedure is
  documented and was exercised in-process; the restart step requires explicit
  authorisation and no script performs it.
- **No live market or model calls.** Every test runs on the frozen fixture.

## 8. Open finding carried forward

R2 from `docs/audits/P1_RUNTIME_VERIFICATION.md`: `set_run_registry` is still
never called in production, so the analysts cannot read the registry the gate
certifies against. It does not block this certification — the gate's registry is
independent and fail-closed — but the gap between "the engine certifies" and
"the agent can quote" remains, and is the next defect worth closing.

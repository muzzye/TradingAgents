# P0 Final Hardening — verification report (2026-11-08, BRK-B 22:56 run)

**Branch under audit:** `origin/custom` @ `9b16f88`
**P0 hardening branch:** `origin/fix/p0-data-risk-execution-gate` @ `3782ca6` (PR #16, **not merged**)
**Run inspected:** `/root/.tradingagents/logs/reports/BRK-B_20261108_225608`
**Audit record inspected:** `/root/.tradingagents/logs/BRK-B/reliability_audit/audit_2026-11-08.json`

## Verdict

**P0 Final Hardening cannot be declared complete.** Three of the seven required
controls are not verifiable on the code that produced the report, and the
reported symptoms reproduce exactly.

The root cause is **not a logic bug in the P0 code**: it is that the code is
*not on `custom`* and the running service predates it.

| Fact | Evidence |
|---|---|
| PR #16 is not merged | `git merge-base --is-ancestor origin/fix/p0-data-risk-execution-gate origin/custom` → **NOT MERGED**; `origin/custom` is at `9b16f88` |
| The 22:56 run used the post-#14 code | report dir mtime `22:56:09`, merge #14 at `22:20:32` |
| But the gates did not execute | `audit_2026-11-08.json` → `data_quality.fundamentals_freshness: null`, no `execution.verdict` |
| The service predates the merge | `systemctl show tradingagents-web -p ActiveEnterTimestamp` → **20:46:03**, i.e. 1h40m *before* the merge |
| The report shows no gate outcome | `grep -ril "execution_action\|execution_approved"` over the report tree → **no match** |
| Q2 equity was used | `1_analysts/fundamentals.md` contains `747,9…` and `30/06/2026` |
| The SEC cache has no Q3 | `CIK0001067983.json` period ends after `2026-06-30` → **none** |

A Python process that was started before a code change keeps the old modules
in memory. Restarting `tradingagents-web` is required for any merged P0 code
to take effect. Until then, every new run reproduces the 22:56 symptoms
regardless of what is on disk.

## Control-by-control verification

| # | Control | File / function | Test executed | Result on `custom` | Block evidence | Residual |
|---|---|---|---|---|---|---|
| 1 | P0 controls implemented and applied | `dataflows/filing_freshness.py` (`check_filing_freshness`), `fact_registry.py`, `order_reconciliation.py`, `plan_extraction.py`, `valuation.py`, `claim_harvester.py` | `pytest tests/test_p0_auto_wiring.py tests/test_p0_completion.py` | **PARTIAL**: 6 modules present and wired into `record_decision`; `arithmetic.py`, `execution_verdict.py`, `tranche_risk`, `aggregate_tranche_risk` **absent** (PR #16) | — | P0-B and P0-C are not on `custom` |
| 2 | Why the Fundamentals Analyst uses Q2 | `graph/trading_graph.py::_known_filings` → `filing_freshness.filings_from_sec_edgar` | offline SEC extraction against the real cache | **FAIL** — two independent causes: **(a)** the process holds pre-merge code, so `_evaluate_reliability` never calls `_known_filings`; **(b)** the cached `companyfacts` for CIK 0001067983 contains **no period after 2026-06-30**, so even a restarted process finds no Q3 filing to compare against | `audit.fundamentals_freshness == null` | The 8-K release of 2026-11-07 is not in XBRL `companyfacts`; the freshness gate needs the release date from a filing index (8-K/IR), not only companyfacts |
| 3 | Stale caches / persisted snapshots | `~/.tradingagents/cache`, `logs/`, `__pycache__` | inspection | **FAIL** — four **orphan `.pyc`** files with no source module (`arithmetic`, `technical_arithmetic`, `cross_agent`, `fundamentals_freshness`); SEC cache written 13:59 and holding only ≤Q2 facts; no memory log or checkpoint present | — | Orphan bytecode is harmless in itself but proves branches were checked out in place; the SEC cache is the operative staleness |
| 4 | Arithmetic errors blocked before the Research Manager | `dataflows/arithmetic.py` (PR #16) | `pytest tests/test_p0_final_hardening.py -k arith` on PR #16 | **FAIL on `custom`** (module absent); **PASS on PR #16** | On PR #16 the audit carries `blocking_claims: ["FUNDAMENTALS_STALE", "ARITHMETIC_MISMATCH"]` and the gate returns **BLOCK** | The check runs at `evaluate_decision` (end of run), **not** before the Research Manager — the debators still see the wrong numbers; only execution is blocked |
| 5 | Risk engine validates every tranche against the real portfolio | `risk_engine.size_with_exposure_cap` (on `custom`), `tranche_risk` + `aggregate_tranche_risk` (PR #16) | `pytest tests/test_p0_sizing_wiring.py`; direct `aggregate_tranche_risk` call | **PARTIAL** — single-tranche sizing with the exposure cap is present and wired; **`aggregate_tranche_risk` raises `ImportError` on `custom`** | 100k/1%/560/552.07 → **8 shares (4,480 USD)** under a 5% cap, not 126 | Multi-tranche aggregation exists only on PR #16, and is not yet called by `record_decision` even there |
| 6 | Execution Gate prevents simulated order submission | `dataflows/execution_gate.py` (on `custom`); `execution_verdict.py` (PR #16) | `pytest tests/test_p0_auto_wiring.py`; direct demo on PR #16 | **PARTIAL** — WAIT/BLOCK and idempotent `submit()` work on `custom`; the **structured verdict** (`execution.approved`, `authorized_orders`) is only on PR #16 | On PR #16, a stale-fundamentals + wrong-arithmetic run: `action=BLOCK`, `approved=False`, `authorized_orders=[]`, **broker received 0 orders** | The report never showed the gate because the running process predates it |
| 7 | P0 tests actually run in CI | `.github/workflows/ci.yml` | inspection | **FAIL on `custom`** — `grep -c "reliability-regression"` → **0**; the job and the `test_p0_paper_safety.py` / `test_p0_final_hardening.py` files exist only on PR #16 | — | Also unverified: whether the ruleset marks any job as a required check |

## Demonstrated rejected order (real path, PR #16 code)

Input: BRK-B, Q2 fundamentals while the Q3 release is known, plan
`Entry 556.00, stop 542.50, ATR 9.408`, equity 100k, risk budget 1k, 5%
exposure cap, and a stated −11.3% distance from the 586.30 high.

```
ACTION        : BLOCK
REASONS       : ['DATA_QUALITY_BLOCKED']
APPROVED      : False
AUTH ORDERS   : []
BLOCKING      : ['DATA_QUALITY_BLOCKED']
broker.send() : never called — 0 orders
```

The audit record of the same evaluation:

```
status          : BLOCKED_BY_CLAIMS
blocking_claims : ['FUNDAMENTALS_STALE', 'ARITHMETIC_MISMATCH']
freshness       : STALE_FUNDAMENTALS
arithmetic      : [('OK', False), ('ARITHMETIC_MISMATCH', True)]
```

The same scenario on `custom` cannot be evaluated at all: `arithmetic.py`
and `execution_verdict.py` do not exist there.

## What this branch adds

`tests/test_p0_verifiability.py` makes the invisible failure visible: an
audit that lacks the P0 fields is **diagnosed as "gates did not run"**, never
read as a passed validation. On `custom` it passes 3 and skips 1 with an
explicit reason; on PR #16 it passes 4 with no skip, so the block is proven
where the code exists.

## Required actions, in order

1. **Merge PR #16** (P0 final hardening) — otherwise P0-B/P0-C/P0-D and the
   CI gate are simply not in the product.
2. **Restart `tradingagents-web`** after the merge. Without it the old
   modules stay in memory and every run reproduces the 22:56 symptoms.
3. **Fix the second freshness cause**: SEC `companyfacts` carries no Q3 2026
   for Berkshire. The gate must read the 8-K/IR release date, or the
   fundamentals tool must be told the period is stale — otherwise the
   analyst legitimately sees only Q2.
4. **Move the arithmetic check earlier** than `evaluate_decision` so the
   Research Manager and Portfolio Manager do not debate wrong numbers.
5. **Wire `aggregate_tranche_risk`** into `record_decision` instead of
   leaving it available-but-uncalled.
6. **Mark the CI jobs as required checks** on the `custom` ruleset.
7. **Clear orphan bytecode** (`__pycache__`) when switching branches in
   place, to avoid reading a stale module.

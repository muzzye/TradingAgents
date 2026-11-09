# Evidence Index

Every conclusion in the audit points to an entry here. Commands were run in a
detached worktree at `/tmp/ta-audit` (commit `f2e7da0`); the user's checkout was
never modified.

---

## E1 — The 21:01 run executed pre-merge code

| Field | Value |
|---|---|
| Symbol | OS process table; `ps -eo pid,lstart,cmd` |
| Command | `ps -eo pid,lstart,cmd \| grep webapp` |
| Observed | `1437188 Sun Nov 8 23:24:19 2026 /var/www/TradingAgents/.venv/bin/python -m webapp` |
| Compared with | `gh run list` → PR #21 merged 19:50, PR #22 merged 19:51 (2026-11-09) |
| Conclusion | the process predates the merge by ~20 h, so `dataflows/math/` was not in memory |

Corroboration: `python3 -c "...audit_2026-11-09.json... .get('technical_audit')"`
→ `None`, while `AuditRecord.to_dict` on `custom` emits that key unconditionally.

---

## E2 — Only one run per date survives

| Field | Value |
|---|---|
| Command | `ls -la /root/.tradingagents/logs/BRK-B/{reliability_audit,TradingAgentsStrategy_logs}/` |
| Observed | one `audit_2026-11-09.json`, one `full_states_log_2026-11-09.json`, both 21:01 |
| Conclusion | filenames are date-keyed, not run-keyed; the 14:05/18:37/19:24 runs are gone |

---

## E3 — Revenue tag priority (F1)

| Field | Value |
|---|---|
| File | `tradingagents/dataflows/vendors/sec_edgar.py` |
| Lines | 58 (tag order), 167 (resolver comment) |
| Command | `sed -n '57,59p'; sed -n '167,169p'` |
| Observed | `("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet")` and `# Tags are tried in order and a period keeps the first one that reports it` |
| Cross-check | temporary script over the live SEC payload: contract 74,322,000,000; `Revenues` 105,647,000,000; `SalesRevenueNet` absent |

---

## E4 — P/B has no computing caller (F2)

| Field | Value |
|---|---|
| File | `tradingagents/dataflows/valuation.py` (`reconcile_valuation`) |
| Command | `grep -rn "reconcile_valuation(" --include=*.py . \| grep -v "def \|test"` |
| Observed | no output |
| Cross-check | report text: `P/B (Class B) \| ~1,73x`, `P/B 0,00116`, `~1,35x`, `~1,7x` |
| Independent arithmetic | `761,368,000,000 / 2,117,500,000 = 359.5599`; `576.74 / 359.5599 = 1.6040` |

---

## E5 — Sizing never computed (F4)

| Field | Value |
|---|---|
| File | `logs/BRK-B/reliability_audit/audit_2026-11-09.json`, `risk` block |
| Command | `python3 -c "import json; print(json.load(open(...))['risk'])"` |
| Observed | `"qty_proposed": null, "planned_risk": null, "exposure_capped_qty": null, "stop_model": "UNVALIDATED"` |
| Report text | `"Position Sizing": Tranche 1 subito: +4-7pp sull'allocazione standard` |
| Gate | `"action": "BLOCK"`, `reason_codes: ["DATA_QUALITY_BLOCKED", "ENTRY_TRIGGER_NOT_MET"]` |

---

## E6 — No registry reaches the gate (F6)

| Field | Value |
|---|---|
| File | `tradingagents/graph/trading_graph.py`, `_evaluate_reliability` |
| Command | `grep -n "metric_registry=" tradingagents/graph/trading_graph.py` |
| Observed | no output (parameter never supplied) |
| Reproduction | `evaluate_decision(..., trigger_met=True)` → `BLOCK`, `['DATA_QUALITY_BLOCKED', 'NUMERIC_CLAIM_UNVERIFIABLE']` |
| Cross-check | `evaluate_math(registry=None, ...)` → `reasons: (NUMERIC_CLAIM_UNVERIFIABLE,)`, `blocks: True` |

---

## E7 — The partial-candle hazard is documented, not guarded (F5)

| Field | Value |
|---|---|
| File | `tradingagents/dataflows/vendors/yahoo/ohlcv.py` |
| Lines | 152–154 (`_cache_is_fresh` docstring) |
| Command | `sed -n '145,175p'` |
| Observed | `Yahoo publishes a partial daily candle during market hours, whose ``Close`` is not the closing price, and row inspection cannot tell it from a final one (#1150)` |
| Mitigation present | `OHLCV_CACHE_TTL_SECONDS` (staleness bound only) |

---

## E8 — All three report times fall inside the NYSE session

| Field | Value |
|---|---|
| Command | temporary script, `zoneinfo` conversion Europe/Rome → America/New_York |
| Observed | 18:37 → 12:37 ET, 19:24 → 13:24 ET, 21:01 → 15:01 ET; session closes 16:00 ET |

---

## E9 — Report quotes for the five metrics

| Field | Value |
|---|---|
| File | `logs/BRK-B/TradingAgentsStrategy_logs/full_states_log_2026-11-09.json` |
| Fields | `fundamentals_report`, `market_report`, `trader_investment_plan`, `final_trade_decision` |
| Command | `python3` regex extraction per metric |
| Observed | revenue `74,3 mld (+16,6% a/a)`; P/B `0,00116`/`~1,73x`/`~1,35x`; EPS `39,76`/`22,43`; sizing `+4-7pp`; price `576,74` labelled `l'ultima chiusura verificata` |

---

## E10 — Test results

| Field | Value |
|---|---|
| Command | `python -m pytest tests/test_deterministic_math.py tests/test_sec_reconciliation.py tests/test_trade_actions.py tests/test_execution_chokepoint.py tests/test_exchange_calendar.py -q` |
| Observed | `120 passed in 4.92s` |
| Limitation | proves the modules work in isolation, not that a run reaches them (see E1) |

---

## E11 — Control status matrix sources

| Control | Source of the "invoked" verdict |
|---|---|
| Math engine | E1 |
| Metric Registry | E6 |
| Claims validator | E6 (invoked, but inert data) |
| SEC reconciliation | `data_quality_node._reconcile_sec_concepts` returns `None` when no `sec_facts_provider` is injected; `graph/setup.py` constructs the node with no provider |
| Session validator | E8 + `grep -rn "assess_bar\|market_session_status"` → only `gate_integration.py` and `audit.py` |
| Risk engine | E5 |
| Execution Gate | E5 (`action: BLOCK` recorded) |

---

## Commands run, in order

```
git worktree add /tmp/ta-audit f2e7da0 --detach
cd /tmp/ta-audit && git checkout -b audit/tradingagents-data-lineage-brkb
ps -eo pid,lstart,cmd | grep webapp
stat -c '%y %n' logs/BRK-B/...
python3 (regex extraction over full_states_log_2026-11-09.json)
python3 (zoneinfo conversion of the three report times)
python3 (SEC payload concept comparison)
grep -rn "reconcile_valuation(" --include=*.py .
grep -n "metric_registry=" tradingagents/graph/trading_graph.py
python -m pytest <five validator test files> -q
```

No application file, test file, prompt, formula, provider priority or cache was
modified. No order was sent anywhere; the repository contains no broker adapter
to send one to.

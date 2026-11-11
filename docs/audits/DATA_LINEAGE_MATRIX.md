# Data Lineage Matrix — BRK-B, commit `f2e7da0`

One row per transition. `NOT_OBSERVABLE` means the artefacts do not contain the
value; no figure was invented to fill a cell.

**Reading note:** the 21:01 run — the only one preserved — executed code that
predates the math engine (evidence E1). So a `CALCULATED` cell reading
`NOT_CALCULATED` means *no code produced this*, not *the value is unknown*.

---

## M1 — Consolidated Q3 2026 revenue

| Stage | Value | Unit | Source | File / Function | Status |
|---|---|---|---|---|---|
| RAW | 74,322,000,000 **and** 105,647,000,000 | USD | SEC `companyfacts`, CIK 0001067983 | `sec_edgar._cached_json` | both present in payload |
| NORMALIZED | 74,322,000,000 | USD | contract concept selected | `sec_edgar._as_of` l.167, order from l.58 | **definition lost here** |
| CALCULATED | none | — | no derivation performed | — | `NOT_CALCULATED` |
| AGENT INPUT | "Revenue 74,322" (prose) | USD m | fundamentals analyst prompt | `agents/analysts/fundamentals_analyst.py` | concept name absent |
| AGENT OUTPUT | "record trimestrale a 74,3 mld (+16,6% a/a)" | USD bn | LLM | `full_states_log_2026-11-09.json` | - |
| FINAL DECISION | 74,322 inherited | USD | report chain | `final_trade_decision` | 16.6% growth on a subset |

Verification against the filing: consolidated 105,647 vs 94,972 (Q3 2025) is
**+11.24%**; the report's +16.6% is computed across 74,322 / 63,747, the
contract-only series.

---

## M2 — Price-to-Book

| Stage | Value | Unit | Source | File / Function | Status |
|---|---|---|---|---|---|
| RAW | price 576.74; equity 761,368,000,000; A 543,000; B 1,303,000,000 | mixed | yfinance + SEC | `yahoo/ohlcv.py`, `sec_edgar.py` | price is a partial bar (§M5) |
| NORMALIZED | as above, no class reconciliation | — | — | — | `NOT_OBSERVABLE` (no BVPS field captured) |
| CALCULATED | **none** | — | `reconcile_valuation` has no caller | `dataflows/valuation.py` | `NOT_CALCULATED` |
| AGENT INPUT | prose figures only | — | - | fundamentals prompt | no metric id, no formula |
| AGENT OUTPUT | `0,00116` / `~1,35x` / `~1,7x` / `~1,73x` | multiple | LLM | state log | four mutually inconsistent values |
| FINAL DECISION | 1,73x quoted | multiple | LLM | `final_trade_decision` | - |

Reference arithmetic (this audit, independent):
`761,368,000,000 / (1,303,000,000 + 543,000 × 1500) = 359.5599` BVPS
→ `576.74 / 359.5599 = 1.6040`. The reported 1.73x does not reproduce from any
single pair; 0.00116 ≈ 1.604 / 1500 is the double-conversion signature.

---

## M3 — EPS TTM

| Stage | Value | Unit | Source | File / Function | Status |
|---|---|---|---|---|---|
| RAW | vendor EPS field | USD/share | yfinance fundamentals | `yahoo/fundamentals.py` | **field name not captured** |
| NORMALIZED | 39.76 | USD/share | pass-through | — | basis unknown |
| CALCULATED | **none** | — | `earnings_per_share()` not called | `math/valuation.py` | `NOT_CALCULATED` |
| AGENT INPUT | "EPS TTM 39,76" | USD/share | prompt prose | fundamentals prompt | no basis, no share count |
| AGENT OUTPUT | 39.76 reused; "EPS forward 22,43"; "-44%" | mixed | LLM | state log | the -44% is right (22.43/39.76-1 = -43.59%) |
| FINAL DECISION | 39.76 inherited | USD/share | - | - | uncertified |

Implied weighted-average shares: `77,084,000,000 / 39.76 = 1,938,732,394`.
Period-end basis would give 36.40 — a 9% error the report avoids, though not
provably by design.

---

## M4 — ATR, stop and sizing

| Stage | Value | Unit | Source | File / Function | Status |
|---|---|---|---|---|---|
| RAW | ATR 9.524; price 576.74 | USD | yfinance indicators | `yahoo/ohlcv.py` | partial bar |
| NORMALIZED | as above | USD | - | - | - |
| CALCULATED | **none** | — | sizing not executed | `risk_engine.size_with_exposure_cap` | `qty_proposed: null` |
| AGENT INPUT | entry 576.0, stop 555.0 as prose | USD | LLM plan | `trader_investment_plan` | entry/stop invented, not extracted |
| AGENT OUTPUT | "+4-7pp sull'allocazione standard", "110-120%" | % | LLM | state log | a range, not a quantity |
| FINAL DECISION | `qty_proposed: null` | — | gate | `reliability_audit/audit_2026-11-09.json` | blocked on data quality, not on size |

The four formulas (`risk_per_share`, `stop_distance_atr`, `position_risk`,
`notional`) were **not evaluated**. Had they been, with entry 576 and stop 555
the risk per share is 21.00 and the ATR distance 2.2041 — but that is this
audit's arithmetic, not the system's.

---

## M5 — Intraday bar versus EOD close

| Stage | Value | Unit | Source | File / Function | Status |
|---|---|---|---|---|---|
| RAW | OHLC 569.99 / 576.885 / 562.45 / 576.74 | USD | yfinance daily candle | `yahoo/ohlcv.py` | downloaded 15:01 ET, session open |
| NORMALIZED | `Close` = 576.74 | USD | accepted verbatim | `ohlcv.py` l.152 (hazard documented, unguarded) | **intraday promoted to close here** |
| CALCULATED | indicators recomputed on the partial bar | — | EMA 561.33, SMA 522.995, Bollinger upper 580.937 | analyst tooling | provisional, not marked |
| AGENT INPUT | "chiusura verificata 576,74" | USD | prompt prose | market analyst | mislabelled |
| AGENT OUTPUT | "OHLC coincidono con il CSV", +24.2%, +15.83% | mixed | LLM | market_report | all derived from the partial bar |
| FINAL DECISION | EOD framing | — | - | - | strategy treats it as settled |

NYSE status at the three report times: 12:37, 13:24, 15:01 ET — **open** in all
three. The session closes at 16:00 ET.

---

## Summary of where meaning dies

| # | Transition | What is lost |
|---|---|---|
| 1 | `sec_edgar._as_of` | which concept the number came from |
| 2 | `ohlcv.py` `Close` | whether the bar is final |
| 3 | analyst prompt assembly | all provenance: no metric id, no formula, no period |
| 4 | LLM restatement chain | the distinction between computed and asserted |

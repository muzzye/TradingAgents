# P3 — Market data finality and event/price reconciliation

Scope: `fix/p3-market-temporal-consistency`, branched from `custom` at `5992662`.
Corrects F5, and closes two related ways a number reaches a report as something
it is not.

Prerequisites (phases 1 and 2) are merged in `custom` at `5992662`.

## 1. What was already correct

`vendors/bar_finality.py` already implemented the five states and the
calendar-based judgement, and `vendors/yahoo/snapshot.py` already used it. The
three report times of 2026-11-09 already returned `INTRADAY_PARTIAL`. Task A was
therefore mostly done; the gaps are stated in §2.

## 2. Root cause of the remaining gaps

### G1 — the OHLCV frame carried no finality (F5)

`vendors/yahoo/ohlcv.py` documented the hazard at its cache comment — "Yahoo
publishes a partial daily candle during market hours, whose `Close` is not the
closing price, and row inspection cannot tell it from a final one (#1150)" — and
mitigated it with a cache TTL. A TTL bounds staleness; it cannot make a candle
final. `load_ohlcv` returned a frame with no statement about its last bar, so a
caller had to remember to judge it.

`BarProvenance` also had no `snapshot_id`, so two readings in one run could not
be shown to be the same observation.

### G2 — nothing reconciled a news figure against the session change

The reports stated `+0.76%` end to end (570.89 → 575.25) while articles
described `-1.01%`. Both are real: an article written mid-session measures an
intraday window. Nothing in the codebase could say so — grep for
`TIME_WINDOW_MISMATCH`, `CONTRADICTED` or any event-claim type returned nothing.

### G3 — a growth label was never checked against its periods

The reports called a comparison between September 2026 and November 2025
`YoY`. Those periods are **ten** months apart. `math/definitions.py` had
`period_granularity()`, which tells a quarter from a year, but nothing checked
the *distance*, so a 10-month change was labelled as an annual one.

## 3. Changes

| File | Change |
|---|---|
| `vendors/bar_finality.py` | `BarProvenance.snapshot_id`, threaded through `assess_finality` at all six return points and reported by `summary()`. |
| `vendors/yahoo/ohlcv.py` | New `assess_last_bar(symbol, data, observed_at, now)`: judges the frame's newest bar against the session calendar, with a `{symbol}-{session_date}` snapshot id. The frame and its dates are already here, so this is where the judgement belongs. |
| `math/event_claims.py` | New. `BenchmarkSnapshot` (the certified change, built from two confirmed closes), `EventClaim` (a figure with its full provenance), five verdicts, `reconcile_event_claim` / `reconcile_claims`. |
| `math/period_labels.py` | New. `PeriodKind`, `parse_period`, `label_kind`, `check_period_label`, `growth_for_label`. |
| `tests/test_p3_market_temporal_consistency.py` | 33 tests over the cases the task lists. |

### Why the news verdicts are five, not two

Deleting a disagreeing article would be wrong, and the audit says so: it may
describe a different window. The verdicts distinguish *not comparable* from
*wrong*, which one flag cannot:

- `TIME_WINDOW_MISMATCH` — a real move over a window the benchmark does not
  measure. The `-1.01%` case.
- `CONTRADICTED` — **same** window, same comparison type, different figure.
  The only verdict that says an article is wrong.

Both keep `may_replace_eod_change = False`, so an article can never stand in for
the certified change even when it is correct.

## 4. Example — a bar's finality

```console
=== bar as the 19:24 run saw it ===
  session 2026-11-09  finality INTRADAY_PARTIAL  usable_for_eod=False
  snapshot_id BRK-B-2026-11-09

=== same bar, judged at 23:00 Rome ===
  finality EOD_CONFIRMED  usable_for_eod=True
```

The same frame yields two answers because the question — *may an end-of-day
strategy use this?* — has two answers at two times. That is the point of judging
a bar against a clock rather than reading its `Close`.

## 5. Example — event-to-price reconciliation

```console
certified EOD change: 0.7637%  (from the confirmed bar)

  -1.01%  TIME_WINDOW_MISMATCH   replace_EOD=False
   0.76%  CONFIRMED              replace_EOD=True
```

The `-1.01%` article is kept and is quotable *as an intraday move*; it is not
the session's change.

## 6. Example — a mislabelled period

```console
'YoY' Nov2025->Sep2026: PERIOD_MISMATCH (10 vs 12 months) blocks=True
```

`growth_for_label()` returns `None` for that pair. The arithmetic would produce
a number, and the number is what a reader would quote without reading the label.

## 7. Test results

```console
$ pytest tests/test_p3_market_temporal_consistency.py -q
33 passed

$ pytest tests/ -q
4 failed, 2358 passed, 12 skipped, 100 subtests passed
```

The four failures are **pre-existing**: `test_financial_math_engine.py::TestProperties`
needs the `hypothesis` package, which is not installed. Verified earlier against
clean `custom` with `git stash`.

Covered: the three report times (session open), after the close (pending, then
confirmed past the grace), the NYSE holiday (Christmas), an early close (13:00
the day after Thanksgiving), daylight-saving flips, stale series, news before /
during / after the session, the `-1.01%` / `+0.76%` contradiction, indicators
over a partial bar, and the `YoY` / `MoM` / `QoQ` / quarterly-`YoY` / annualized
labels.

`ruff check .`: clean.

## 8. Not changed

The LLM strategy logic, the risk engine and the Execution Gate are untouched.
No broker operation; no order was created or sent.

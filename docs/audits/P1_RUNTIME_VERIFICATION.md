# P1 — Runtime verification and the metric registry (F6)

Scope: `fix/p1-runtime-registry`, branched from `custom` at `0541d4c`.
Covers Task 1 (does the serving process run the distributed code?) and Task 2
(the F6 defect: was the metric registry actually wired?).

Every figure below was read from the machine at the time of writing, not from
an earlier report. The commands are given so they can be re-run.

## 1. The F6 defect is already fixed in `custom`

The audit's F6 finding (branch `audit/tradingagents-data-lineage-brkb`,
`docs/audits/DATA_LINEAGE_FINDINGS.md`) states that
`graph/trading_graph.py::_evaluate_reliability` called `evaluate_decision`
without a `metric_registry`.

That is no longer true. Checked on `0541d4c`:

```console
$ grep -n "metric_registry=" tradingagents/graph/trading_graph.py
515:                metric_registry=registry,
```

The registry is built per run by `_build_metric_registry` (line 606) from the
engine's own computations via `math/run_registry.py`, and supplied at line 515.
The audit's own probe — the one that reproduced the block — now returns an
approval instead of `NUMERIC_CLAIM_UNVERIFIABLE`.

The behaviour is pinned by `tests/test_p1_runtime_gate_integration.py`
(20 tests), which exercises the real path
`TradingAgentsGraph.record_decision` → `_evaluate_reliability` →
`evaluate_decision`, not `evaluate_decision` in isolation:

| Requirement | Test |
|---|---|
| Registry present and used | `test_a_supplied_registry_is_used_for_verification` |
| Registry per run, not shared | `test_the_registry_is_per_run_not_shared` |
| Registry built from computation | `test_the_registry_is_built_from_engine_computation_not_prose` |
| Registry missing → BLOCK | `test_a_missing_registry_blocks_execution` |
| Registry empty → BLOCK | `test_an_empty_registry_blocks_execution` |
| Missing registry still reports | `test_a_missing_registry_still_produces_a_report` |
| Wrong claim → BLOCK | `test_a_contradicted_metric_blocks` |
| Report ≠ authorisation | `test_report_and_authorization_are_separate_fields` |
| `WAIT` is not a refusal | `test_a_wait_is_not_applicable_not_blocked` |
| No order survives a block | `test_no_order_object_survives_a_block` |
| The graph supplies it | `test_record_decision_builds_and_supplies_a_registry` |
| Fail-closed without metrics | `test_the_graph_blocks_when_the_state_carries_no_metrics` |

The gate stays fail-closed: an unverified critical metric still blocks. Nothing
here makes F6 disappear by making arithmetic errors non-blocking.

## 2. Runtime verification of the serving process

### 2.1 What is running

```console
$ pgrep -fl "webapp"
6066 /opt/homebrew/.../Python -m webapp
$ ps -p 6066 -o lstart=
Tue Nov 10 10:30:10 2026
$ ps eww -p 6066 | tr ' ' '\n' | grep TRADINGAGENTS_WEB
TRADINGAGENTS_WEB_RELOAD=1
```

| Question | Answer | How |
|---|---|---|
| Code on disk | `0541d4c` | `git rev-parse --short HEAD` |
| Code the process runs | `0541d4c` | `/api/health` → `build.commit` |
| Working directory | `/Users/emiliano.muzzurru/Work/TradingAgents` | `lsof -a -p <pid> -d cwd` |
| Interpreter | `/opt/homebrew/Cellar/python@3.15/3.15.2/.../python3.15` | `ps` |
| Modules imported | the checkout, not site-packages | `tradingagents.__file__` |
| Listener | PID 38410 (spawn worker) | `lsof -nP -iTCP:8000 -sTCP:LISTEN` |
| Worker started | `Tue Nov 10 11:12:15 2026 CET` | `ps -p 38410 -o lstart=` |

The parent (6066) and a spawn worker (38410) both hold the listening socket:
`TRADINGAGENTS_WEB_RELOAD=1` makes Uvicorn fork a reloader child. A restart
therefore replaces the worker, not the parent.

### 2.2 Stale code is not the problem today

Comparing each critical module's mtime against the moment the code was loaded:

```console
process loaded its code: 2026-11-10T10:22:37+00:00
  ok     2026-11-10T10:12:15+00:00  tradingagents/graph/trading_graph.py
  ok     2026-11-10T10:12:15+00:00  tradingagents/dataflows/math/run_registry.py
  ok     2026-11-10T09:26:03+00:00  tradingagents/dataflows/math/registry.py
  ok     2026-11-10T10:12:15+00:00  tradingagents/dataflows/gate_integration.py
  ok     2026-11-10T10:12:15+00:00  webapp/app.py
-> 0 module(s) changed after load: process matches the disk
```

So the running process is **not** stale: `commit` on disk and `commit` the
process reports agree, and no critical module changed after it loaded.

## 3. Findings

### R1 — `service_started_at` is not the process start *(medium)*

The health payload reports `service_started_at` as `LOADED_AT`, which
`build_info.py` captures when the module is **first imported**
(`LOADED_AT = datetime.now(UTC)` at line 31). That is not the process start.

Evidence, from the live service:

```
worker 38410 started           Tue Nov 10 11:12:15 2026 CET = 10:12:15 UTC
reported service_started_at    2026-11-10T10:22:37+00:00
```

The reported value is **10 minutes after** the process it describes began —
impossible for a start time. The same skew reproduces in isolation:

```console
$ python -c "
t0 = now(); sleep(3)
from tradingagents.build_info import LOADED_AT
print(t0, LOADED_AT)"
process started at : 2026-11-10T10:24:05+00:00
LOADED_AT says     : 2026-11-10T10:24:08+00:00
```

Impact: the pair `commit` + `service_started_at` is the audit's proposed test
for "did the service restart after the merge?". With this defect the second
half is unusable — an operator comparing a stale commit against an import time
later than the process start would conclude the opposite of the truth.

Severity is medium, not high: the primary question (*which commit is live?*) is
still answered correctly by `commit`, and today's process happens to match.

A correct `service_started_at` is obtainable without extra dependencies:
`ps -o lstart=` on the owning PID, or `time.time() - psutil.Process().create_time()`.
Both read the kernel's start time rather than a Python import.

Not fixed in this branch: it is a change to the runtime identity contract that
`run_record.runtime_fingerprint()` and the P4 determinism tests also consume.
Recorded here so it is fixed deliberately rather than smuggled into a
verification branch.

### R2 — the agents cannot read the certified metrics *(high)*

`get_certified_metric` is bound to the market and fundamentals analysts
(`math_tools.TOOLS_FOR_MARKET`, `TOOLS_FOR_FUNDAMENTALS`) so a report quotes a
certified value instead of restating a model's prose. It reads a module global:

```python
_REGISTRY: MetricRegistry | None = None

def _run_registry() -> MetricRegistry | None:
    return _REGISTRY

def set_run_registry(registry) -> None:
    global _REGISTRY
    _REGISTRY = registry
```

`set_run_registry` is called from **the tests only**:

```console
$ grep -rn "set_run_registry" tradingagents/ cli/ webapp/ --include=*.py | grep -v __pycache__
tradingagents/agents/math_tools.py:683:def set_run_registry(registry) -> None:
```

The definition, and no call. Reproduced end to end:

```console
registry has P/B: True            # the gate's registry certifies price_to_book
agent tool sees: None | UNSUPPORTED | blocks: True
```

The registry the gate uses is built inside `_evaluate_reliability`, **after**
the agents have finished. During the run `_REGISTRY` is still `None`, so every
`get_certified_metric` call answers `UNSUPPORTED`. The tool exists, is bound,
and cannot work.

Why the test suite did not catch it: `tests/test_p3_math_wiring.py::test_7`
asserts `hasattr(math_tools, "set_run_registry")` — that the function exists,
never that the graph calls it. A passing test over dead code.

Impact: the analyst is told "quote certified numbers, never estimate", has no
way to obtain one, and falls back to prose. This is the F2 defect (a model's
figures presented as facts) surviving behind a wired-looking tool. It does not
break the gate — the gate's own registry is independent and still blocks — but
it defeats the reason the tools were added.

Also observed, and part of the same gap: the `calculate_*` tools compute a
value and return it, but never write it into `_REGISTRY`. A metric the agent
had certified is lost, and the gate must recompute it from state or report text.

**Fixed in phase 7** (branch `fix/p7-agent-registry`, see §R2 resolution below).

### R3 — the startup line does not name the build *(low)*

`python -m webapp` prints the version but not the commit or the load time:

```console
$ python -m webapp
TradingAgents web console v0.8.0: http://127.0.0.1:8000/  (Ctrl-C to stop)
```

The audit asked for startup diagnostics recording build ID, commit SHA and the
version of the critical modules. All three are available via `build_info()`;
none is logged at startup. The information exists in `/api/health` only, which
an operator must know to query. Fixed in this branch (§4).

## 4. Changes in this branch

- `webapp/__main__.py` — the startup line now reports the build (commit, build
  id, python) and the validator status, so a stale or incomplete deployment is
  visible in the console that started it, without an HTTP call (R3). Output on
  this machine:

  ```console
  TradingAgents web console v0.8.0: http://127.0.0.1:8000/  (Ctrl-C to stop)
    build 0541d4c@2026-11-10T10:27:04+00:00 (commit 0541d4c, python 3.15.2)
    validators loaded: arithmetic_guard, execution_gate, gate_integration,
      math_extraction, math_precision, math_technical, math_valuation,
      metric_registry, risk_engine
  ```

- `tests/test_p1_runtime_verification.py` — 9 tests: the startup diagnostics,
  plus explicit reproductions of R1 and R2 that fail if those defects change
  shape, so neither can be closed by deleting the evidence.

## 4.1 R2 is worse than "the tool is inert": runs are not isolated

While writing the test for R2, the defect appeared in a second form. With the
full suite, `get_certified_metric` returned `1.4801` — a P/B computed from a
**different test's** inputs, left in the module global by an earlier test:

```console
$ pytest tests/test_deterministic_math.py tests/test_p1_runtime_verification.py
E   assert '1.4801' is None
```

`run_registry.py`'s docstring states the rule this breaks: *"Per run, never
shared. A registry belongs to an analysis; a process-wide one would let one
analysis certify another's figures."* The module stores the registry in a
process-wide global, so any analysis in the same process reads whatever the
last one attached. The test was fixed to reset the global first; the defect it
exposed is real and recorded here.

## 5. Test results

```console
$ pytest tests/ -q
4 failed, 2303 passed, 12 skipped, 1 deselected, 100 subtests passed
```

The four failures are **pre-existing and unrelated**: `test_financial_math_engine.py::TestProperties`
requires the `hypothesis` package, which is not installed here. Verified by
stashing this branch's changes and running the same file on clean `custom` —
the same four fail, `ModuleNotFoundError`. Not fixed here: it is a missing dev
dependency, not a defect in this branch.

P1-specific suites, all passing:

```console
$ pytest tests/test_p1_runtime_gate_integration.py tests/test_p1_build_info.py \
         tests/test_p1_runtime_verification.py tests/test_p3_math_wiring.py -q
61 passed
```

## 6. What this branch does not do

- No change to trading behaviour, the Execution Gate, or the risk rules.
- No live broker access; no order was created or sent.
- No restart of the running service.
- No fix for R1 or R2 — both are recorded with reproductions, deliberately
  deferred.


## R2 resolution (phase 7)

R2 is closed in branch `fix/p7-agent-registry`. The fix needed three parts, and
leaving any one out would have looked fixed while remaining broken.

### 1. The binding is a `ContextVar`, not a module global

`math_tools._REGISTRY` was a process-wide global. That is wrong twice: the web
console runs several analyses at once, so one analysis's registry would answer
another's tool calls — the leak reproduced in this document's R2 section, where
the full suite returned `1.4801` from a different test — and a global survives
the run that set it, so the next analysis read stale metrics.

It is now `_run_registry_var: ContextVar`, the same mechanism
`dataflows/config` already uses for a run's vendors. LangGraph carries the
context into tool calls.

### 2. The graph binds it for the whole run

`_run_graph` creates the registry *before* the agents start and holds the
binding until the run ends:

```python
run_registry = MetricRegistry(run_id=f"{company_name}-{trade_date}")
token = set_run_registry(run_registry)
try:
    ...  # the agents work; their tool calls now read this registry
    self.record_decision(..., run_registry=run_registry)
finally:
    reset_run_registry(token)
```

`_evaluate_reliability` certifies that same registry (`_complete_run_registry`
adds the figures that only exist once the agents have finished) instead of
building a second one. Building a second registry would mean the gate certifies
a different set of records from the figures the analysts were given.

The binding is inline in `_run_graph` rather than in a helper, because callers
drive that method directly (`partial(TradingAgentsGraph._run_graph, graph)` in
`test_memory_log.py`); a `self.<helper>` call would resolve against the caller.

### 3. The tools record what they compute

`_payload()` — the single exit point of every `calculate_*` tool — registers the
`MetricRecord` in the bound registry before answering. Previously a value the
agent had legitimately computed was returned to the model and then lost, and the
gate had to recompute it from state or prose.

### Evidence

```
registry has usable P/B: True        # the gate's own registry
agent sees (inside the binding): 1.6041 | VERIFIED
outside the binding:                     None
```

And through the real graph path:

```
memory entries: 1
registry after run (must be None): None
```

### Regression tests

`tests/test_p1_runtime_verification.py::TestR2AgentRegistry` now pins the fixed
behaviour instead of the defect: an agent reads a certified metric inside a run,
answers `UNSUPPORTED` outside one, two runs do not share a registry, a tool
records what it computed, and the binding is restored after an exception.
`tests/test_deterministic_math.py` had three tests that bound a registry without
releasing it — the leak R2 describes, in test form — and now uses the
`certified_registry` context manager.

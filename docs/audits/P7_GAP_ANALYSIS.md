# P7 — Audit tecnico e gap analysis

**Data:** 2026-11-11
**Repository:** `muzzye/TradingAgents`
**Branch:** `custom` @ `52afe8a` — versione `0.8.27`
**Tipo:** audit read-only. Nessun file applicativo modificato, nessun branch, nessuna PR.

Fix già integrati e verificati: FIX #1 (ricavi SEC), FIX #2 (P/B e provenienza
contabile), FIX #3 (integrità del prezzo), FIX #4 (claim P/E).

Questo documento non li riapre. Elenca ciò che **non** è coperto e ciò che è
dichiarato coperto ma non lo è.

---

## Sintesi

| ID | Difetto | Gravità | Riprodotto | Raggiungibile in produzione |
|---|---|---|---|---|
| D5 | La produzione non valuta il trigger di ingresso: ogni run è `WAIT` | **P1** | sì | **sì** |
| E2 | Le claim refutate non possono bloccare la decisione | **P1** | sì | **sì** (per assenza) |
| E1 | Il Fact Checker non è collegato al grafo né al gate | **P1** | sì | **sì** (per assenza) |
| A6 | `revenue_growth` non rifiuta un confronto trimestre-vs-TTM | P2 | sì | no |
| C5 | `stress_exit` / `size_position` sono long-only senza dichiararlo | P2 | sì | no |
| E3 | `debate.py` non è importato da nessun modulo del grafo | P2 | sì | no |

I tre difetti P1 condividono la stessa causa: una funzione costruita e testata
in isolamento, mai collegata al percorso reale, e una documentazione che la
descrive come attiva.

---

## D5 — La produzione non valuta mai il trigger di ingresso

### Difetto

`evaluate_decision()` riceve `trigger_met: bool = False` come parametro del
chiamante. Il grafo non lo passa e nessun modulo del grafo lo calcola.

Conseguenza: dal percorso di produzione il gate produce **sempre** `WAIT` con
`ENTRY_TRIGGER_NOT_MET`, anche quando l'entry del piano è già stata superata e
tutti i dati sono verificati. `PROPOSE_PAPER_ORDER` è irraggiungibile.

Fail-closed, quindi non pericoloso per il capitale — ma significa che il sistema
non può fare ciò per cui il gate esiste.

### Evidenza

Il percorso di produzione, in `tradingagents/graph/trading_graph.py`:

```python
record, decision = evaluate_decision(
    company_name, trade_date, final_state,
    snapshot_id=f"{company_name}-{trade_date}",
    model_versions={
        "deep": self.config.get("deep_think_llm"),
        "quick": self.config.get("quick_think_llm"),
    },
    fundamentals_filings=filings,
    used_fundamentals_period_end=used_end,
    metric_registry=registry,
    **_portfolio_sizing_inputs(self.config, portfolio, company_name),
)
```

`"trigger_met" in trading_graph.py` → **`False`**.

Stesso identico stato, valutato con e senza il kwarg che la produzione non passa:

```
entry=49.00  price=50.80  (entry superata)
  kwargs di produzione          -> action=WAIT                  order=False
  reasons=['ENTRY_TRIGGER_NOT_MET']

  stessi kwargs + trigger_met=True -> action=PROPOSE_PAPER_ORDER  order=True
```

L'unica differenza fra i due esiti è il parametro che la produzione non fornisce.

### Perché la suite non lo rileva

50 test chiamano `evaluate_decision(..., trigger_met=True)`. Nessuno simula il
default. La copertura misura la funzione, non il percorso.

### Contraddizione documentale

`docs/audits/P6_E2E_CERTIFICATION.md` (riga 115) afferma:

> "With a book supplied the same pipeline proposes a paper order (see P5's evidence)."

Falso per il percorso di produzione: con un book valido l'esito resta `WAIT`.

### Correzione proposta

Calcolare `trigger_met` in modo deterministico prima della chiamata al gate,
confrontando l'entry del piano con il prezzo qualificato dello snapshot. La
condizione di attivazione è già scritta nel piano (`Entry 560.00`); manca solo il
confronto. Non introdurre una nuova euristica e non leggere il valore dal testo
del Trader.

### Test di accettazione

1. Grafo con fixture congelate, portfolio valido, dati verificati, entry 540.00
   e prezzo 550.00 → `PROPOSE_PAPER_ORDER`. **Oggi fallisce (`WAIT`).**
2. Entry 560.00 e prezzo 550.00 (entry non raggiunta) → `WAIT`, `ENTRY_TRIGGER_NOT_MET`.
3. Nessun prezzo verificato → `WAIT`/`BLOCK`, mai un ordine.
4. Mercato chiuso → `WAIT` con `MARKET_CLOSED`.
5. I 50 test esistenti che passano `trigger_met=True` restano verdi.
6. Il test deve passare dal grafo compilato, non da `evaluate_decision` diretto:
   è precisamente la distinzione che ha nascosto il difetto.

---

## E2 — Le claim refutate non possono bloccare la decisione

### Difetto

Il contratto §6 richiede che una claim `REFUTED` bloccante fermi la decisione.
`gate_integration.py` non importa né chiama `check_claims`, `FactCheckReport` o
`ClaimStatus`:

```
fact_check          in gate_integration: False
ClaimStatus         in gate_integration: False
check_claims        in gate_integration: False
FactCheckReport     in gate_integration: False
```

### Evidenza

In ogni decisione prodotta dal gate, `record.fact_check` è `None`:

```python
rec, dec = evaluate_decision("T", "2026-11-10", state, snapshot_id="e1",
                             metric_registry=reg, equity=100_000.0,
                             cash=50_000.0, risk_budget=1000.0,
                             max_symbol_exposure=0.05, trigger_met=True)
print(rec.fact_check)   # None
```

### Contraddizione documentale

`CHANGELOG.md` (riga 1054, sezione "Independent Fact Checker and bounded
debate (Phase 5)") descrive il comportamento al presente:

> "A refuted *blocking* claim stops the decision."

Non è vero nel percorso di produzione: nessuna claim può raggiungere il gate.

### Correzione proposta

Collegare `check_claims` nel gate, dove report e fatti verificati coesistono —
stessa collocazione già usata per `reconcile_revenue_claims` (FIX #1.1), che è
stata spostata lì perché il Data Quality node gira in parallelo agli analisti e
non vede i report.

### Test di accettazione

1. Report con una claim causale refutata (`EVENT_AFTER_LAST_MARKET_SESSION`) →
   `fact_check` popolato e `action=BLOCK`.
2. Stessa claim con una sessione post-evento disponibile → nessun blocco.
3. Claim corretta → nessun falso positivo.
4. `audit.data_quality.fact_check_summary` popolato nel JSON.

---

## E1 — Il Fact Checker non è collegato al percorso

### Difetto

`tradingagents/dataflows/fact_checker.py` esiste, ha test propri, e non è
raggiungibile dalla produzione. Gli unici import sono:

```
tradingagents/dataflows/audit.py:26     from ...fact_checker import FactCheckReport   # solo il tipo
tradingagents/dataflows/debate.py:19    from ...fact_checker import ClaimStatus, FactCheckReport
```

e `debate.py` non è importato da nessun modulo del grafo:

```
grep "dataflows.debate" tradingagents/ --include=*.py  ->  nessun risultato
```

### Evidenza

`FactCheckReport()` è costruito in un solo punto in tutto il codice di
produzione — dentro `fact_checker.py:237`, cioè nella funzione che nessuno
chiama.

### Correzione proposta

È la stessa di E2. Un'unica modifica chiude entrambi.

---

## A6 — `revenue_growth` non rifiuta un confronto trimestre-vs-TTM

### Difetto

`period_granularity()` in `math/definitions.py` distingue correttamente
`quarter` / `ttm` / `year`, ma `revenue_growth` in `run_registry.py` non lo
usa: divide i due valori senza verificare che siano confrontabili.

### Evidenza

```python
RunInputs(revenue_consolidated=105_647_000_000.0,   # Q3 2026
          revenue_prior=400_000_000_000.0,          # un TTM
          revenue_provenance={"validation_status": "VERIFIED",
                              "concept": "Revenues", "period_end": "2026-09-30"})

revenue_growth = -73.5883   status=VERIFIED   usable=True
```

Un calo del 73.59% certificato, prodotto da un confronto fra grandezze diverse.

### Perché non è raggiungibile in produzione

`_comparative()` in `sec_revenue_inputs.py:132` seleziona esplicitamente lo
stesso trimestre dell'anno prima, confrontando le chiavi per data:

```python
target_year = int(latest_end[:4]) - 1
target_end = f"{target_year}{latest_end[4:]}"
```

e `revenue_prior` non viene mai estratto dal testo (`extraction.py:242` legge
solo `state["revenue_prior"]`, che il Data Quality node scrive dalla SEC).

Quindi è un difetto dell'API, non del percorso. Resta un rischio: chiunque chiami
`build_run_registry` direttamente può certificare un confronto invalido.

### Correzione proposta

In `revenue_growth`, quando entrambe le provenance dichiarano un periodo,
rifiutare il confronto se le granularità differiscono. `NOT_COMPUTABLE` con
motivo, non `VERIFIED`.

### Test di accettazione

1. Trimestre vs TTM → non certificato, con motivazione.
2. Q3 2026 vs Q3 2025 → certificato (`+11.2402%`, il caso reale).
3. FY vs FY → certificato.
4. Provenance assente su un lato → comportamento attuale invariato.

---

## C5 — `stress_exit` e `size_position` sono long-only senza dichiararlo

### Difetto

Entrambe assumono che lo stop sia **sotto** l'entry: sottraggono sempre lo
stress e calcolano `stressed_risk = entry - stressed_exit`. Per una posizione
SHORT questo inverte il segno.

### Evidenza

```python
stress_exit(entry=560.0, stop=568.0, atr=9.52, gap_atr=1.0, slippage_pct=0.0005)
  -> stressed_exit=558.2   extra_loss_per_share=9.8
```

Uno stop SHORT a 568 con 9.52 di ATR viene stressato **a 558.20**, cioè a un
prezzo *migliore* dello stop. Lo stress premia invece di penalizzare.

### Perché non è raggiungibile in produzione

Il gate usa `size_with_exposure_cap`, che gestisce la direzione correttamente:

```python
if direction == "SHORT" and stop <= entry:
    return ExposureSizingResult(0, ..., (RiskReason.INVALID_STOP,))
...
risk_per_share = abs(entry - stop)
stressed_risk = risk_per_share + stressed_extra     # additivo, non sottrattivo
```

`stress_exit` è chiamata solo da `size_position`, e `size_position` solo dai
test. Sono API pubbliche del modulo, quindi il rischio è di un uso futuro
scorretto, non un difetto attivo.

### Correzione proposta

Due opzioni, in ordine di preferenza:
1. aggiungere un parametro `direction` e gestire il segno;
2. documentare esplicitamente `long-only` nella docstring e rinominare in modo
   che il vincolo sia visibile (`stress_long_exit`).

---

## E3 — `debate.py` non è nel grafo

Il controller del dibattito (convergenza, cap dei round, separazione fra
disaccordo di rating e di trigger) esiste e ha test propri, ma non è importato da
nessun modulo del grafo. Il dibattito reale è governato da
`graph/conditional_logic.py`.

Da chiarire prima di considerarlo un difetto: potrebbe essere una scelta
consapevole (il controller è disponibile per un uso programmatico). Non ho
trovato documentazione che lo dica in un senso o nell'altro — per questo è P2 e
non P1.

---

## Rischi non riprodotti

| Rischio | Perché resta ipotetico |
|---|---|
| Tolleranza relativa 1% sui multipli (FIX #4) | ±0.254 su un P/E di 25.4. Non ho trovato un caso in cui mascheri un errore *materiale*; il criterio non è dichiarato nel contratto |
| `price_to_book` `REFUSED` con prezzo assente, `pe_trailing` `NOT_COMPUTABLE` | Incoerenza semantica fra due strade, ma entrambe non certificano: nessun ordine passa |
| `snapshot_id` presente solo in `audit.snapshot_id` | Persistito, ma assente dal verdict e dal run record: chi legge il verdict non lo trova |
| Timeout di un provider | Non riproducibile offline senza un mock del vendor |

---

## Copertura test per area

| Area | File di test | Stato |
|---|---|---|
| Ricavi SEC (FIX #1) | `test_fix_registry_consolidated_revenue.py`, `test_fix11_sec_claim_reconciliation.py` | ✅ coperta |
| P/B e provenienza (FIX #2) | `test_fix2_pb_price_source.py` | ✅ coperta |
| Integrità prezzo (FIX #3) | `test_fix3_pe_price_source.py` | ✅ coperta |
| Claim P/E (FIX #4) | `test_fix4_pe_claims.py` | ✅ coperta |
| Risk engine | `test_risk_engine.py`, `test_financial_math_engine.py` | ✅ unit; ❌ nessun test sul percorso |
| Execution Gate | `test_execution_gate.py`, `test_gate_integration.py` | ⚠️ 50 test con `trigger_met=True`, **zero con il default** |
| Fact Checker | `test_fact_checker.py` | ⚠️ unit only, non collegato |
| Causal guard / freshness | `test_causal_guard.py`, `test_fundamentals_lookahead.py` | ✅ coperta |
| Audit / run record | `test_audit.py`, `test_usage.py` | ✅ coperta |

---

## Risultati test (baseline dell'audit)

```
ruff check .                →  All checks passed
pytest tests/ -q            →  2712 passed, 12 skipped, 1 deselected
pytest --collect-only       →  2724/2725 collected
```

| Esito | Conteggio |
|---|---|
| PASS | 2712 (+100 subtests) |
| FAIL | 0 |
| SKIPPED | 12 — cache SEC assente per AAPL/NVDA/BRK-B |
| NOT RUN | 0 |

I 12 skip richiedono `~/.tradingagents/cache/sec_edgar/` popolata per quei
ticker; forzarli significherebbe chiamare `data.sec.gov`, che risponde 403 senza
uno User-Agent appropriato.

La suite è verde per intero. Questo è il punto centrale dell'audit: **una suite
verde non ha rilevato nessuno dei tre difetti P1**, perché tutti misurano le
unità e nessuno misura il percorso di produzione.

---

## Funzionalità complete — non reimplementare

- Ricavi SEC: concetto consolidato, periodo, componente-vs-totale, contraddizione EN/IT
- P/B: prezzo qualificato, provenienza contabile, coerenza periodo/classe, forma per-azione e aggregata
- Integrità prezzo: 6 metriche derivate, distinzione assente / presente-non-qualificato / verificato
- Claim P/E: 17 forme (trailing/forward/storico, IT/EN, virgola/punto, prima/dopo, parentesi/trattino/virgola)
- Risk engine: budget, cap di esposizione, gap/slippage, stop non garantito
- Execution Gate: idempotenza, retry, 6 ragioni di blocco, separazione rating/azione
- Audit: struttura completa con provenance, metric registry, reason codes

---

## Intervento prioritario proposto

**FIX #5 — cablare il trigger di ingresso nel percorso di produzione** (chiude D5)

Razionale: è il difetto con l'impatto più diretto sulla funzione del sistema, ed
è quello per cui esiste già una condizione dichiarata nel piano (`Entry 560.00`)
che aspetta solo di essere confrontata con il prezzo.

I test di accettazione sono elencati nella sezione D5. Il requisito
metodologico, comune a D5, E1 ed E2, è che il test passi dal **grafo compilato**
e non da una chiamata diretta: è esattamente la distinzione che ha nascosto
tutti e tre.

---

## Raccomandazione

**FIX_REQUIRED**

Tre difetti P1 riprodotti con evidenza eseguibile, tutti dello stesso tipo: una
funzione costruita e testata in isolamento, mai collegata al percorso reale, e
una documentazione che la descrive come attiva. In due casi su tre
(`P6_E2E_CERTIFICATION.md:115`, `CHANGELOG.md:1054`) la documentazione afferma un
comportamento che il codice non ha.

Non `READY_FOR_SMOKE_TEST`: uno smoke test end-to-end mostrerebbe sempre `WAIT` e
non distinguerebbe un sistema corretto da uno che non valuta nulla.

Non `INSUFFICIENT_EVIDENCE`: i tre difetti sono riprodotti.

### Nota di metodo

La suite verde è la misura sbagliata per questa classe di difetti. Suggerimento
per le fasi successive: ogni comportamento dichiarato dovrebbe avere almeno un
test che lo esercita **dal punto di ingresso reale** (`propagate()` o `_run_graph`),
non solo dall'unità che lo implementa.

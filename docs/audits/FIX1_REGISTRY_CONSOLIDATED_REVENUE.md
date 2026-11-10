# FIX #1 — `consolidated_revenue` dal filing SEC al Metric Registry

**PR:** https://github.com/muzzye/TradingAgents/pull/37
**Branch:** `fix/registry-consolidated-revenue` (base `origin/custom`)
**Commit:** `a8045e7`
**Versione:** 0.8.8 (patch)
**CI:** 8/8 check verdi — nessun merge

---

## Sintomo riportato

Report TradingAgents 0.8.7, BRK-B, 10 novembre 2026 ore 16:15.

Il report dichiarava:

- Ricavi Q3 2026: **105.647.000.000 USD**
- Ricavi Q3 2025: **94.972.000.000 USD**
- Crescita consolidata: **+11,24%**

La sezione Certified Metrics riportava invece:

```
consolidated_revenue = NOT_COMPUTABLE
reason: "no consolidated revenue was filed or reported"
```

Le due affermazioni non possono essere entrambe vere.

---

## A. Root cause

| | |
|---|---|
| **File** | `tradingagents/dataflows/math/run_registry.py` (consumatore) e il produttore assente |
| **Funzione** | `build_run_registry()` legge `RunInputs.revenue_consolidated`, che nessuno popolava da fonte SEC |
| **Causa esatta** | L'unica sorgente era `math/extraction.py`, che estrae una cifra dal **testo** del report con una regex |

```python
# tradingagents/dataflows/math/extraction.py
"revenue_consolidated": (
    r"(?:total\s+revenue|consolidated\s+revenue|ricavi\s+consolidati)"
    r"\D{0,24}?(\d[\d,\.]*)\s*(billion|million|bn|mn|b|m)?"),
```

Il fatto strutturato `Revenues` che il Fundamentals Analyst aveva scaricato tramite
`get_income_statement` **non raggiungeva mai il registry**.

```console
$ grep -rn '"revenue_consolidated"' tradingagents/ --include=*.py
math/run_registry.py:215    # legge
math/extraction.py:238      # scrive, ma dal testo del report
# nessun altro writer di produzione
```

### Evidenza del difetto (prima della modifica)

```console
$ pytest tests/test_fix_registry_consolidated_revenue.py -q
11 failed, 2 passed
```

E il comportamento in isolamento:

```
labelled 'total revenue'   value=105647000000.0 status=VERIFIED
table row                  value=None           status=NOT_COMPUTABLE
prose only                 value=None           status=NOT_COMPUTABLE
```

**Interpretazione:** la metrica esisteva ed era corretta *solo* quando il modello
sceglieva esattamente le parole attese dalla regex. Una cifra che il filing dichiara
in modo autorevole era resa condizionale alla formulazione di un LLM. La run delle
16:15 ha colpito il ramo `NOT_COMPUTABLE`.

---

## B. Fix

| File | Modifica |
|---|---|
| `tradingagents/dataflows/math/sec_revenue_inputs.py` | **nuovo** — `revenue_inputs_from_sec()` legge il payload `companyfacts`, seleziona il concetto consolidato tramite l'ordinamento `concept_resolution` già esistente, restituisce `RunInputs` con valore, prior e provenienza |
| `tradingagents/graph/data_quality_node.py` | il nodo risolve i ricavi del filing e pubblica `revenue_consolidated` / `revenue_prior` / `revenue_provenance` nello stato del run |
| `tests/test_fix_registry_consolidated_revenue.py` | **nuovo** — T1–T12, poi esteso dai commit di FIX #1.1 |
| `CHANGELOG.md` | sezione 0.8.8 |
| `tradingagents/__init__.py` | 0.8.8 |

### Modifica minima nel nodo

```python
revenue_inputs = _revenue_inputs_from_provider(
    sec_facts_provider, ticker, trade_date)
...
if revenue_inputs is not None:
    update["revenue_consolidated"] = revenue_inputs.revenue_consolidated
    update["revenue_provenance"] = revenue_inputs.revenue_provenance
    if revenue_inputs.revenue_prior is not None:
        update["revenue_prior"] = revenue_inputs.revenue_prior
```

Il default, quando il nodo è costruito senza provider, legge i companyfacts da SEC
EDGAR con lo **stesso fetch cachato** del controllo di freschezza. Sorgente non
disponibile → `None` → la metrica resta `NOT_COMPUTABLE`.

### Perché è minimale

- Registry, renderer del report e contratto `MetricRecord` **non toccati**.
- Nessun secondo sistema di metriche.
- Nessun valore iniettato nel report.
- **Riuso, non duplicazione:** `sec_edgar._as_of_with_provenance` ordina già i tag
  per significato e rifiuta di mescolare scope nella stessa riga, quindi un
  componente non può riempire un periodo consolidato.

---

## C. Test

**Comando:**

```bash
pytest tests/test_fix_registry_consolidated_revenue.py -q
```

| ID | Test | Prima del fix | Dopo il fix |
|---|---|---|---|
| T1 | `test_q3_2026_revenue_is_the_consolidated_figure` | pass *(resolver già corretto)* | pass |
| T2 | `test_the_concept_and_period_survive_normalisation` | pass | pass |
| T3 | `test_the_registry_holds_consolidated_revenue_from_the_filing` | **fail** *(modulo assente)* | pass |
| T4 | `test_the_metric_is_certified_only_from_valid_sec_inputs` | **fail** | pass |
| T4b | `test_the_provenance_is_carried_into_the_record` | **fail** | pass |
| T5 | `test_contract_revenue_alone_does_not_become_the_total` | **fail** | pass |
| T6 | `test_an_empty_payload_certifies_nothing` | **fail** | pass |
| T6b | `test_a_fact_filed_after_the_analysis_date_is_not_used` | **fail** | pass |
| T7 | `test_the_table_shows_the_value_period_and_status` | **fail** | pass |
| T8 | `test_the_certified_count_matches_the_records` | **fail** | pass |
| T9 | `test_sec_payload_to_certified_table_through_the_registry` | **fail** | pass |
| T10 | `test_pb_and_eps_are_untouched_by_the_revenue_path` | **fail** | pass |
| T10b | `test_revenue_growth_follows_the_consolidated_pair` | **fail** | pass |
| T11 | 3 test — il nodo pubblica i ricavi nello stato | **fail** | pass |
| T12 | 2 test — payload reale in cache di Berkshire | **fail** | pass |

```
Prima:  11 failed, 2 passed
Dopo:   18 passed
```

> **Aggiornamento 2026-11-10.** I commit di FIX #1.1 hanno esteso lo stesso file:
> oggi contiene 29 test e passano tutti (`29 passed`). I conteggi sopra restano
> come documentazione del momento in cui la correzione è stata scritta.

### Regressione completa

```console
$ pytest tests/ -q
4 failed, 2466 passed, 12 skipped, 1 deselected, 100 subtests passed
```

I 4 fallimenti sono **pre-esistenti**: `tests/test_financial_math_engine.py::TestProperties`
richiede il pacchetto `hypothesis`, non installato su questa macchina.

```console
$ ruff check .
All checks passed!
```

---

## D. Evidenza del nuovo MetricRecord

Prodotto dal codice sul payload SEC **reale** in cache (`CIK0001067983.json`):

```
value      : 105647000000.0
unit       : USD
period     : 2026-07-01 -> 2026-09-30
concept    : Revenues
accession  : 0001193125-26-452412
cik        : 0001067983
scale      : 1
validation : VERIFIED
formula_id : sec_filed_fact
growth     : 11.24 %
```

Riga renderizzata nel report:

```
| `consolidated_revenue` | 105647000000.0000 | USD | `sec_filed_fact` | 2026-09-30 | certified |
```

### Percorso end-to-end verificato

```
1. Valore SEC originale : 105647000000.0
2. Dopo la normalizzazione: 105647000000.0
3. Concetto             : Revenues
4. Periodo              : 2026-09-30
5. Registry             : value=105647000000.0 status=VERIFIED usable=True
6. Tabella Certified    : ... | certified |
```

---

## E. Git

| | |
|---|---|
| **Branch** | `fix/registry-consolidated-revenue` |
| **Base** | `origin/custom` |
| **Commit** | `a8045e7` |
| **PR** | https://github.com/muzzye/TradingAgents/pull/37 |
| **CI** | 8/8 check verdi (tests py3.11–3.14, ruff, P0 reliability, e2e reliability, clean-install smoke) |
| **Merge** | nessuno — in attesa di revisione |

---

## Limiti rispettati

Non modificati: P/B, EPS, calcoli ATR, position sizing, strategie, prompt LLM,
parametri dei modelli, regole di autorizzazione dell'Execution Gate.

Nessun ordine inviato a broker. Nessun riavvio di produzione.
Nessun valore BRK-B hardcoded nel codice applicativo: le costanti 105.647 / 94.972
vivono solo nelle fixture di test.

---

## Note operative

1. **Il fix entra in azione quando il nodo `Data Quality Check` gira**, cioè nel
   percorso normale del grafo. Se SEC EDGAR non è raggiungibile e la cache è
   assente, la metrica resta `NOT_COMPUTABLE` — fail-closed, non un valore inventato.

2. **`docs/audits/TOKEN_COST_REVIEW.md`** è un file non tracciato di una richiesta
   precedente: **non** fa parte di questo commit.

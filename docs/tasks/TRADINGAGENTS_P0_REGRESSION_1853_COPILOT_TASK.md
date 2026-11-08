# TradingAgents — P0 Regression Fix: report BRK-B 08/11/2026 ore 18:53

**Tipo:** task di implementazione per GitHub Copilot Agent / Codex  
**Priorità:** P0 — regressioni di data quality e sicurezza del paper trading  
**Repository:** TradingAgents esistente (non ricreare il progetto)  
**Branch base / destinazione PR:** `custom` (**mai `main`**)  
**Modelli attuali:** quick `deepseek-v4.1-flash:cloud`, deep `glm-5.3:cloud`  
**Output obbligatorio:** codice, test riproducibili, CI, `CHANGELOG.md`, commit, push del feature branch, Pull Request verso `custom`; **nessun merge automatico**.

> **Regola principale:** i miglioramenti non devono essere solo istruzioni nei prompt. Devono esistere **validazioni deterministiche nel codice**, esiti machine-readable, test che dimostrino il blocco e protezione del percorso che potrebbe inviare ordini *paper*. Un report `HOLD` può descrivere scenari futuri, ma non autorizza ordini.

## 0. Prima di iniziare

1. Leggi nel repository: `AGENTS.md`, `.github/copilot-instructions.md` se presente, `docs/specs/TRADINGAGENTS_RELIABILITY_CONTRACT.md` se presente, `docs/tasks/TRADINGAGENTS_P0_DATA_QUALITY_COPILOT_TASK.md` se presente, `CHANGELOG.md`, test e workflow CI esistenti. Il presente task è **incrementale** e corregge regressioni emerse **dopo** quello delle 17:05: non duplicare funzionalità già funzionanti.
2. Ispeziona **realmente** l'architettura, i modelli di dato, l'orchestrazione LLM, l'adapter SEC EDGAR, i provider yfinance, il percorso ordini, la configurazione GitHub Actions, i test e l'attuale `git status`. Non supporre nomi di file o comandi non verificati.
3. Parti dall'ultimo `origin/custom` e crea un nuovo branch, per esempio `fix/p0-1853-regression-gates`. Non pushare direttamente su `custom`. Se vecchie istruzioni parlano di `main`, prevale **`custom`**; correggi solo le istruzioni incoerenti necessarie.
4. Preserva tutte le modifiche locali: niente `reset --hard`, `clean -fd`, force-push, sostituzioni distruttive o cancellazioni arbitrarie. Se il worktree è sporco, evita di perdere lavori dell'utente; usa una worktree/strategia sicura se disponibile.
5. **Paper trading soltanto.** Non creare connessioni live, non eseguire ordini veri, non leggere o stampare segreti nei log. Il solo modello LLM non può autorizzare un trade.
6. Non cambiare i provider LLM né aggiungere eccezioni per `BRK-B`. Usa BRK-B come fixture; implementa regole generalizzabili ad altri ticker e mercati.
7. Prima crea test che riproducano i difetti; registra il risultato baseline. Poi correggi il codice e verifica l'intera suite. Se il repository non dispone di un adapter o di un endpoint richiesto, dichiara il limite e implementa una protezione fail-closed verificabile.

## 1. Difetti osservati nel report delle 18:53:34

**Contesto congelato della fixture:** `analysis_as_of=2026-11-08T18:53:34+01:00` (report), mercato `NYSE`, simbolo `BRK-B`. Ultima seduta: **venerdì 6 novembre**. Earnings Q3 pubblicati **sabato 7 novembre**. Prima seduta post-evento: **lunedì 9 novembre**. I dati di riferimento sono usati esclusivamente come *fixture*, non come costanti di produzione.

| ID | Gravità | Evidenza del report | Comportamento richiesto |
|---|---|---|---|
| `EVENT-REG-001` | **P0** | News: `NOT_YET_OBSERVABLE`; Sentiment: afferma che il movimento di **−0,01% del 6 novembre** sia una reazione ai conti del 7 novembre e segno di prezzo equilibrato | Rileva il claim temporalmente impossibile, contrassegnalo `INVALID_EVENT_CAUSALITY`; rimuovilo dalle evidenze usabili e non lasciare che influenzi il verdetto |
| `FILING-REG-002` | **P0** | Fundamentals usa patrimonio **747,9 mld USD al 30/06/2026** benché il filing Q3 al **30/09/2026** sia già stato pubblicato il 7/11 | Recupera/riconosci il filing più recente disponibile all'`as_of`; se il tool restituisce solo Q2, prova un recupero sicuro o segnala `STALE_FUNDAMENTALS` e blocca decisioni esecutive dipendenti dal dato |
| `FACT-REG-003` | **P0** | News conosce **buyback Q3 di circa 9,6 mld USD**; Fundamentals ragiona sui soli buyback del primo semestre e li definisce modesti | Confronta **stesso periodo, stessa metrica e fonte**; distingui dati storici da dato corrente, senza convertirli in contraddizione falsa né ignorare conflitti materiali |
| `VAL-REG-004` | **P0** | P/B **1,63x** basato su periodi e denominatori misti; il testo presenta anche book value per Classe A/B discordanti | Riconcilia equity attributable, azioni B-equivalenti, classe e prezzo con timestamp espliciti; se impossibile, `UNRECONCILED_VALUATION`; non produrre un P/B 'verificato' |
| `MATH-REG-005` | **P1** | TTM **85,77 mld** descritto come in accelerazione rispetto a **88,99 mld** del 2024; altre affermazioni miscelano TTM, FY e YTD | Confronti trend solo su periodi omogenei e in ordine corretto; blocca contraddizioni aritmetiche o categorizzale come `INVALID_TREND_COMPARISON` |
| `METRIC-REG-006` | **P1** | EPS **5,94** vs **5,88** attesi (social/retail) accostato agli utili senza tipo | Conserva `GAAP_EPS`, `OPERATING_EPS`, `ADJUSTED_EPS`, consenso e fonte in campi diversi; nessun 'beat' verificato senza comparabilità di definizione e periodo |
| `RISK-REG-007` | **P0** | Entrata **560 $**, stop **552 $**, perdita **8 $/azione**, rischio **1% per tranche** senza capitale/exposure limit effettivo | Calcola in codice quantità e nozionale soggetti simultaneamente a rischio, liquidità, peso massimo, lotto, slippage; non confondere budget allo stop con massimo capitale investibile |
| `RISK-REG-008` | **P0** | Tre tranche a prezzi diversi possono accumulare rischio; per alcune viene riutilizzato stop 552 $ | Ricalcolo per ciascun ingresso e rischio **aggregato** su tutte le posizioni e gli ordini aperti; rispetta il tetto di rischio incrementale complessivo configurato |
| `ENTRY-REG-009` | **P0** | Una condizione dice «apertura sotto **559,61** annulla il setup»; un'altra permette acquisto su gap ribassista verso **557–562** | Formalizza trigger, istante di osservazione, priorità, scadenza e condizioni mutual-exclusive. Se conflittuali, `INVALID_ENTRY_CONDITION`, nessun ordine |
| `EXEC-REG-010` | **P0** | Rating **HOLD** e descrizione di tranche ma nessuna prova di gate che impedisca ordini | `strategic_rating` distinto da `execution_action`; senza contesto portafoglio validato o trigger post-evento, `authorized_orders=[]`, `execution_approved=false` |

**Controlli noti già migliorati da preservare:** dati OHLCV del 6/11, indicatori coerenti con uno snapshot verificato, assenza dividendi BRK-B, data evento riconosciuta da News, differenza fra core e capitale incrementale. Non peggiorarli durante il fix.

**Nota epistemica:** per il Q3 utilizzare il filing SEC disponibile **a quella data**, non un dato pubblicato in futuro. I valori di controllo del caso Berkshire, da trattare come *fixture ufficiale* con fonte, sono circa **761,368 mld USD** di equity attribuibile e **1.303,907 mld USD** di attività al 30/09/2026; recuperare e verificare classe azionaria e share count prima di affermare il P/B. I valori non vanno hardcodati nel codice di produzione.

## 2. Architettura richiesta: percorso obbligatorio dei controlli

```text
Vendor / SEC / Price / Macro / News
  -> Source + AsOf Registry
  -> Fundamentals Freshness / Metric Definition Validation
  -> Canonical VerifiedFact Snapshot
  -> Agents: Market, Fundamentals, News, Sentiment
  -> Claim Extraction + Cross-Agent Consistency (deterministico sui fatti strutturati)
  -> Research / Trader / Portfolio Manager (interpretazioni libere, fatti vincolati)
  -> Quantitative Risk Validation + Conditional Plan Validation
  -> Execution Gate (fail-closed, solo paper)
  -> Report + Machine-readable Audit Trail
```

Preferisci **estendere i componenti già esistenti**. Se non esistono, crea il minimo necessario e collegalo ai reali entrypoint prima di dichiarare il task completato. Vietato un validatore dimostrativo non chiamato in produzione.

### 2.1 As-of, SEC, fact registry

- Snapshot per report: `analysis_as_of` univoco, timezone, `last_market_session` per exchange, date release note per evento, periodi fiscali, timestamp di pubblicazione/retrieval e identificativo fonte.
- Ogni `VerifiedFact` materiale contiene almeno: `symbol`, `metric_id`, `value`, `unit`, `currency`, `period_start/end`, `fiscal_period`, `source_id`, `source_url`/`filing_accession`, `published_at`, `observed_at`, `as_of`, `scope`/`segment`, `metric_definition`, `status` e `errors`.
- `missing`, `zero`, `not_applicable`, `provisional`, `stale` sono stati distinti. Non convertire `null` in `0`.
- SEC e IR societario precedono i dati derivati dei vendor per contabilità; mai mescolare periodi o definizioni senza riconciliazione. Un dato Q2 **resta utilizzabile come storico**, non come l'ultimo disponibile se Q3 era già pubblicato.
- EPS operativo, GAAP, consensus non sono intercambiabili. Anche trimestrale singolo, YTD, FY e TTM non sono confrontabili per trend senza trasformazione esplicita.
- Quando una fonte primaria non risponde, non fabbricare dati: stato `UNVERIFIED`/`UNAVAILABLE` e blocco delle azioni dipendenti.

### 2.2 Event causality e cross-agent claims

- `check_event_pricing(event_published_at, market_calendar, session_open/close, observed_price_timestamp)` deve usare calendari reali (weekend, festività, after-hours, DST), non regole testuali.
- Con report Q3 pubblicato **sabato 7/11** e ultimo prezzo **venerdì 6/11**, `NOT_YET_OBSERVABLE`. **Qualsiasi** affermazione di reazione, assorbimento, price-in o neutralizzazione attribuita al Q3, anche implicita nel sentiment, deve fallire.
- Gli agenti possono dissentire su BUY/HOLD/SELL e sui rischi **interpretativi**, ma non attribuire fatti, date, aziende o metriche incompatibili.
- Se l'affermazione conflittuale non può essere corretta con fonte autorevole, **escludila dal ragionamento finale** e riporta il conflitto. Il report può essere prodotto a fini diagnostici, ma nessuna esecuzione paper può dipendere da quel fatto.
- Non scambiare una notizia sul **gruppo Berkshire** per un dato del segmento **BNSF/GEICO**. Non interpretare l'assenza di post Reddit come segnale empirico di acquisti/vendite.

### 2.3 Calcoli e piano operativo deterministici

- La matematica non deve essere generata/validata soltanto dall'LLM: prezzi OHLC, variazioni open-to-close/close-to-close, ATR, P/B, rischio nozionale, spread, esposizione post-trade e rischio aggregato vanno calcolati dal software.
- Separare `strategic_rating` e `conditional_trade_plan` da `orders`. Il piano può contenere `signal_at`, `eligible_from`, `valid_until`, `side`, `order_type`, `condition`, `entry_reference`, `stop_type`, `stop_reference`, `stop_trigger_basis`; una condizione su **chiusura** può essere valutata soltanto dopo la chiusura e non autorizza retroattivamente un ordine a quel prezzo.
- Non trattare uno **stop su chiusura** come protezione massima garantita: modellare slippage e gap risk, dove possibile, e usare limiti prudenziali di esposizione.
- **Rischio per singola tranche** e **rischio totale delle tranche + posizione esistente** entrambi entro policy. Un trasferimento dello stop a breakeven *proposto* non riduce il rischio finché non è effettivamente registrato nel paper broker.
- Vietato short/opzioni per default salvo supporto e policy espliciti. Solo paper, sempre.

**Esempio numerico da usare nei test, non come policy di produzione:** con equity di **100.000 USD**, budget di rischio **1.000 USD**, entrata **560**, stop **552**, il limite basato solo sulla distanza darebbe `floor(1000/8)=125` azioni, ovvero **70.000 USD di esposizione (70%)**. Se la policy della fixture limita l'esposizione per titolo al **5%**, il sistema deve applicare il vincolo più stringente e restituire **al massimo 8 azioni** (4.480 USD), a parità di cash/disponibilità e prima degli eventuali costi. Non deve produrre 125 azioni. Con altre tranche prezzi e stop differenti, ricalcolare il totale.

### 2.4 Contratto output e fail-closed

Schema minimo (adattare alle convenzioni già presenti):

```json
{
  "symbol": "BRK-B",
  "strategic_rating": "HOLD",
  "execution_action": "BLOCK",
  "execution_approved": false,
  "authorized_orders": [],
  "blocking_reasons": [
    "INVALID_EVENT_CAUSALITY",
    "STALE_FUNDAMENTALS",
    "PORTFOLIO_CONTEXT_MISSING"
  ],
  "validation_audit_id": "fixture-1853"
}
```

Questo JSON è **un esempio per la fixture non riconciliata**, non un esito già presente nel report. `WAIT` può descrivere un segnale non ancora maturato in assenza di errori; `BLOCK` indica un dato critico non validato. Nessuna chiamata di invio ordine quando `execution_approved=false`. Il gate deve essere impossibile da bypassare tramite output testuale degli LLM.

## 3. Test obbligatori (riproducibili e offline)

Implementa test **prima delle correzioni**, conserva il test baseline inizialmente fallente, poi rendilo verde. Usa `pytest`, `bun test`, o lo strumento **realmente presente nel repository**. I test devono funzionare senza Ollama Cloud, SEC live, yfinance live, token, mercati aperti o accesso a broker.

### Test unitari

- [ ] `EVENT-REG-001`: sabato release, venerdì close => `NOT_YET_OBSERVABLE`; claim «−0,01% reazione Q3» => `INVALID_EVENT_CAUSALITY`. Test anche festività e pubblicazione after-hours.
- [ ] `FILING-REG-002`: filing Q3 pubblicato e disponibile all'`as_of`, vendor solo Q2 => aggiornamento Q3 verificabile o `STALE_FUNDAMENTALS`, mai stato `VERIFIED` su Q2 come latest.
- [ ] `FACT-REG-003`: buyback H1 e buyback Q3 non sono direttamente in conflitto; «nessun buyback Q3» confligge con buyback Q3 confermato. Stesso scope/periodo/definizione obbligatori.
- [ ] `VAL-REG-004`: calcolo P/B A/B corretto; equity Q2 vs azioni Q3 => rifiuto o warning materializzato, mai P/B validato senza allineamento.
- [ ] `MATH-REG-005`: confronto 85,77 TTM con 88,99 FY non classificato come «accelerazione»; `INVALID_TREND_COMPARISON`.
- [ ] `METRIC-REG-006`: GAAP EPS vs operating EPS e consensus adjusted => mismatch esplicito. No `beat` certificato senza metrica compatibile.
- [ ] `RISK-REG-007`: scenario 100k, 560/552, cap per simbolo 5% => max 8 azioni, non 125; casi aggiuntivi cash insufficiente, prezzo invalido e stop LONG/SHORT errato.
- [ ] `RISK-REG-008`: tre tranche con entry/stop diversi, cap di rischio aggregato, ordini aperti e posizioni esistenti; nessun calcolo erroneo di breakeven prima che lo stop sia registrato.
- [ ] `ENTRY-REG-009`: gap open 558 con regola `open < 559.61` => piano condizionale non deve autorizzare acquisto 558 senza un'esplicita revisione del setup; scenari 558, 560, 580, 586; trigger su chiusura distinto dall'esecuzione seduta successiva.
- [ ] `EXEC-REG-010`: `HOLD` + portafoglio mancante o fatti critici invalidi => `approved=false`, `orders=[]` e nessuna chiamata al paper broker.

### Test d'integrazione con mock

- [ ] Snapshot BRK-B delle 18:53 congelato, News e Sentiment contraddittori; il Fact Checker intercetta l'errore e il Portfolio Manager riceve solo fatti verificati + avvisi di conflitto.
- [ ] SEC mock Q3 vs yfinance mock Q2: il risultato cambia e il log riporta quali campi aggiornati/non disponibili.
- [ ] Un provider in timeout / risposta malformata / timezone non risolto => fail closed; nessuna decisione operativa silenziosamente validata.
- [ ] Modello quick/deep: output non conforme allo schema rifiutato senza generare ordini; test con fake LLM, niente chiamate a provider cloud.
- [ ] Un trade valido (solo se l'app implementa già paper execution) passa i controlli e raggiunge un **mock** broker paper; retry idempotente non duplica ordini.

### Generalizzazione e non-regressione

- [ ] Fixture sintetiche parametrizzate almeno su `AAPL`, `NVDA`, `ENI.MI` oltre BRK-B: stesse regole su timezone, valuta, calendario, metriche settoriali e corporate actions.
- [ ] Preserva i test di snapshot tecnico, date earnings, assenza dividendi, conversione Class A/B e funzioni esistenti.
- [ ] Nessun test dipendente dalla prosa identica degli agenti; controlli su fatti, stati e invarianti.
- [ ] Registra versione fixture, fonte, timestamp e hashing (se infrastruttura disponibile) per replay; i timestamp devono essere parte dello snapshot, non dell'orologio di sistema.

## 4. CI GitHub: condizione di merge

1. Ispeziona `.github/workflows/`. Estendi workflow esistente o creane uno minimo con trigger `pull_request` target `custom`, opzionalmente `push` su `custom` e `workflow_dispatch`, permissions **`contents: read`**.
2. Jobs stabili, se compatibili col progetto: `lint`, `unit-tests`, `integration-tests`, `reliability-regression`. Non inventare job funzionanti se mancano dipendenze; implementa comandi reali.
3. I test P0 **devono fallire la CI** quando reintroduci volontariamente (in test isolati) un claim pre-evento, un Q2 spacciato per Q3 o un sizing oltre exposure cap.
4. Evita network live e segreti nei test CI. Nessuna dipendenza da modelli cloud durante i check obbligatori.
5. Documenta nomi precisi dei check da selezionare come **required status checks** nel ruleset GitHub del branch `custom`. Se non hai permessi amministrativi, **non simulare** che i ruleset siano configurati.

## 5. Git, commit, push, Pull Request

1. `git status --short --branch`, `git remote -v`; preserva lo stato locale. `git fetch origin` e verifica `origin/custom`.
2. Crea il feature branch da **`origin/custom`**, non da `main`. Non modificare il branch base direttamente.
3. Registra baseline: comando test, commit iniziale, problemi preesistenti. Aggiungi test regressivi fallenti; poi implementa il fix con modifiche minime e revisionabili.
4. Esegui i test unitari/integration/end-to-end mocked, lint, formattazione e type-check disponibili. Se qualcosa non è eseguibile, specifica **perché**, senza dichiararlo verde.
5. Commit progressivi suggeriti (adatta ai file reali):
   - `test(reliability): reproduce 18:53 report regressions`
   - `fix(data): enforce event causality and filing freshness`
   - `fix(facts): reconcile cross-agent claims and metric definitions`
   - `fix(risk): enforce exposure and aggregate tranche budgets`
   - `fix(execution): fail closed on unverified plans`
   - `docs: document validations and update changelog`
6. Aggiorna `CHANGELOG.md` sotto `[Unreleased]` con **solo** correzioni realmente implementate; aggiorna `AGENTS.md`/contratti se necessario, senza riscriverli inutilmente.
7. `git diff --check`, review dei file staged, assenza di credenziali/.env/log sensibili; commit e push **solo** del feature branch.
8. Apri PR verso **`custom`**; nella descrizione collega ogni `ID` della tabella a test e funzioni concrete, riporta comandi e risultati e segnala limiti residuali. **Mai merge automatico.** Non effettuare bypass dei ruleset.
9. Se accesso, push o PR sono impossibili, completa il lavoro sicuro locale e fornisci il blocco esatto con i comandi necessari. **Non inventare URL di PR, commit SHA o test verdi.**

## 6. Definition of Done (vincolante)

La PR può essere considerata completata **solo se**:

- [ ] `EVENT-REG-001` intercetta ogni attribuzione post-evento impossibile anche dentro Sentiment; niente falso «equilibrio del mercato dopo gli earnings».
- [ ] `FILING-REG-002` riconosce il Q3 quando già pubblicato; se non recuperabile lo segnala e impedisce l'uso del Q2 come dato aggiornato.
- [ ] `FACT-REG-003` e `VAL-REG-004` riconciliano periodo, classe azionaria, prezzo, share count e fonti; `UNRECONCILED` quando necessario.
- [ ] `MATH-REG-005` e `METRIC-REG-006` impediscono confronti TTM/FY invalidi e confusione GAAP/operating/adjusted EPS.
- [ ] `RISK-REG-007/008` applicano contemporaneamente sizing allo stop, esposizione nozionale e budget aggregato, incluse le tranche future.
- [ ] `ENTRY-REG-009` separa signal, eligibility ed execution ed evita condizioni conflittuali.
- [ ] `EXEC-REG-010` dimostra fail-closed sul percorso **reale** degli ordini paper, con audit JSON e `authorized_orders=[]` quando non autorizzati.
- [ ] Tests offline e CI passano davvero (o viene documentato esattamente il blocco, senza dichiarare done).
- [ ] Codice generalizzabile ad altri ticker, senza hardcode BRK-B.
- [ ] Changelog, commit, push e PR verso `custom` completati quando tecnicamente consentito; **nessun merge**.

## 7. Report finale obbligatorio dell'agente

```text
BASE BRANCH: origin/custom
FEATURE BRANCH: <nome reale>
BASELINE COMMIT: <SHA>
COMMITS CREATED: <SHA reali o motivo del blocco>
PULL REQUEST: <URL reale; base custom; oppure motivo della mancata apertura>
TEST BASELINE: <comando, passed/failed/skipped>
TEST FINALI: <comando, passed/failed/skipped>
CI CHECKS: <job e stato verificato/non verificato>

REGRESSION MATRIX:
EVENT-REG-001   <test + file/funzione + PASS/FAIL>
FILING-REG-002  <...>
FACT-REG-003    <...>
VAL-REG-004     <...>
MATH-REG-005    <...>
METRIC-REG-006  <...>
RISK-REG-007    <...>
RISK-REG-008    <...>
ENTRY-REG-009   <...>
EXEC-REG-010    <...>

PAPER SAFETY: <prova che gli ordini non validati sono bloccati>
CHANGED FILES: <file effettivi>
REMAINING RISKS: <priorità e limiti>
```

---

**Istruzione operativa finale per Copilot:** inizia subito dall'ispezione del repository e dai test regressivi che riproducono gli errori delle 18:53. Implementa il fix nel codice e nei punti effettivi di orchestrazione/esecuzione; non fermarti alla sola modifica dei prompt o alla redazione di un piano. Completa test, CI, `CHANGELOG.md`, commit, push e Pull Request su `custom` senza merge automatico.

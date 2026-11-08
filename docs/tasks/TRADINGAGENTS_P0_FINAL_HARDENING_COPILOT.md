# TradingAgents — P0 Final Hardening: dati, calcoli, rischio, ordini

**Destinatario:** GitHub Copilot Agent / Codex Agent nel repository TradingAgents  
**Base obbligatoria:** `origin/custom`  
**Ambito:** correzioni software P0, test automatici, audit, CI, commit e PR  
**Vincolo assoluto:** **paper trading soltanto**; nessuna operazione reale; nessun merge automatico.

## 0. Obiettivo e mandato esecutivo

Implementa le correzioni **nel codice** per impedire che un report TradingAgents possa originare ordini simulati quando i dati sono obsoleti, i calcoli non tornano, le tranche superano il budget di rischio o le proposte degli agenti non sono riconciliate.

Non limitarti a riscrivere prompt, AGENTS.md o report. Ispeziona l'architettura esistente, riproduci i difetti tramite test **prima** di correggerli, implementa il minimo design riutilizzabile, verifica tutti i percorsi d'esecuzione, aggiorna documentazione e `CHANGELOG.md`, crea commit logici e apri una PR verso `custom` dopo test riusciti.

Prosegui autonomamente nelle operazioni ordinarie consentite. Se un tool esterno/permesso è assente, non simulare il successo: completa le attività locali fattibili e documenta il blocco.

### Dati di regressione: ultimo report BRK-B del 2026-11-08

Il report include questi difetti riproducibili:

1. **Fundamentals Analyst usa Q2:** equity attribuibile al 30/06/2026 pari a circa **747,910 mld USD**, benché il filing Q3 2026 sia disponibile al tempo dell'analisi (pubblicazione il **7/11/2026**). I riferimenti Q3 utilizzati per la fixture ufficiale, previa verifica contro il filing sorgente, sono equity **761,368 mld USD** e total assets **1.303,907 mld USD** al 30/09/2026. Non codificare queste cifre nella logica applicativa.
2. **Aritmetica errata:** a prezzo di riferimento **570,89 USD**, il Fundamentals Analyst dichiara una distanza di **-11,3%** dal massimo **586,30**, **+4,5%** sulla SMA200 **497,723**, «in linea» con SMA50 **521,560**, e **+12,1%** dal minimo **464,01**. Le grandezze corrette, usando quei dati, sono rispettivamente **-2,63%**, **+14,70%**, **+9,46%**, **+23,03%**.
3. **Tranche con stop incoerenti:** gamba A a **556,00**, stop **542,50**: perdita nominale **13,50 USD/azione**, ossia **1,435 ATR** con ATR **9,408**. Gamba B nell'area **543–544** e stop **542,50**: con ingresso di esempio **543,50**, distanza di **1,00 USD**, **0,106 ATR**; non riutilizzare il rischio della A per la B.
4. **Decisioni non riconciliate:** Research Manager `OVERWEIGHT`, Trader `BUY` con target **115–125%** dell'allocazione standard; Portfolio Manager `OVERWEIGHT` con target **105–115%**, diverse percentuali/tranche e condizioni. Manca nel report una decisione d'ordine finale normalizzata, calcolata sul portafoglio effettivo e validata dall'Execution Gate.
5. **Evento non ancora prezzato:** Q3 pubblicato sabato **7/11/2026**, ultimo OHLCV venerdì **6/11**, prima seduta utile lunedì **9/11**. Il modulo temporale deve impedire di considerare verificata una reazione di prezzo alla trimestrale alla data dell'8/11.

Tratta questi valori come **fixture di regressione**, non come eccezioni per un ticker. Il sistema deve funzionare per società, mercati, valute, classi azionarie e calendari fiscali diversi.

## 1. Procedura Git e precauzioni

1. Leggi `AGENTS.md`, eventuali `SPEC.md`/`CONTRATTO.md`, configurazioni, struttura delle app, test, strumenti di esecuzione, branch protection e `CHANGELOG.md`.
2. Verifica lo stato di Git e **non sovrascrivere modifiche utente non committate**. Non usare comandi distruttivi (`reset --hard`, clean indiscriminato, force-push) per ottenere uno stato pulito.
3. Effettua fetch di `origin/custom` e crea il branch `fix/p0-data-risk-execution-gate` **a partire da `origin/custom`**. Se il nome è occupato, usa un suffisso descrittivo. **Mai partire da `main`.**
4. Non fare commit direttamente su `custom`. Non modificare componenti non correlati senza ragione verificabile.
5. Non abilitare ordini live, chiavi broker reali, fallback bypass o modalità di approvazione automatica.
6. Ogni sottosezione deve avere test e commit; non eseguire merge automatici. La PR deve avere base **`custom`**.

## 2. P0-A — Filing Freshness + canonical facts

### Implementazione

- Individua l'ultimo **filing rilevante noto all'istante `analysis_as_of`** (non il più recente conosciuto oggi), usando SEC EDGAR/Company Facts e gli eventuali servizi ufficiali già presenti. Identifica i documenti con CIK, form type, accession number, `filed_at` / `accepted_at`, `fiscal_period_end`, `observed_at`, fonte e URL/ID.
- Distingui **data del filing**, **periodo del dato**, **istante di disponibilità**, **periodo fiscale**, **unità** e **definizione della metrica**. Supporta annuale vs trimestrale, Q4 derivato quando lecito, amendment, ritardi di pubblicazione, timezone e backtest point-in-time.
- Riconcilia i dati dei vendor con i documenti ufficiali quando coprono la **stessa metrica/definizione e periodo**. Usa lo schema canonico `Fact` (o estendi l'equivalente già esistente): `instrument_id`, `metric_id`, `metric_definition`, `value`, `unit`, `currency`, `period_start`, `period_end`, `form_type`, `accession`, `published_at`, `retrieved_at`, `source_ref`, `validation_status`, `confidence`.
- Per BRK-B risolvi correttamente le classi A/B e le **azioni equivalenti**: book value per azione B ≠ book value per azione A; non confondere equity totale e book value per share. Segui le definizioni e il periodo del filing.
- Il fact registry dev'essere condiviso dai passaggi fondamentali e dai decisori: una cifra Q2 non deve arrivare come «dato Q3» a Manager/Trader. Mantieni i dati originali, la riconciliazione e la decisione sulla fonte in un audit trail.
- Confronta il periodo disponibile e quello usato. Se il filing richiesto è disponibile ma il dataset restituisce un precedente trimestre, tenta il **refresh**; se non riesce, marca `STALE_FUNDAMENTALS` e **blocca gli ordini dipendenti**. Non bloccare inutilmente la generazione di un report informativo, purché segnali chiaramente i dati stale.
- Supporta errori di rete, rate-limit, filing assente, dati XBRL con definizioni non omogenee e cache scaduta. Non fare fallback silenzioso a dati vecchi.
- Non assumere che ogni periodo del Q3 sia disponibile il giorno seguente alla chiusura del trimestre; usa solo le informazioni effettivamente pubblicate fino a `analysis_as_of`.

### Test P0-A

- `FRESH_01`: all'8/11/2026, fixture con filing Q3 già pubblicato e vendor fermo al Q2 ⇒ refresh valido oppure `STALE_FUNDAMENTALS`; **mai** stato fresh con equity Q2.
- `FRESH_02`: giorno precedente alla pubblicazione Q3 ⇒ non usare il filing futuro (**anti look-ahead**).
- `FRESH_03`: SEC momentaneamente non raggiungibile, cache stale ⇒ nessuna autorizzazione d'ordine che richieda dati freschi.
- `FRESH_04`: classi A/B e medesimo `period_end` ⇒ P/B calcolato coerentemente; casi con ticker singola classe.
- `FRESH_05`: vendor con EPS adjusted e filing con EPS GAAP ⇒ `METRIC_DEFINITION_MISMATCH` se non esplicitamente riconciliabili.
- `FRESH_06`: società con fine esercizio diversa dal 31 dicembre ed emittente non USA ⇒ identificazione periodo/autorità adatta o stato non verificabile esplicito.

## 3. P0-B — Arithmetic & Market Data Validator

### Implementazione

- Centralizza in funzioni pure i calcoli verificabili: variazione `(current/reference - 1) * 100`, distanza da high/low e medie, P/B, EPS, market cap, rapporti d'unità, rendimento/rischio, ATR ratio, OHLCV e coerenza MACD/istogramma.
- Usa lo **stesso snapshot datato** per prezzo e indicatori; registra `as_of`, vendor, precisione e rettifiche corporate action. Gestisci valori nulli, zero, negativi, unità incompatibili e divisioni per zero. Imposta tolleranze numeriche per arrotondamenti.
- Non permettere che percentuali calcolate nel testo LLM diventino fatti certificati se contraddicono i calcoli deterministici. Aggiungi `ARITHMETIC_MISMATCH`, `UNIT_MISMATCH`, `PRICE_TIMESTAMP_MISMATCH`.
- Non interpretare come divergenza tecnica confermata una descrizione in prosa senza criteri quantitativi. Un'indicazione qualitativa può essere conservata, ma non marcata come pattern verificato.
- Correggi la confusione tra **variazione intraday open→close** e **variazione close→close**: sono metriche distinte.

### Test P0-B

- `ARITH_01`: close 570.89, 52w_high 586.30 ⇒ **-2.63%** circa; il -11.3% deve essere respinto.
- `ARITH_02`: close 570.89, SMA200 497.723 ⇒ **+14.70%**, non +4.5%.
- `ARITH_03`: close 570.89, SMA50 521.560 ⇒ **+9.46%**.
- `ARITH_04`: close 570.89, 52w_low 464.01 ⇒ **+23.03%**, non +12.1%.
- `ARITH_05`: mismatch data di prezzo/indicatore ⇒ warning o blocco secondo criticità e uso; niente riconciliazione fittizia.
- `ARITH_06`: dataset multi-ticker con valute/unità/classi differenti.

## 4. P0-C — Multi-Tranche Risk Engine deterministico

### Implementazione

- Identifica tutte le traiettorie d'ordine disponibili. Il **Risk Engine** deve dimensionare ogni gamba separatamente e anche il totale dopo esecuzione, usando dati realmente disponibili: equity account, buying power, liquidità, posizioni e ordini aperti, cash/FX, side, entry, stop, quantità proposta, price freshness, tick-size, commissioni/slippage, limitazioni portafoglio e budget monetario.
- Per un LONG con stop protettivo ordinario: `risk_per_share = entry - stop` se `entry > stop`; SHORT speculare. `position_risk = quantity * risk_per_share + expected_costs`. Il calcolo allo stop è **perdita teorica**, non massima garantita: aggiungi gap/slippage stress separato.
- Calcola `notional`, `incremental_exposure`, `total_ticker_exposure`, `portfolio_gross/net_exposure`, concentrazione, rischio delle tranche attive e candidate **considerando posizioni/ordini esistenti**; valuta anche il worst-case di più ordini condizionali simultaneamente attivi, se consentito.
- Per ordini multi-tranche, tratta `entry`, `stop`, `strategy_id`, `trigger`, `valid_after`, `expires_at`, `quantity`, `risk_budget` come attributi di ogni singola gamba. Scenario di pullback e scenario breakout non devono essere fusi in un solo ordine con lo stesso stop arbitrario.
- I limiti di esposizione, rischio %, massimo nozionale, ATR min/max per strategia e numero di gambe devono provenire da **configurazione di policy**, non da numeri hardcoded. Un limite ATR può generare `STOP_TOO_TIGHT_FOR_POLICY` quando previsto dalla strategia; non trasformare genericamente 2×ATR in una regola universale per tutti i trade.
- Usa formule deterministiche per il `quantity` e limita ulteriormente per buying power, esposizione, liquidità e rischio. Ridimensiona o respingi secondo policy documentata. Senza account/portafoglio non inventare size ⇒ `PORTFOLIO_CONTEXT_MISSING`.
- Ogni cambiamento di entry/stop/size annulla l'approvazione precedente e forza una rivalidazione. Tra segnale e invio ordine, aggiorna prezzi, disponibilità, ordini aperti e stato del gate.

### Test P0-C

- `RISK_01`: gamba A entry 556, stop 542.5, ATR 9.408 ⇒ **13.5 USD** di rischio/azione e **1.435 ATR**.
- `RISK_02`: gamba B entry 543.5, stop 542.5, ATR 9.408 ⇒ **1 USD** e **0.106 ATR**. Non riutilizzare rischio della A.
- `RISK_03`: portafoglio teorico 100k USD, entry 560, stop 552.07 e budget 1% ⇒ verifica che il puro sizing allo stop (circa 126 azioni, ≈70.6k USD nozionali) sia **ridotto/bloccato** da un limite di esposizione configurato inferiore al 70%, non trattato come sicuro automaticamente.
- `RISK_04`: seconda/terza tranche con stop differenziati ⇒ rischio aggregato corretto, inclusi gli ordini aperti.
- `RISK_05`: SHORT con stop sotto entry, LONG con stop sopra entry ⇒ `INVALID_STOP_SIDE`.
- `RISK_06`: gap che oltrepassa lo stop ⇒ perdita stress maggiore della nominale, evidenziata senza promettere esecuzione al prezzo dello stop.
- `RISK_07`: assenza portfolio, cambio quantità dopo approvazione, ordini duplicati e notional insufficiente ⇒ fail-closed.
- `RISK_08`: scenari alternativi (pullback/breakout) ⇒ ordini indipendenti o mutuamente esclusivi, policy verificata.

## 5. P0-D — Decision Reconciliation + Execution Gate

### Implementazione

- Distingui **rating** (`HOLD`, `BUY`, `SELL`, `OVERWEIGHT`, ecc.), **decisione strategica**, **piano condizionale**, **candidati d'ordine** e **autorizzazione dell'esecuzione**. `OVERWEIGHT` non è un ordine: ha senso soltanto con benchmark/peso obiettivo e portafoglio esistente.
- Il **Portfolio Manager** stabilisce la proposta finale: l'esecutore non può prendere direttamente la proposta del Trader/Researcher ignorando variazioni successive. Normalizza il piano finale in uno schema tipizzato (non parsare testo libero quando è possibile imporre structured output).
- Ogni gamba deve avere quantità concreta e calcolata, ticker, side, tipo ordine, trigger, stop, validità, fonti/periodi, policy e reason codes. Se la quantità non è determinabile, il candidato resta una proposta non eseguibile.
- Implementa o verifica un **Execution Gate in codice**, invocato da TUTTI i percorsi di paper order (manuale, scheduler, agente, API, retry, webhook). Nessuna chiamata diretta al broker/simulatore deve bypassarlo.
- Alla data 8/11/2026 l'evento Q3 del 7/11 non è ancora prezzato: il calendario NYSE e il publication timestamp devono restituire `NOT_YET_OBSERVABLE`; un piano subordinato alla reazione post-evento resta WAIT fino alla **prima seduta utile con dati realmente disponibili**, non semplicemente allo scoccare del 9/11.
- Fail closed per `STALE_FUNDAMENTALS` quando rilevante, `ARITHMETIC_MISMATCH` critico, `FACT_CONFLICT`, `PORTFOLIO_CONTEXT_MISSING`, `EXPOSURE_LIMIT_EXCEEDED`, `RISK_LIMIT_EXCEEDED`, `INVALID_STOP_SIDE`, `ORDER_RECONCILIATION_FAILED`, `MARKET_DATA_STALE`, `EVENT_PRICE_NOT_OBSERVABLE`, `CONDITIONAL_TRIGGER_UNMET` e simili.
- Dati sentiment mancanti non devono bloccare da soli un'operazione se non sono necessari alla policy; possono abbassare confidence.
- Esito **strutturato, persistito e auditabile**, con `execution_approved: false` e `authorized_orders: []` per i blocchi. Nessun LLM può modificare `execution_approved` o azzerare motivi di blocco.
- Garantire idempotenza e revalidation al momento dell'invio (TOCTOU); monitorare eventuali retry e ordini aperti.

### Schema indicativo (NON hardcodare l'esito BRK-B)

```json
{
  "run_id": "example-run",
  "instrument": "BRK-B",
  "analysis_as_of": "2026-11-08T18:00:00Z",
  "strategic_rating": "OVERWEIGHT",
  "execution_action": "WAIT",
  "execution_approved": false,
  "authorized_orders": [],
  "blocking_reasons": [
    "STALE_FUNDAMENTALS",
    "PORTFOLIO_CONTEXT_MISSING",
    "EVENT_PRICE_NOT_OBSERVABLE"
  ],
  "checked_at": "<timestamp>",
  "policy_version": "<version>"
}
```

### Test P0-D

- `EXEC_01`: Trader BUY 115–125%, PM OVERWEIGHT 105–115% ⇒ proposta precedente invalidata, nessun ordine prima del ricalcolo della decisione PM.
- `EXEC_02`: domenica 8/11 e reazione Q3 richiesta ⇒ `EVENT_PRICE_NOT_OBSERVABLE`, `authorized_orders=[]`.
- `EXEC_03`: ingresso condizionato non attivato ⇒ WAIT, nessun ordine.
- `EXEC_04`: portfolio mancante ⇒ nessun BUY o SELL inventato, anche con rating OVERWEIGHT.
- `EXEC_05`: ordini da scheduler/API/retry bypass tentato ⇒ gate invocato comunque, impossibile raggiungere l'adapter paper senza approvazione.
- `EXEC_06`: modifica a stop, entry o quantità dopo autorizzazione ⇒ approvazione invalida e rivalidazione.
- `EXEC_07`: HOLD senza ordine concreto ⇒ WAIT/no order; rating Buy o Overweight non implica `execution_approved=true`.
- `EXEC_08`: mock broker registra **zero ordini** quando un qualunque controllo critico fallisce.
- `EXEC_09`: caso valido con dati freschi e portafoglio configurato ⇒ transizione all'ordine paper ammessa soltanto quando tutti i gate passano (evita un gate che blocchi sempre).

## 6. Regressioni cross-agent e qualità dei fatti

- Aggiungi fixture di test per **BRK-B**, più almeno **AAPL**, **NVDA**, **ENI.MI** o equivalenti realmente supportati. BRK-B è un esempio di classi azionarie diverse; ENI.MI verifica fuso orario, valuta e mercato non USA.
- Controlla che News, Fundamentals e Portfolio Manager condividano fonti e periodi per i fatti contabili, e che un'affermazione temporalmente falsa (reazione prezzo prima della pubblicazione) non superi l'event checker.
- Verifica anche **casi positivi**: un feed sentiment incompleto può produrre un report con confidence ridotta; l'assenza del Q3 se **non ancora pubblicato** non è `STALE_FUNDAMENTALS`.
- I test devono usare fixture/versioned cassettes offline e non chiedere token a modelli cloud, dati live o credenziali SEC durante CI.
- I risultati di validazione devono essere mostrati nella diagnostica del report (tabella `Validation Summary`) con stati **PASS / WARN / BLOCK**, senza confondere la narrazione degli agenti con il verdetto del software.

## 7. Osservabilità e audit trail

Per ogni run conserva almeno: `run_id`, commit/versione, provider/modelli, `as_of`, ticker normalizzato, filing (periodo + accession), source refs e timestamps, fact conflicts, risultati dei validatori, snapshot prezzo, decisioni agenti, versione proposta finale, portfolio snapshot redatto, calcoli/tranche, motivazioni del gate, eventuali ordini paper e risultati.

Nessun token API, password o segreto nei log. Usa log strutturati correlati da `run_id` e reason codes stabili.

## 8. CI, test e criteri di completamento

- Esegui lint, typecheck e test esistenti prima e dopo la modifica. Identifica i comandi dal repository; non inventare script non presenti.
- Aggiungi unit, integration, regression e test dell'adapter paper con broker mock. Preferisci funzioni pure e dipendenze iniettabili (clock, data provider, broker) per test deterministici.
- Esegui un test di sicurezza che **inietta un ordine non valido** direttamente dall'API di esecuzione: deve essere bloccato anche bypassando i componenti LLM.
- Integra la suite P0 in GitHub Actions per le PR con base `custom`. La CI deve fallire se un test P0 fallisce. Se non puoi impostare la branch protection, documenta il check name richiesto e i passaggi manuali.
- Non asserire «PASS» in mancanza dell'output del comando. Riporta i test non eseguiti come `NOT_RUN` con motivazione.

### Definition of Done (tutti obbligatori)

- [ ] Filing freshness point-in-time implementato e testato, con fact registry/periodi condivisi.
- [ ] Errori percentuali dell'ultimo report catturati dal validatore deterministico.
- [ ] Sizing completo per gamba + esposizione/rischio aggregati e limiti account verificati.
- [ ] Reconciliation dal Portfolio Manager all'ordine candidato implementata.
- [ ] Execution Gate obbligatorio su ogni percorso paper; fail-closed dimostrato.
- [ ] Nessun ordine quando mancano dati critici, portafoglio o trigger; caso valido autorizzabile.
- [ ] Test offline multi-ticker e assenza look-ahead superati.
- [ ] Audit, diagnostica e `CHANGELOG.md` aggiornati.
- [ ] GitHub Actions P0 aggiunta/aggiornata e test eseguiti localmente.
- [ ] Commit logici, push del feature branch e PR **verso `custom`**, senza merge automatico.

## 9. Commit e PR

Usa commit atomici, per esempio:

1. `test(p0): reproduce stale filings and arithmetic regressions`
2. `fix(data): reconcile point-in-time SEC facts across agents`
3. `fix(validation): enforce deterministic arithmetic checks`
4. `fix(risk): validate tranche sizing and aggregate exposure`
5. `fix(execution): fail-closed reconciled paper orders`
6. `test(ci): enforce offline P0 regression gates`
7. `docs: update validation architecture and changelog`

Prima del push controlla diff, file non tracciati, secret scanning disponibile e regressioni; non committare `.env` o credenziali.

Apri la PR come **draft solo se il lavoro è incompleto** e segnala esplicitamente i blocker. Per il completamento, la PR deve includere: riepilogo architetturale, matrice test con comando/risultato, evidenza dei casi fail-closed, eventuali migration, limitazioni e rischio residuo, screenshot/estratti della nuova Validation Summary se presente. **Non fare merge.**

## 10. Output richiesto all'agente

Concludi con una tabella:

| Controllo | File/Simbolo implementato | Test | Risultato | Residuo |
|---|---|---|---|---|
| Freshness | ... | FRESH_* | PASS/FAIL/NOT_RUN | ... |
| Arithmetic | ... | ARITH_* | PASS/FAIL/NOT_RUN | ... |
| Risk | ... | RISK_* | PASS/FAIL/NOT_RUN | ... |
| Execution | ... | EXEC_* | PASS/FAIL/NOT_RUN | ... |

Inoltre indica: branch di origine, nome branch feature, SHA commit, URL PR (se creata), nome check GitHub Actions, percorso audit/validation summary e una prova con broker mock che **nessun ordine** sia stato trasmesso con controlli P0 falliti.

**Regola conclusiva:** il task non è completato da un report finanziario più convincente; è completato solo quando il codice impedisce che quei difetti producano ordini paper non autorizzati.

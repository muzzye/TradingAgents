# TradingAgents — Task P0: Data Quality, Cross-Agent Consistency & Regression Gates

**Versione documento:** 2026-11-08  
**Destinatario:** GitHub Copilot Agent in Visual Studio Code (compatibile anche con altri coding agent)  
**Repository:** progetto TradingAgents esistente, non riscrivere da zero  
**Branch Git di integrazione:** `custom` (**NON** `main`)  
**Output richiesto:** codice implementato e testato, commit, push del branch di lavoro e Pull Request verso `custom` **senza merge automatico**  
**Trading:** esclusivamente paper/simulation; mai inviare ordini reali

## 0. Contratto operativo dell'agente — LEGGERE PRIMA

1. Leggi **prima** il file `AGENTS.md` **del repository**, `docs/specs/TRADINGAGENTS_RELIABILITY_CONTRACT.md` (se esiste), `README.md`, `CHANGELOG.md`, la configurazione CI e i test presenti. Questo task è un **incremento mirato** del contratto di affidabilità già definito: non cancellarlo e non riscrivere l'applicazione.
2. **Vincolo Git vincolante:** la base è `origin/custom`. Se un file storico parla di `main`, aggiorna le istruzioni interessate e segui **`custom`**. Non assumere che il default branch di GitHub sia quello corretto. Non cambiare il default branch su GitHub.
3. Analizza prima il repository vero: linguaggio, entrypoint, orchestratore degli agenti, adapter SEC EDGAR/yfinance/FRED/Polymarket, LLM provider, persistenza report, workflow degli ordini, test e interfaccia web. Non inventare file, commandi o interfacce.
4. Agisci autonomamente su **modifiche non distruttive**: ispezione, implementazione, test, lint, commit, push del branch dedicato, PR. Non chiedere approvazioni a ogni file. **Non** effettuare merge, force push, reset distruttivi, cancellazioni di dati, modifiche di permessi o attivazione del trading reale.
5. Preserva modifiche locali dell'utente: controlla `git status` e non sovrascrivere file modificati/non tracciati. Se non puoi operare in sicurezza, spiega il blocco senza simulare un risultato.
6. Non sostituire né disabilitare i provider/modelli configurabili. Preserva la combinazione cloud attualmente utilizzata: `deep=glm-5.3:cloud`, `quick=deepseek-v4.1-flash:cloud` (senza hardcodarla come unico provider).
7. Non aggiungere logica specifica del ticker `BRK-B`. Usa il caso BRK-B come **fixture di regressione**, non come comportamento eccezionale.
8. Non usare output linguistico degli LLM come prova che una validazione deterministica sia stata eseguita: devono esistere funzioni, stati verificabili e test eseguibili.
9. **Esegui questa sola fase P0 in una Pull Request piccola e revisionabile.** Non avviare in autonomia fasi successive che dipendono da un merge non ancora approvato.

## 1. Scopo e risultati attesi

**Obiettivo:** impedire che un dato finanziario superato, una contraddizione fra agenti o un errore aritmetico diventino fatti approvati in un report e, soprattutto, basi di un ordine simulato.

**Questa PR deve implementare:**

- `Source/AsOf Registry`: provenienza, data/ora, periodo contabile e stato dei dati;
- `Fundamentals Freshness Gate`: confronto fra ultimo filing autorevole disponibile e periodo usato dall'analista fondamentale;
- `Cross-Agent Fact Registry/Checker`: fatti canonici condivisi, conflitti espliciti e priorità delle fonti;
- `Arithmetic & Signal Validator`: calcoli deterministici su percentuali, unità e coerenza dei livelli;
- integrazione nel workflow che **blocca o degrada** l'esito a fronte di problemi critici, con **audit machine-readable**;
- test automatici e CI (se non esistono, aggiungi il workflow minimo coerente con lo stack).

**Fuori scope della presente PR:** rifare dashboard, riscrivere tutto il Risk Engine, implementare strategie opzioni/short o un broker adapter nuovo, ottimizzare i prompt per far cambiare il rating, cambiare il modello LLM. Se il Risk/Execution Gate è assente, aggiungi l'interfaccia minima sicura di blocco **senza** fingere che il motore di esecuzione completo esista; documenta la fase successiva.

## 2. Evidenze reali da riprodurre con fixture — report 2026-11-08 17:05:53

Il report BRK-B del **08/11/2026 alle 17:05:53** ha `rating=HOLD`, `deep=glm-5.3:cloud`, `quick=deepseek-v4.1-flash:cloud`. I seguenti punti sono regressioni da intercettare:

| ID | Difetto osservato | Controllo atteso | Esito della fixture |
|---|---|---|---|
| **FRESH-001** | Fundamentals Analyst usa **Q2 2026 / 30 giugno**, sebbene al momento dell'analisi sia pubblicato il **Q3 / 30 settembre** | Verifica filing SEC più recente noto all'`analysis_as_of`, non solo ultimo record disponibile nel vendor | `STALE_CRITICAL` o snapshot aggiornato a Q3; non approvare valori Q2 come correnti |
| **FACT-002** | News/Sentiment riportano **buyback Q3 ~9,6 mld USD**, Fundamentals sostiene «nessun buyback segnalato» | Registrare `buyback_amount`, `period`, `source`; assenza vendor ≠ zero; riconciliare/contestare il fatto | Conflitto rilevato; fonte primaria prevale se verificata |
| **EVENT-003** | Earnings Q3 annunciati **sabato 07/11/2026**, ultima seduta **venerdì 06/11**, prima seduta post-evento **lunedì 09/11** | `check_event_pricing` deterministico con timezone/calendario | `NOT_YET_OBSERVABLE`; nessuna «reazione» prima del 9/11 |
| **VALUATION-004** | P/B vendor `0.0011` basato su mix di classi A/B; report usa patrimonio Q2 e P/B 1,65 senza allineare i periodi | Unità, classi azionarie, share count ed equity con timestamp espliciti | Rifiuta valore vendor non comparabile; ricalcola solo con dati compatibili |
| **MATH-005** | Seduta 05/11/2026: O=584,23, C=572,33, prevC=579,09; il report attribuisce **-2,03%** alla variazione dal close precedente | Calcola separatamente `open_to_close_return` e `close_to_close_return` | Circa **-2,04%** open→close e **-1,17%** close→close; etichette corrette |
| **SIZING-006** | «1/4 dell'allocazione intesa», «trim 20–25%» senza portafoglio, quantità, cash e limiti di rischio | Un testo condizionale non diventa ordine approvato | `execution_approved=false` senza contesto di portafoglio |
| **ENTRY-007** | Proposta 554–558 richiede anche chiusura >557,91; il trigger e l'istante di ordine non sono separati | Modello `signal_at`, `eligible_from`, prezzo limite, scadenza, rivalidazione | Nessun ordine retroattivo o automaticamente eseguibile nello stesso candle |
| **METRIC-008** | I dati P/B, FCF, revenue TTM e utili sono presi da feed con perimetri/definizioni differenti | `metric_definition`, `period_start/end`, `unit`, `filing_accession`, `share_class` | Risultati incompatibili marcati `UNRECONCILED`, non spacciati per verificati |

**Nota sulle fixture:** i numeri sopra sono esempi ricavati dal report, non costanti da inserire nel motore. Puoi copiare *solo i dati essenziali* in fixture JSON/CSV versionate con licenze e attribuzioni appropriate. I test devono girare offline senza credenziali, quote API o prezzi live.

## 3. Contratti dati richiesti

Proponi tipi coerenti con lo stack esistente. I nomi sotto sono indicativi; conserva le convenzioni del progetto.

### 3.1 `VerifiedFact` / Source Registry

Campi minimi:

- `ticker`, `instrument_id`, `fact_key`, `value`, `unit`, `currency` (se applicabile);
- `period_start`, `period_end`, `fiscal_period`, `filing_date`, `published_at`, `retrieved_at`, `as_of`;
- `source_type`, `source_id`, `source_url`, `filing_accession` (per SEC), `share_class`, `calculation_method`;
- `status`: `VERIFIED | PROVISIONAL | STALE | CONFLICTING | UNAVAILABLE | NOT_APPLICABLE`;
- `validation_errors[]`, `confidence_reason` (se applicabile).

Non riempire artificialmente campi assenti. **Missing ≠ 0**. Un'ipotesi/calcolo derivato deve essere distinguibile da un dato pubblicato.

### 3.2 Snapshot condiviso e freshness

- Genera **un solo snapshot `analysis_as_of`** per l'intero report. I singoli agenti devono ricevere la stessa rappresentazione canonica dei fatti certificati e della loro freschezza.
- Per i filing: prima scopri **il documento rilevante più recente noto all'ora di analisi**, poi confrontalo con la massima copertura dei tool fondamentali. Un endpoint SEC con report di periodo successivo **non ancora pubblicato** non deve essere considerato noto nel backtest.
- Per le aziende USA preferisci filing aziendali/SEC (10-Q, 10-K, company facts/XBRL e, ove appropriato, comunicato ufficiale) per i dati contabili; il vendor è una fonte secondaria. Verifica che gli endpoint realmente disponibili supportino il recupero richiesto; in caso contrario segnala `UNAVAILABLE` o `STALE`, senza inventare un filing.
- Non trattare la data di fine trimestre come data di pubblicazione. Usa timestamp pubblicazione con timezone e calendario della borsa per le verifiche causali.
- `Q2` può apparire esplicitamente come **dato storico**, ma non come «ultimo trimestre» se `Q3` è già ufficialmente disponibile all'`as_of`.
- Prevedi priorità e fallback delle fonti, ritardi di vendor, revisioni SEC e multiple share class; registra i conflitti senza sovrascrivere silenziosamente.

### 3.3 Cross-agent consistency

- Raccogli claim strutturati da News, Sentiment, Market, Fundamentals, Research, Trader e Portfolio Manager, almeno per i fatti materiali utilizzati nella conclusione.
- Ogni claim critico deve puntare a un `VerifiedFact` o essere dichiarato `UNVERIFIED/INFERENCE`.
- Se News sostiene buyback Q3 > 0 e Fundamentals dice «nessun buyback», rileva il conflitto. **Assenza di informazione non autorizza una negazione**.
- La frase «il mercato ha assorbito gli earnings» è in conflitto con `NOT_YET_OBSERVABLE`, anche se compare nel solo Sentiment o in un report esterno.
- Permetti differenze di **interpretazione** (BUY/HOLD/SELL, tesi bull/bear), ma non due versioni incompatibili dello stesso **fatto** non motivate da perimetro, periodo, unità o fonte.
- I claim di BNSF, GEICO, Berkshire consolidata devono conservare la dimensione `segment` per evitare che ricavi di gruppo siano attribuiti alla ferrovia.

### 3.4 Arithmetic & technical signals

- Calcola in codice: variazioni **open→close**, **close→close**, rendimento semplice/composto, P/B, FCF, equity/share count, range candle, ATR distance, valore nozionale, budget di rischio e percentuali.
- Usa precisione decimale e tolleranze adeguate; non confrontare numeri formattati o stringhe localizzate.
- Una «divergenza RSI/MACD» deve rispettare una definizione algoritmica esplicita con swing high/low e finestre configurabili; se non verificata, riportare soltanto «momentum in raffreddamento».
- Stop protettivi e target devono essere validati rispetto al **lato** della posizione (`LONG` vs `SHORT`); non confondere un livello di ribilanciamento strategico con uno stop eseguibile.
- Gestisci fuso di mercato, valute, split, classi azionarie, market holidays e `as_of` per qualsiasi strumento supportato (senza eccezioni specifiche BRK-B).

## 4. Policy di esecuzione: FAIL CLOSED

Questa PR deve collegare le verifiche al punto di decisione già presente. Se non esiste un Execution Gate, implementa il **blocco minimo sicuro** sul percorso esistente e documenta il lavoro residuo; non introdurre esecuzione automatica nuova.

**Bloccare** nuove operazioni simulate quando ricorre almeno uno dei seguenti casi:

- filing fondamentale necessario per la strategia obsoleto o impossibile da riconciliare;
- timestamp evento non verificato / `NOT_YET_OBSERVABLE` mentre il piano presuppone una reazione post-evento;
- divergenza materiale non risolta fra agenti sui fatti utilizzati;
- prezzo, quantità, valuta, posizioni, disponibilità o limite di rischio assenti;
- quantità zero/negativa, non finita o superiore ai vincoli; stop incompatibile con il lato; trigger scaduto;
- provider in errore/time-out, validazione schema fallita, rischio di duplicazione ordine;
- tentativo di operare su conto reale oppure richiesta di short/opzioni non espressamente supportati e autorizzati dalla policy.

**Distinguere obbligatoriamente:**

- `strategic_rating` = opinione analitica (`BUY | HOLD | SELL | OVERWEIGHT | UNDERWEIGHT`);
- `execution_action` = `WAIT | BLOCK | EXECUTE_PAPER`;
- `execution_approved` = boolean calcolato da codice deterministico;
- `authorized_orders[]` = vuoto se non approvato;
- `blocking_reasons[]` = codici stabili e testati.

Esempio **atteso per un caso incompleto**, non un risultato già osservato:

```json
{
  "symbol": "BRK-B",
  "strategic_rating": "HOLD",
  "execution_action": "BLOCK",
  "execution_approved": false,
  "authorized_orders": [],
  "blocking_reasons": [
    "FUNDAMENTALS_STALE",
    "PORTFOLIO_CONTEXT_MISSING"
  ]
}
```

Un report `HOLD` può descrivere livelli ipotetici: **non deve creare un ordine in attesa** senza una valutazione successiva e deterministica delle condizioni e del rischio. Se un mercato è chiuso, distingui `WAIT` (assenza di segnale/tempo di esecuzione) da `BLOCK` (violazione o dato critico mancante). Evita ordini duplicati con chiave di idempotenza stabile se esiste già un adapter paper.

## 5. Test obbligatori — Test Driven Regression

Prima di modificare la logica, aggiungi test che falliscono per il comportamento corrente e passano solo dopo la correzione. Conserva il test di regressione in CI.

### Unit test — deterministici

- [ ] Conteggio sessioni calendario evento weekend / after-hours / timezone; `NOT_YET_OBSERVABLE` fino alla prima seduta realmente successiva.
- [ ] Percentuali open→close e close→close distinguibili (05/11: -2,04% vs -1,17%).
- [ ] Equivalenza azioni A/B e P/B con classe/prezzo corretto; assenza unità o share count ⇒ fail.
- [ ] Missing vs zero vs `NOT_APPLICABLE` per buyback, dividendi, cash flow e metriche di settore.
- [ ] Revisioni dei filing senza look-ahead; `filing_date <= as_of`, altrimenti non visibile.
- [ ] Conflitti numerici con tolleranza e perimetro (`company` vs `segment`) e conflitti testuali materializzati.
- [ ] Pattern tecnici: una semplice discesa simultanea di prezzo e RSI **non** implica divergenza ribassista.
- [ ] LONG/SHORT stop direction; size massima vincolata da capitale, esposizione e worst-case con slippage.
- [ ] Trigger condizionali: candle di segnale distinto da tempo di ordine; nessun ordine retroattivo.

### Integration test — provider mocked

- [ ] SEC contiene Q3 pubblicato; vendor contiene solo Q2: `FRESH-001` segnala staleness o ricarica Q3 e aggiorna lo snapshot.
- [ ] News riferisce 9,6 mld di buyback Q3; fundamentals «nessun buyback»: `FACT-002` intercetta e impedisce una decisione basata sulla negazione errata.
- [ ] Sentiment attribuisce una reazione del 06/11 ai conti del 07/11: conflitto bloccato.
- [ ] SEC indisponibile o non riconciliabile: esito **non verificato** esplicito, niente acquisti approvati.
- [ ] Provider market o portfolio in time-out: fail closed, log senza segreti.
- [ ] Stesso evento consegnato due volte: nessun doppio ordine paper.

### End-to-end / contract

- [ ] Report BRK-B 2026-11-08 con dati congelati: `HOLD`, nessun ordine autorizzato, errori materiali spiegati e audit accessibile.
- [ ] Fixture indipendenti dal ticker su **AAPL, NVDA ed ENI.MI**: stesse regole, adattamento a USD/EUR, calendari e settore.
- [ ] Con input valido e portafoglio mock, un percorso `EXECUTE_PAPER` **se già supportato** passa solo i check previsti e non chiama endpoint live.
- [ ] Il runner resta funzionante con i provider LLM configurati; i test CI non richiedono chiamate a Ollama Cloud né credenziali esterne.
- [ ] Test di idempotenza, status code dei blocchi, schema machine-readable e compatibilità della reportistica preesistente.

**Test quality rules:** usa gli strumenti realmente presenti nel repository, aggiungi fixture versionate e clock congelato, copri errori e success path, evita snapshot test basati sulla prosa non deterministica degli LLM. Non inventare una percentuale di coverage: misura e comunica quella ottenuta, aggiungendo una soglia solo quando supportata dal progetto.

## 6. CI / GitHub Actions

1. Ispeziona `.github/workflows/`. Se esiste CI, estendi i job senza duplicarli; altrimenti aggiungi `.github/workflows/ci.yml` per `pull_request` con base `custom` e `workflow_dispatch` (eventualmente `push` su `custom`), con permessi minimi `contents: read`.
2. Esegui lint/format, type-check (se esiste), unit e integration mocked, verifica regressioni P0, scansione accidentale di segreti con strumenti disponibili.
3. La CI deve avere nomi job stabili (es. `unit-tests`, `integration-tests`, `reliability-regression`) che GitHub possa rendere **status checks obbligatori** nel ruleset di `custom`.
4. Nessuna dipendenza obbligatoria da servizi live nella CI. Bloccare il merge in caso di test falliti una volta resi obbligatori i job nel ruleset.
5. Se la modifica del ruleset GitHub richiede un amministratore, **non modificarlo**: segnala esattamente quali check selezionare in Settings → Rules → Rulesets.

## 7. Procedura Git, commit e Pull Request — obbligatoria

1. Verifica `git status --short --branch`, `git remote -v`, `git branch -a`. Se ci sono modifiche dell'utente, preservale: non eseguire checkout/reset che le cancellino.
2. Verifica che `origin/custom` esista e recupera gli aggiornamenti con `git fetch origin` (non ripiegare su `main`). Parti dall'ultimo `origin/custom` in un branch dedicato, **per esempio** `fix/reliability-data-gates` (scegli un nome libero se esiste già).
3. Registra **baseline** dei test prima del cambiamento e fallimenti preesistenti.
4. Implementa test e codice per il solo scope P0. Mantieni piccoli commit significativi, ad esempio:
   - `test(reliability): reproduce stale filings and cross-agent contradictions`
   - `feat(data): add as-of registry and filing freshness gate`
   - `feat(validation): add deterministic fact and arithmetic checks`
   - `fix(execution): block paper orders on invalid inputs`
   - `docs: update reliability contract and changelog`
5. Aggiorna `CHANGELOG.md` nella sezione `[Unreleased]` indicando **solo modifiche effettivamente realizzate**. Aggiorna `AGENTS.md`, `docs/specs/...` e README **solo se necessario**. Non riordinare inutilmente file non pertinenti.
6. Esegui i test alla fine, riporta comandi, risultati, count pass/fail e motivi di eventuali skip. Prima del commit controlla diff e segreti: `git diff --check`, `git diff --stat`, ispezione file staged; non includere `.env`, token, log sensibili o file generati pesanti.
7. Crea commit sul **feature/fix branch**. Se l'accesso remoto è disponibile, push **soltanto quel branch** e apri una PR con **base `custom`**; verifica esplicitamente la base della PR. Non eseguire mai push diretto su `custom`.
8. La PR deve contenere: problema, causa, file modificati, schema dei controlli, tabella defect→test→fix, comandi e risultati test, rischi residui, conferma paper-only e screenshot/report di esempio (se esistono), istruzioni per eventuali check GitHub.
9. **Nessun merge automatico**, nessun bypass dei ruleset e nessuna chiusura PR da parte dell'agente. Se push/PR non sono possibili per limiti di permesso, completa tutto il lavoro locale sicuro e fornisci comandi necessari e impedimento reale.
10. Non dichiarare test verdi, commit eseguiti o PR aperta senza prova verificabile (exit code, SHA commit, URL PR).

## 8. Definition of Done / criterio di accettazione

Questa fase si considera conclusa **solo quando**:

- [ ] Il sistema sa distinguere Q2 storico e Q3 già pubblicato all'`as_of`.
- [ ] I fatti aziendali materiali sono tracciabili (fonte, unità, periodo, timestamp) e condivisi tra agenti.
- [ ] Le contraddizioni P0 non diventano silenziosamente argomenti della decisione finale.
- [ ] La matematica citata nei report è calcolata/verificata da funzioni deterministiche.
- [ ] Un ordine paper non può passare se manca il contesto di portafoglio o esiste un fatto critico invalido.
- [ ] Le fixture di regressione BRK-B e almeno altri tre ticker sono eseguite offline.
- [ ] La CI esegue i test sulla PR verso `custom` e gli eventuali problemi sono documentati.
- [ ] `CHANGELOG.md` aggiornato, commit creati, branch remoto e PR verso `custom` creati **quando i permessi lo consentono**.
- [ ] Nessun ordine reale, nessun merge automatico, nessun segreto esposto.

## 9. Rapporto finale dell'agente — formato richiesto

Fornisci un report compatto con queste sezioni, **includendo prove**:

```text
BRANCH: <nome> (from origin/custom)
COMMIT: <SHA1>, <SHA2> ... oppure BLOCCATO: <motivo>
PR: <URL reale> (base custom) oppure NON APERTA: <motivo>
BASELINE TEST: <comando + pass/fail>
FINAL TEST: <comandi + pass/fail/skip>
CI: <workflow, job names, stato reale o non verificato>
IMPLEMENTATO: <file e funzioni reali>
REGRESSIONI: FRESH-001 ..., FACT-002 ..., MATH-005 ..., stato pass/fail>
EXECUTION SAFETY: <prova fail-closed, paper-only, zero ordini live>
LIMITI RESIDUI: <P0/P1 e motivazioni>
NEXT PR: <unico passo consigliato, senza avviarlo autonomamente>
```

---

### Istruzione finale

**Inizia adesso dall'ispezione del repository e dalla riproduzione dei problemi con i test. Poi implementa le correzioni P0. Non fermarti a una proposta teorica.** Mantieni questa iterazione entro una PR revisionabile, con base `custom`, commit e test verificabili, **senza merge automatico**.

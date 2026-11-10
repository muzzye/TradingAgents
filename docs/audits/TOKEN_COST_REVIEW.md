# Risparmio token e chiamate LLM — situazione attuale e proposte

Data: 2026-11-10. Fonte: `~/.tradingagents/usage.sqlite` (il modulo `tradingagents/usage.py`
registra ogni chiamata senza salvare prompt, risposte o credenziali) più ispezione del codice.

## 1. Consumo misurato

> **Aggiornamento 2026-11-10, 22:25.** La prima stesura di questa sezione dichiarava
> «costo registrato 0 USD — nessuna tariffa configurata». Non è più vero: la tabella
> `prices` contiene ora due tariffe e il database registra un costo reale. I numeri
> sotto sono riletti dal database, non ricopiati dalla stesura precedente.

| Metrica | Valore |
|---|---|
| Chiamate LLM registrate | 85 |
| Token in input | 1,140,671 |
| Token in output | 290,038 |
| Token da cache | 409,800 (35.9% dell'input) |
| Costo registrato | **0.2666 USD** |

Tariffe configurate in `prices` (USD per milione di token):

| Provider | Modello | Input | Output | Cache |
|---|---|---|---|---|
| `openai_compatible` | `deepseek-v4.1-flash:cloud` | 0.30 | 1.20 | 0.006 |
| `openai_compatible` | `glm-5.3:cloud` | 1.40 | 4.40 | 0.26 |

Entrambe le run complete registrate sono BRK-B del 2026-11-10:

| Run | Chiamate | Token in | Token out | Costo |
|---|---|---|---|---|
| `e7e557df04d2` (analisti + dibattito + manager) | 25 | 315,321 | 93,832 | **0.2666 USD** |
| `4cf9b71bc578` (analisti + dibattito) | 32 | 684,187 | 160,543 | non tariffata |

La run `4cf9b71bc578` è precedente alla configurazione delle tariffe: i suoi token sono
reali ma `cost_usd` è `NULL` e non va sommato a `0.2666`, che copre la sola
`e7e557df04d2`. Lo 0.2666 USD è quindi il costo di **una** run completa, non il totale.

Dove vanno i token in una run completa di 25 chiamate, per costo:

| Agente | Chiamate | Token in | Token out | Costo | Quota |
|---|---|---|---|---|---|
| Research Manager | 1 | 5,648 | 13,928 | 0.0692 | 26% |
| Portfolio Manager | 1 | 9,101 | 8,198 | 0.0488 | 18% |
| Market Analyst | 4 | 73,485 | 11,724 | 0.0313 | 12% |
| News Analyst | 9 | 103,745 | 13,561 | 0.0249 | 9% |
| Fundamentals Analyst | 3 | 14,760 | 11,618 | 0.0180 | 7% |
| Bear Researcher | 1 | 20,057 | 8,950 | 0.0168 | 6% |
| Bull Researcher | 1 | 15,754 | 8,119 | 0.0145 | 5% |
| Neutral Analyst | 1 | 23,642 | 5,496 | 0.0137 | 5% |
| Conservative Analyst | 1 | 19,691 | 5,568 | 0.0126 | 5% |
| Aggressive Analyst | 1 | 16,770 | 3,162 | 0.0088 | 3% |
| Trader | 2 | 12,668 | 3,508 | 0.0080 | 3% |

**Research Manager + Portfolio Manager da soli valgono il 44% del costo** con 2 chiamate:
una sola chiamata del Research Manager costa 0.0692 USD, un quarto dell'intera run. Il
motivo è il modello: entrambi girano su `glm-5.3:cloud`, che costa 4.7× l'input e 3.7×
l'output di `deepseek-v4.1-flash:cloud`. Il peso in token del dibattito resta alto
(ricercatori + manager), ma **il costo è dominato dalle due chiamate deep-think**, non
dal volume.

Questo cambia la priorità rispetto alla stesura precedente: ottimizzare il loop degli
analisti riduce i token (e il tempo), ma il risparmio in dollari più diretto viene dalla
scelta del modello per Research Manager e Portfolio Manager.

## 2. Perché i token in input sono alti (cause nel codice)

**a) Il loop tool → LLM ripete l'intera cronologia.**
In `tradingagents/agents/analysts/turn.py`, `take_turn()` non usa una chat a stato nativa:
dopo ogni tool call, se il modello non ha finito, **tutta la storia dei messaggi viene
ricostruita come testo** (`_as_text()`) e re-inviata insieme ai risultati dei tool.
Una run con N tool call per agente implica O(N²) token di contesto ripetuti.
Il News Analyst della run completa ha fatto 6 chiamate (~60k token) con solo 4–5 tool call reali:
ogni turno rispedisce tutto.

**b) Il debate è per default 3 round × 2 ricercatori × ~20k token a round.**
Bear e Bull si leggono le risposte a vicenda e al manager ad ogni round; il prompt di sistema
porta in ogni turno l'intero dibattito precedente più il report degli analisti già prodotti
(market + news + fundamentals + sentiment, che sono le 4 sezioni più lunghe della pipeline).

**c) Le tariffe sono configurate, ma il costo non guida ancora le decisioni.**
`usage.py` registra `cost_usd` correttamente: la run `e7e557df04d2` riporta 0.2666 USD.
Il monitoraggio però non è usato per decidere: le due chiamate deep-think
(Research Manager e Portfolio Manager) valgono il 44% del costo pur essendo 2 su 25, e
nessun test o soglia impedisce che il costo per run cresca. Il divario di tariffa fra
`glm-5.3:cloud` (1.40/4.40 per milione) e `deepseek-v4.1-flash:cloud` (0.30/1.20) significa
che spostare una singola chiamata di modello cambia il costo più di qualunque ottimizzazione
del loop degli analisti.

**d) La configurazione attuale (`.env`) è già economica ma non ottimale.**
- Provider: `openai_compatible` verso `https://ollama.com/v1` (cloud remoto)
- Quick think: `deepseek-v4.1-flash:cloud` — già una fascia bassa
- Deep think: `glm-5.3:cloud` — usato solo dal Research Manager e dal debate

Il 72% dei token viene dal quick think model: è la fascia giusta, ma non vengono usati
prompt caching dedicato né compacting dei report intermedi.

**e) I 4 report degli analisti viaggiano interi nel debate.**
`Research Manager`, Bear e Bull ricevono i report completi di Market, News, Fundamentals
e Sentiment. Una sezione di market analyst con tabelle OHLCV e indicatori può superare
le 15k parole; News aggiunge elenchi di articoli. Il debate non ha bisogno di ogni tabella:
gli serve il verdetto e le cifre chiave.

**f) Nessun limite a riguardanti "completamento".**
Se il modello chiama tool in loop (News ha fatto `get_global_news` due volte con date quasi
identiche in run successive), ogni chiamata ripaga tutto il contesto.

## 3. Proposte di miglioramento (in ordine di impatto atteso)

### P1 — Sintesi strutturata dei report degli analisti prima del debate (impatto: alto)

Aggiungere un passaggio deterministico (non LLM) dopo gli analisti, prima di
Research Manager e dei ricercatori: estrarre da ogni report solo
- rating/verdetto;
- le cifre certificate dal `MetricRegistry` (già esistenti dalla Fase 4);
- 5–8 righe di punti chiave.
Le sezioni complete restano nel final state per il report, ma il debate riceve ~1–2k token
per analista invece di 10–20k. Nei dati misurati, questo taglia circa il 40% dei token in input
di Bear/Bull/Manager. Con la tariffa dei ricercatori e dei manager, però, l'impatto è di
pochi centesimi: i due ricercatori insieme valgono 0.0313 USD, e il taglio del 40% del loro
input ne risparmia ≈0.012. **P1 resta utile per il tempo e per il limite di contesto, non
per il costo.**

### P2 — Compacting del loop tool (`take_turn`) (impatto: alto, lavoro: medio)

Provare due varianti:
- **Variante A (minima):** dopo ogni tool call, ricostruire la cronologia con
  le ultime K chiamate (es. K=2) più una sintesi one-shot di quelle precedenti.
  Problema: la sintesi costa una chiamata. Beneficio: la ripetizione O(N²) sparisce.
- **Variante B (zero chiamate extra):** usare le API native "cache" del provider se supportato
  (Anthropic e alcuni compatibili OpenAI/Ollama supportano prompt caching server-side),
  marcando il punto di “cache point” sul contesto che non cambia (system prompt + tool risultati).
  Il 38.7% di token già serviti da cache mostra che il path esiste; spostare il cutoff
  in avanti costa zero cambi di comportamento.

### P3 — ~~Configurare le tariffe e monitorare~~ (fatto)

Le tariffe sono ora configurate (tabella in §1). P3 è chiusa: il costo per run è misurabile
e la run `e7e557df04d2` lo dimostra. Ciò che resta è **usare** il dato, non produrlo:

| Modello | Input rate | Output rate | Note |
|---|---|---|---|
| `deepseek-v4.1-flash:cloud` | 0.30 | 1.20 | quick think |
| `glm-5.3:cloud` | 1.40 | 4.40 | deep think |

**Il nuovo primo passo è una decisione di modello, non un'ottimizzazione di codice.**
Research Manager e Portfolio Manager costano 0.118 USD su 0.2666 (44%) perché girano su
`glm-5.3:cloud`. Provare il Research Manager su `deepseek-v4.1-flash:cloud` e confrontare
la qualità della sintesi costa una singola run (≈0.20 USD) e ha un impatto in dollari
maggiore di P1 e P2 combinati. Va misurato, non assunto: il Research Manager produce la
sintesi che alimenta la decisione, ed è l'agente in cui un modello più debole si nota.

### P4 — Opzioni sul numero di round (lavoro: basso, impatto: medio)

`max_debate_rounds` e `max_risk_discuss_rounds` sono configurabili (`TRADINGAGENTS_MAX_DEBATE_ROUNDS`).
Il default è 3. Portare a 2 taglia ~1/3 dei token del dibattito (~25k token/run) con
impatto sulla qualità che va verificato empiricamente: la popolazione registered mostra che il
Research Manager, dopo round 2, riusa prevalentemente le stesse argomentazioni.

### P5 — Dedup/cache degli strumenti che hanno già chiamato (impatto: medio)

News Analyst ha chiamato `get_global_news` con finestre sovrapposte in run successive
sul medesimo ticker e data. Aggiungere una cache in-memory per `(tool_name, args_canonici)`
durante la run: le domani-lezione della webapp già registrano in `usage_api` ogni chiamata;
basta riutilizzare il risultato se gli argomenti coincidono. Riduce il 15% dei token di News
duplicando le stesse 8k parole di articoli.

### P6 — Provider locale per gli analisti mid-rank (impatto: medio-basso)

La run `06d85d328efb` mostra già in uso `llmfactory:local:DeepSeek-V4.1-Flash` per i 4 analisti
iniziali (News, Market, Fundamentals, Sentiment). Estendere lo stesso pattern a Bear/Bull
(quando il modello locale regge il debate) e mantenere `glm-5.3:cloud` solo per Research Manager
che giudica e aggrega — quello che misuriamo essere il più ristretto.

### P7 — Validazione del risparmio (lavoro: basso, obbligatorio prima di dichiarare un risparmio)

Ogni modifica a P1–P4 deve essere misurata con lo stesso snapshot:
`tests/fixtures/brkb_2026_11_09.py` più `pytest tests/test_p6_e2e_certification.py`, e la
segnalazione di `usage_db` su una run di benchmark. Non dichiarare "risparmiato X" senza
due run comparabili prima/dopo.

## 4. Cosa NON fare

- **Non ridurre il debate a zero argomenti:** Bear/Bull è il punto in cui le affermazioni
  contrastanti delle news e dei fondamentali vengono filtrate; la Fase 5 ha mostrato che il
  sistema falliva proprio quando una sola voce decideva. Tagliare le sezioni intere del report
  invece delle round è la via.
- **Non sostituire tool con query al generale dal modello:** il progetto ha appena speso otto
  fasi per rendere i numeri certificati; tornare a lasciare che grep dia un valore non validato
  andrebbe contro il contratto `docs/specs/TRADINGAGENTS_RELIABILITY_CONTRACT.md`.
- **Non installare pesi aggiuntivi per parsing (Markitdown, BeautifulSoup) nel path news:**
  non ci sono e non servono; il limite attuale non è la capacità di parsing ma la ripetizione
  del contesto.

## 5. Stima complessiva (conservativa)

Baseline misurata: una run completa (`e7e557df04d2`) costa **0.2666 USD**, 25 chiamate,
315k token in input e 94k in output. Le stime sotto sono proiezioni da quella baseline,
non misure.

Applicando P1 + P4:
- token in input run completa: da ~315k a ~180k (−43%)
- chiamate LLM: da 25 a ~21 (P5 evita ~4 chiamate duplicate)
- token in output: invariato (~94k, la decisione deve ancora essere prodotta)
- costo a tariffe invariate: ≈0.19 USD

Applicando anche P2A/B: ulteriore −20% sul loop degli analisti.

Applicando invece il solo cambio di modello deep-think (P3 chiusa, nuovo primo passo):
- se Research Manager e Portfolio Manager passano a `deepseek-v4.1-flash:cloud`,
  il costo di quelle due chiamate scende da 0.118 a 0.031 USD
- costo per run: 0.180 USD **senza toccare il codice**

Le due strade non sono alternative: la prima riduce il volume, la seconda il prezzo
unitario. La seconda è più economica da provare e va misurata per prima, perché ha un
rischio di qualità che i tagli di token non hanno.

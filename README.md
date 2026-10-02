# AI-Träningsplanerare

En personlig AI-coach för uthållighetsträning (cykel, rullskidor, löpning med mera) som läser din träning från
[intervals.icu](https://intervals.icu) och lägger in en anpassad plan i samma kalender varje morgon.

Idén är att **träningsvetenskap styr belastningen** (CTL/ATL/TSB, ramp, mesocykler, HRV, hard-easy) och att
**AI:n står för passen**: val, placering, beskrivningar och coachfeedback.

## Så fungerar det

1. **Hämtar** aktiviteter, wellness (HRV, sömn, vilopuls), fitness, kalender, tävlingar och väder.
2. **Analyserar** nuläget: readiness, belastning och ramp, mesocykelvecka, compliance, tävlingsvecka och taper.
3. **Avgör läget**: behöver planen göras om (`full`), förlängas (`extend`) eller inte röras (`none`)?
   Vid `none` görs inga AI-anrop.
4. **Planerar med AI**: en prompt med all kontext, flera kandidater, granskning och revision.
5. **Säkrar planen**: ett 20-tal deterministiska regler (hard-easy, HRV-veto, sjukdom/RTP, deload,
   sportgränser, TSS-tak) plus en slutvalidering. Bryter planen mot en hård regel sparas den inte.
6. **Sparar** passen i intervals.icu med strukturerade steg (watt för inomhuscykel, pulszoner för övrigt),
   samt en daglig coachanteckning och en veckorapport.

Detaljerna finns i [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Dokumentation

| Dokument | Innehåll |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Hur koden fungerar idag, målstrukturen och hur man tar sig dit |
| [docs/TRAINING_MODEL.md](docs/TRAINING_MODEL.md) | Metodval: vad som ska vara vetenskap och vad som ska vara AI, och varför |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Det som bör fixas eller byggas, sorterat efter ROI, med status |

## Snabbstart

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
# skapa .env med variablerna i tabellen nedan (minst intervals.icu + en AI-nyckel)
python main.py --dry-run   # visa planen utan att spara
python main.py             # interaktiv morgonkoll + spara
python main.py --auto      # icke-interaktivt (cron/webhook)
```

Övriga flaggor: `--horizon 28` (antal dagar framåt), `--days-history 60`, `--provider gemini|openai|anthropic|ollama|groq|mistral`.

### Daglig input utan terminal
I läget `--auto` läses dagens input från wellness-kommentaren i intervals.icu: fri text som "max 1h",
"ingen tidsbegränsning" eller en skadebeskrivning, eller ett block mellan `[AI_MORNING]` och `[/AI_MORNING]`
med `time_available=`, `injury=` och `athlete_note=`.

### Styra planen från kalendern
- **Manuella pass** du lägger in själv låses och planeras runt.
- **Tävlingar**: prioritet sätts med namnprefix, `B: Namn` eller `C: Namn` (annars A-tävling).
  Sport kan anges som `[Ride]` i namnet.
- **Begränsningar**: ett event (t.ex. en NOTE) vars namn börjar med `Bara:` eller `Ej:` (även `Only:`/`Not:`), t.ex.
  `Ej: löpning` eller `Bara: Zwift`, gäller för eventets datumintervall.

## Miljövariabler

**Krävs**

| Variabel | Beskrivning |
|---|---|
| `INTERVALS_ATHLETE_ID` | Ditt atlet-id i intervals.icu (t.ex. `i12345`) |
| `INTERVALS_API_KEY` | API-nyckel från intervals.icu → Settings → Developer |
| en AI-nyckel | `GEMINI_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GROQ_API_KEY` eller `MISTRAL_API_KEY` (eller `OLLAMA_MODEL` för lokal Ollama) |

**AI-leverantör**

| Variabel | Standard | Beskrivning |
|---|---|---|
| `AI_PROVIDER` | `gemini` | Leverantör (samma som `--provider`) |
| `AI_PROVIDER_PIPELINE` | – | Leverantör för hela planeringspipelinen (har företräde) |
| `GEMINI_MODELS` / `GEMINI_MODEL` | `gemini-2.5-flash` | Kommaseparerad prioritetslista, eller en enda modell |
| `GROQ_MODELS` / `GROQ_MODEL` | `llama-3.3-70b-versatile` | Som ovan för Groq |
| `OPENAI_MODEL`, `OPENAI_BASE_URL` | `gpt-4o`, – | `OPENAI_BASE_URL` pekar mot en lokal OpenAI-kompatibel server (t.ex. llama-server) |
| `ANTHROPIC_MODEL`, `ANTHROPIC_MAX_TOKENS` | `claude-opus-4-5`, `16000` | |
| `MISTRAL_MODEL` | `mistral-large-latest` | Används även som reserv när Gemini/Groq är slut |
| `OLLAMA_MODEL`, `OLLAMA_NUM_PREDICT`, `OLLAMA_THINK` | –, `16384`, av | Lokal Ollama på `localhost:11434` |
| `AI_MIN_REQUEST_INTERVAL_SEC` | `6.0` | Minsta tid mellan Gemini-anrop |

**Atlet och planering**

| Variabel | Standard | Beskrivning |
|---|---|---|
| `AVAILABLE_SPORTS` | alla i katalogen | T.ex. `Ride,VirtualRide,RollerSki,Run` (styrka och vila ingår alltid) |
| `DEFAULT_SPORT`, `FALLBACK_SPORT`, `POWER_SPORTS` | –, –, `VirtualRide` | Huvudsport när historik saknas, ersättningssport när ett pass måste bytas, sporter med effektmätare (får watt-mål) |
| `TARGET_CTL` | `85` | CTL-mål inför A-tävlingen |
| `RISK_TOLERANCE` | `NORMAL` | `HIGH` höjer ACWR-gränsen |
| `MIN_BUDGET_RUN_MIN`, `MIN_BUDGET_ROLLERSKI_MIN` | `60`, `90` | Golv för veckobudget i skadebenägna sporter |
| `MAX_ROLLSKI_PER_WEEK`, `MAX_STRENGTH_PER_PLAN`, `MIN_STRENGTH_GAP_DAYS` | `1`, `2`, `2` | Sportgränser |
| `ATHLETE_LAT`, `ATHLETE_LON`, `ATHLETE_LOCATION` | Karlstad | Plats för väderprognosen |
| `CONTACT_EMAIL` | platshållare | Skickas i User-Agent till met.no, som kräver kontaktuppgift |

**Pipeline (finjustering)**: `PLAN_CANDIDATE_COUNT` (3), `PLAN_REVIEW_MAX_ITERATIONS` (5),
`PLAN_EARLY_STOP_PATIENCE` (2), `PLAN_FIRST_ROUND_TEMPERATURE` (0.35), `PLAN_REVISION_TEMPERATURE` (0.15),
`PLAN_REVIEW_TEMPERATURE` (0.05), `PLAN_PAIRWISE_TEMPERATURE` (0.05), `PLAN_PAIRWISE_SCORE_MARGIN` (1),
`PLAN_TSS_GAP_REVISION_MIN_MISSING` (120), `PLAN_TSS_GAP_REVISION_MIN_PCT` (0.90),
`PLAN_TSS_DEFICIT_VETO_PCT` (0.85), `POSTPROCESS_TSS_REPAIR_TARGET_PCT` (0.95),
`PLAN_INVALID_REVIEW_RANK_PENALTY` (4.0), `PLAN_INVALID_REVIEW_COMPETITIVE_MARGIN` (2.0),
`PLAN_DEBUG_PARSE_FAILURES`, `LOG_LEVEL` (`INFO`/`DEBUG`).

**Webhook-server**: `WEBHOOK_SECRET`, `PORT` (8080).

## Drift

- **GitHub Actions** ([morning.yml](.github/workflows/morning.yml)) kör `python main.py --auto` varje dag
  kl. 07:00 UTC. `.coach_state.json` sparas mellan körningarna i Actions-cachen.
- **Webhook-server** ([server.py](server.py)) för t.ex. Render: `gunicorn server:app`. Den lyssnar på
  intervals.icu-webhooks (`ACTIVITY_ANALYZED`, `WELLNESS_UPDATED`) och kör planeraren i bakgrunden, högst en
  gång var 5:e minut. Observera att Renders disk är flyktig. Se ROADMAP #7 om state.

Lokalt state: `.coach_state.json` (mesocykel, progressionsnivåer, lärda mönster) och `.weather_cache.json`.

## Tester

```bash
python -m unittest discover -s tests -p 'test_*.py' -v
```

`tests/test_regressions.py` innehåller regressionstester för buggarna i [docs/ROADMAP.md](docs/ROADMAP.md).
Riktade körningar fungerar också, t.ex. `python -m unittest tests.test_postprocess_rules -v`.

## Riktlinjer för ny kod

Följ målbilden i [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): ren logik utan IO i analys- och
planeringslagret, extern IO i `integrations/`, och AI som ett valfritt lager som valideras mot samma regler
som resten av planen.

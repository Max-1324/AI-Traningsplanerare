# AI-Träningsplanerare

En personlig AI-coach för uthållighetsträning (cykel, rullskidor, löpning med mera) som läser din träning från
[intervals.icu](https://intervals.icu) och lägger in en anpassad plan i samma kalender varje morgon.

Grundprincipen är **deterministisk kärna, AI i kanten**. Koden bestämmer belastningen med träningsvetenskap
(CTL/ATL/TSB, ramp, mesocykler, HRV, hard-easy) och väljer passen ur ett passbibliotek med progression. AI:n får
välja mellan godkända alternativ och skriver beskrivningar och coachfeedback. Planen fungerar även utan AI.

## Så fungerar det

1. **Hämtar** aktiviteter, wellness (HRV, sömn, vilopuls, CTL/ATL), kalender, tävlingar och väder.
2. **Analyserar** nuläget: readiness, belastning och ramp, mesocykelvecka, compliance, tävlingsvecka och taper.
3. **Sätter veckomål**: ett TSS-mål per kalendervecka. Finns en årsplan i intervals.icu används dess veckomål
   och återhämtningsveckor. Annars räknas målen ut från CTL, mesocykel (deload på rätt vecka), ramp och
   nedtrappning inför tävlingar. Målen skrivs tillbaka till kalendern, så att fitnessgrafen visar prognosen.
4. **Bygger planen**: ett veckoskelett (nyckelpass, långpass, lätta dagar, vila) blir konkreta pass för
   10 dagar framåt. Varje vecka får normalt två hårda pass, ett VO2max-pass och ett tröskelpass, och resten
   lugnt. Antalet följer din återhämtning: ett vid låg HRV, djupt negativ form, missade nyckelpass eller
   nyss sjuk, tre när du har klarat två i veckan en tid, återhämtar dig bra och veckan har tid för det. Låg HRV, kort sömn,
   lite tid eller skada påverkar dessutom idag och imorgon, och nyckelpasset flyttas då senare i veckan.
5. **Avgör läget**: behöver planen göras om (`full`, alltid på måndagar), förlängas (`extend`) eller inte
   röras (`none`)? Vid `none` görs inga AI-anrop.
6. **Berikar med AI** i ett anrop, och bara för de pass som ska sparas. Planen kontrolleras sedan igen mot
   säkerhetsreglerna och valideringen. Misslyckas AI:n används den deterministiska planen.
7. **Sparar** passen i intervals.icu med strukturerade steg (watt för inomhuscykel, pulszoner för övrigt),
   samt en daglig coachanteckning och en veckorapport.

Den gamla AI-först-pipelinen (flera kandidater, granskning, revision) finns kvar som `--engine legacy`.

Detaljerna finns i [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Dokumentation

| Dokument | Innehåll |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Hur koden fungerar idag, målstrukturen och hur man tar sig dit |
| [docs/TRAINING_MODEL.md](docs/TRAINING_MODEL.md) | Metodval: vad som ska vara vetenskap och vad som ska vara AI, och varför |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Det som bör fixas eller byggas, sorterat efter ROI, med status |
| [docs/LANDSCAPE.md](docs/LANDSCAPE.md) | Jämförelse med TrainerRoad, Xert, JOIN, Garmin, Runna, IntervalCoach m.fl.: vad som saknas |

## Snabbstart

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
# skapa .env med variablerna i tabellen nedan (minst intervals.icu + en AI-nyckel)
python main.py --dry-run   # visa planen utan att spara
python main.py             # interaktiv morgonkoll + spara
python main.py --auto      # icke-interaktivt (cron/webhook)
python main.py --no-ai     # helt utan AI: bara den deterministiska planen
```

Övriga flaggor: `--engine deterministic|legacy`, `--horizon N` (dagar framåt; standard 9 för deterministic och
28 för legacy), `--days-history 60`, `--provider gemini|openai|anthropic|ollama|groq|mistral`.
En komplett mall för `.env` finns i [.env.example](.env.example).

### Daglig input utan terminal
I läget `--auto` läses dagens input från wellness-kommentaren i intervals.icu: fri text som "max 1h",
"ingen tidsbegränsning" eller en skadebeskrivning, eller ett block mellan `[AI_MORNING]` och `[/AI_MORNING]`
med `time_available=`, `injury=` och `athlete_note=`.

### Styra planen från kalendern
- **Manuella pass** du lägger in själv låses och planeras runt.
- **Tävlingar**: prioritet läses från tävlingens kategori i intervals.icu (A/B/C). Som reserv fungerar
  namnprefix, `B: Namn` eller `C: Namn`, annars räknas den som A-tävling. Sport kan anges som `[Ride]` i namnet.
  A-tävlingar får två veckors nedtrappning, B-tävlingar några dagar och C-tävlingar nästan ingen.
- **Årsplan**: gör du en säsongsplan med intervals.icu:s *Annual Training Plan Builder* (Supporter-nivån) följer
  planeraren dess veckomål, faser, återhämtningsveckor och fördelning mellan sporter (t.ex. 2,5 h löpning och
  5 h cykel). Löpningen ökar ändå högst 10 % i veckan från vad du faktiskt sprungit, och det som inte får plats,
  eller som en skada stoppar, flyttas till de andra sporterna i planen. Fasen styr passens format: Base ger
  tempo och korta VO2max-intervaller, Build tröskelintervaller och längre VO2max-intervaller. Utan årsplan
  och tävling byts formatet var sjätte vecka. En vecka vars mål ligger minst 20 % under veckan före
  räknas som återhämtningsvecka. Ett veckomål som skulle höja CTL mer än `RAMP_CTL_MAX` per vecka trappas in i
  stället, så en plan med fler timmar än du är van vid blir ingen chockstart. Utan årsplan skriver planeraren sina
  egna veckomål som `TARGET`-event (stäng av med `SYNC_WEEK_TARGETS=off`). Dina egna veckomål skrivs aldrig över.
- **Skadad**: en `INJURED`-händelse stoppar bara sporterna skadan påverkar. "Knä" eller "löparskada" stoppar
  löpningen men inte cykeln, och "axel" stoppar cykeln. Sätt tillgängligheten till *begränsad* om all träning
  ska vara kort och lätt, eller *inte tillgänglig* för ingen träning alls.
- **Sjuk eller bortrest**: lägg in en `SICK`- eller `HOLIDAY`-händelse med tillgänglighet.
  *Inte tillgänglig* ger inga pass alls, och *begränsad* ger bara korta, lätta pass. Utan angiven tillgänglighet
  räknas sjuk som inte tillgänglig och semester som normal.
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

**Planeringsmotor**

| Variabel | Standard | Beskrivning |
|---|---|---|
| `PLANNER_ENGINE` | `deterministic` | `legacy` ger den gamla AI-först-pipelinen (samma som `--engine`) |
| `PLANNER_AI` | `on` | `off` hoppar över AI-berikningen (samma som `--no-ai`) |
| `DETAIL_HORIZON_DAYS` | `9` | Dagar framåt med konkreta pass (utöver idag) |
| `COACH_LANGUAGE` | `English` | Språk för AI-texterna, t.ex. `Swedish` |
| `RAMP_CTL_PER_WEEK`, `RAMP_CTL_MAX` | `4.0`, `6.0` | Planerad CTL-ökning per byggvecka, och tak. Mot en A-tävling används rampen som krävs för att nå `TARGET_CTL`, inom taket |
| `DELOAD_LOAD_FACTOR` | `0.70` | Deloadveckans dagliga belastning som andel av CTL |
| `KEY_SESSIONS_PER_WEEK` | *(anpassas)* | Tak för antalet hårda pass per vecka (0–3). Utan värde avgör din återhämtning: normalt 2, ibland 1 eller 3 |
| `STRENGTH_PER_WEEK` | `1` | Styrkepass per vecka (begränsas också av `MAX_STRENGTH_PER_PLAN`) |
| `SECONDARY_SESSIONS_PER_WEEK` | `1` | Pass i en kompletterande sport (t.ex. rullskidor) per vecka, när årsplanen inte anger fördelning per sport |
| `LONG_SESSION_SHARE` | `0.35` | Långpassets största andel av veckans TSS |
| `WEEKDAY_MAX_MIN` | `120` | Längsta uthållighetspass måndag–fredag |
| `CATCH_UP_CAP` | `1.15` | Resten av en påbörjad vecka får högst så här mycket mer än sin andel (inget ikapptränande) |
| `PLAN_ENRICH_TEMPERATURE` | `0.3` | Temperatur för AI-berikningen |
| `SYNC_WEEK_TARGETS` | `on` | Skriv veckomålen som `TARGET`-event i intervals.icu (bara veckor utan egna mål) |

**Atlet och planering**

| Variabel | Standard | Beskrivning |
|---|---|---|
| `AVAILABLE_SPORTS` | alla i katalogen | T.ex. `Ride,VirtualRide,RollerSki,Run` (styrka och vila ingår alltid) |
| `DEFAULT_SPORT`, `FALLBACK_SPORT`, `POWER_SPORTS` | –, –, `VirtualRide` | Huvudsport när historik saknas, ersättningssport när ett pass måste bytas, sporter med effektmätare (får watt-mål) |
| `TARGET_CTL` | `85` | CTL-mål inför A-tävlingen. Rampen planeras aldrig förbi målet |
| `RISK_TOLERANCE` | `NORMAL` | `HIGH` höjer ACWR-gränsen |
| `MIN_BUDGET_RUN_MIN`, `MIN_BUDGET_ROLLERSKI_MIN` | `60`, `90` | Golv för veckobudget i skadebenägna sporter |
| `MAX_ROLLSKI_PER_WEEK`, `MAX_STRENGTH_PER_PLAN`, `MIN_STRENGTH_GAP_DAYS` | `1`, `2`, `2` | Sportgränser |
| `ATHLETE_LAT`, `ATHLETE_LON`, `ATHLETE_LOCATION` | Karlstad | Plats för väderprognosen |
| `CONTACT_EMAIL` | platshållare | Skickas i User-Agent till met.no, som kräver kontaktuppgift |

**Legacy-pipeline (finjustering)**: `PLAN_CANDIDATE_COUNT` (3), `PLAN_REVIEW_MAX_ITERATIONS` (5),
`PLAN_EARLY_STOP_PATIENCE` (2), `PLAN_FIRST_ROUND_TEMPERATURE` (0.35), `PLAN_REVISION_TEMPERATURE` (0.15),
`PLAN_REVIEW_TEMPERATURE` (0.05), `PLAN_PAIRWISE_TEMPERATURE` (0.05), `PLAN_PAIRWISE_SCORE_MARGIN` (1),
`PLAN_TSS_GAP_REVISION_MIN_MISSING` (120), `PLAN_TSS_GAP_REVISION_MIN_PCT` (0.90),
`PLAN_TSS_DEFICIT_VETO_PCT` (0.85), `POSTPROCESS_TSS_REPAIR_TARGET_PCT` (0.95),
`PLAN_INVALID_REVIEW_RANK_PENALTY` (4.0), `PLAN_INVALID_REVIEW_COMPETITIVE_MARGIN` (2.0),
`PLAN_DEBUG_PARSE_FAILURES`, `LOG_LEVEL` (`INFO`/`DEBUG`).

**Webhook-server**: `WEBHOOK_SECRET`, `PORT` (8080), `GENERATOR_TIMEOUT_SEC` (900).

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

Testerna körs också i GitHub Actions vid varje push och pull request ([tests.yml](.github/workflows/tests.yml)).

| Testfil | Vad den täcker |
|---|---|
| `tests/test_periodization.py` | Veckomål: mesocykel per vecka, deload, ramp, taper och tävlingsprioritet |
| `tests/test_deterministic_planner.py` | Planeraren i flera hundra kombinationer av situationer: planen ska alltid klara säkerhetsreglerna och valideringen |
| `tests/test_enrichment.py` | AI-berikningen: begränsade val, texter och reservkedjan |
| `tests/test_regressions.py` | Regressionstester för buggarna i [docs/ROADMAP.md](docs/ROADMAP.md) |

Riktade körningar fungerar också, t.ex. `python -m unittest tests.test_deterministic_planner -v`.

## Riktlinjer för ny kod

Följ målbilden i [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): ren logik utan IO i analys- och
planeringslagret, extern IO i `integrations/`, och AI som ett valfritt lager som valideras mot samma regler
som resten av planen.

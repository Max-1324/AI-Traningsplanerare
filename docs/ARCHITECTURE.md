# Arkitektur

Dokumentet beskriver hur planeraren fungerar **idag**, vilken **målstruktur** som rekommenderas och hur
koden kan flyttas dit stegvis. Motiveringen till målstrukturen finns i [TRAINING_MODEL.md](TRAINING_MODEL.md),
och ordningen i [ROADMAP.md](ROADMAP.md).

---

## Del 1: Så fungerar det idag

### Flödet i en körning (`python main.py --auto`, deterministisk motor)

```text
main.py
 └─ training_plan/app/main.py: main()
     1. Hämta      7 dataset parallellt från intervals.icu + met.no; fitness räknas ur wellness
     2. Städa      datakvalitet: filtrera orimliga aktiviteter, blanka ogiltig HRV/sömn
     3. Analysera  HRV (ln rMSSD mot egen baslinje), readiness, mesocykel, CTL-trajektoria, compliance,
                   ACWR, tävlingsvecka, utvecklingsbehov, coachlager …
     4. Planera    app/deterministic.py: build_planner_inputs()
                     → engine/periodization.py: build_week_targets()   ett TSS-mål per vecka
                     → engine/skeleton.py: build_week_skeleton()       dagsroller per vecka
                     → engine/planner.py: build_deterministic_plan()   konkreta pass + alternativ
     5. Läge       resolve_update_mode(): none / extend / full (alltid full på måndagar).
                   Vid "none" avslutas körningen utan AI-anrop
     6. Berika     engine/pipeline/enrich.py: ett AI-anrop för de pass som ska sparas
                   (val bland alternativen + texter), sedan apply_safety_rules() och validering.
                   Reserv: AI-texter utan val → ren deterministisk plan
     7. Validera   slutlig deterministisk validering; stoppas vid hårda fel
     8. Spara      radera framtida AI-event och skapa nya (full) eller lägg till saknade datum (extend),
                   daglig coachanteckning, veckorapport (måndagar/full)
     9. State      .coach_state.json: mesocykel, progressionsnivåer, failure memory, utfall
```

Med `--engine legacy` ersätts steg 4–6 av den gamla AI-först-pipelinen: en stor prompt för 29 dagar,
3 kandidater, cirka 20 efterhandsregler, AI-granskning, parvis jämförelse och upp till 5 revisionsrundor.

### Var koden ligger

| Mapp | Ansvar | Viktigaste filerna |
|---|---|---|
| `training_plan/app/` | Orkestrering | `main.py` (hela flödet), `deterministic.py` (analys → planerarens indata, AI-kontext, berikning + validering) |
| `training_plan/core/` | Delad grund | `config.py` (env), `catalogs.py` (sporter, zoner), `models.py` (Pydantic: `PlanDay`, `AIPlan`, `PlanReview`, `AppState`), `common.py` (logging + gemensamma imports), `cli.py` |
| `training_plan/engine/analysis/` | Analys av nuläget | `data.py` (datakvalitet, HRV, readiness, motivation), `load.py` (ACWR, sportbudget, ramp, TSS-budget), `strategy.py` (fas, tävlingsvecka, RTP, taper, utvecklingsbehov), `athlete.py` (zoner, atletprofil) |
| `training_plan/engine/planning/` | Planeringshjälp | `state.py` (state-fil, mesocykel, failure memory), `metrics.py` (passklassning, polarisering, CTL-trajektoria), `learning.py` (compliance, mönster), `workouts.py` (progression, prehab, FTP-test) |
| `training_plan/engine/insights/` | "Coachlager" | `profiles.py`, `execution.py`, `forecast.py`: kapacitetskarta, minimum effective dose, friktion, benchmarks, prognos, säsongsplan |
| `training_plan/engine/prompt/` | Promptbygge | `generation.py` (huvudprompten), `sections.py`, `inputs.py` (morgonfrågor) |
| `training_plan/engine/` (kärnan) | Deterministisk planering | `periodization.py` (`WeekTarget`, veckomål), `planner.py` (`build_deterministic_plan`: pass, alternativ, `horizon_tss_target`, `max_hard_days`), `skeleton.py` (dagsroller per vecka) |
| `training_plan/engine/pipeline/` | AI | `enrich.py` (berikning: ett anrop, begränsade val, reservkedja). Legacy: `__init__.py` (`run_plan_pipeline`), `core.py`, `prompts.py`, `reviews.py`, `scoring.py`, `candidates.py`, `outcomes.py` |
| `training_plan/engine/postprocess/` | Säkerhetsregler | `__init__.py` (`apply_safety_rules` för den deterministiska motorn, `post_process` för legacy), `recovery.py` (hard-easy, HRV, sjukdom, RTP, deload, styrka …), `load.py` (TSS per steg, TSS-tak/-reparation för legacy), `injury.py` (`injury_restrictions`, rehab), `nutrition.py` |
| `training_plan/engine/validation/` | Slutkontroll | `rules.py` (validering), `structure.py` (reparation), `adapters.py` |
| `training_plan/engine/ai/` | LLM-klient | `client.py` (gemini/openai/anthropic/ollama/groq/mistral), `parsing.py`, `display.py` (utskrift, uppdateringsläge) |
| `training_plan/engine/` | Övrigt | `libraries.py` (styrke-/prehabbibliotek, constraints från kalendern), `context.py` (`PromptContext`, legacy), `utils.py` (bl.a. `race_priority`) |
| `training_plan/integrations/` | Extern IO | `intervals_client.py` (hämta), `intervals_events.py` (spara/radera event), `notes.py` (coachanteckning, veckorapport), `weather.py` (met.no) |
| rot | Körning | `main.py` (CLI), `server.py` (webhook för Render), `.github/workflows/morning.yml` (daglig cron), `.github/workflows/tests.yml` (CI) |

Flera filer är **kompatibilitetsfasader** som bara återexporterar namn: `engine/ai/__init__.py`,
`engine/prompt_builders.py`, `integrations/services.py`, `engine/analysis/__init__.py` och
`engine/planning/__init__.py`. Tillsammans med 55 `import *` gör det beroendegrafen svår att följa.

### Kvarvarande svagheter
- **Allt i en funktion.** `main()` är fortfarande stor, nu med två motorer, och svår att testa i delar.
- **Fasader och `import *`** gör beroendena svåra att följa (ROADMAP #16).
- **State på två ställen.** GitHub Actions-cache och Renders flyktiga disk har var sin `.coach_state.json`
  (ROADMAP #7).

Löst: AI:n äger inte längre planen (en deterministisk plan finns alltid), och horisonten periodiseras vecka
för vecka (ROADMAP #6).

---

## Del 2: Målstruktur

### Principer
1. **Ren kärna, IO i kanten.** Analys och planering är rena funktioner som går att testa utan nätverk.
2. **Planen är giltig utan AI.** AI:n berikar en redan godkänd plan och valideras mot samma regler.
3. **Ett objekt per steg.** `AthleteSnapshot` → `WeekTargets` → `WeekPlan`, i stället för 50 lösa variabler.
4. **Explicita imports** och inga fasader.

### Mappstruktur

```text
AI-Traningsplanerare/
├── main.py                     # CLI: python main.py [--auto] [--dry-run]
├── server.py                   # tunn webhook → coach.app.run()
├── pyproject.toml              # beroenden + ruff + pytest
├── .env.example
├── docs/
│   ├── ARCHITECTURE.md
│   ├── ROADMAP.md
│   └── TRAINING_MODEL.md
├── coach/
│   ├── config.py               # Settings: all konfiguration på ett ställe, typad
│   ├── domain/                 # rena datatyper, ingen IO
│   │   ├── models.py           # PlanDay, WorkoutStep, WeekTarget, WeekPlan, AppState …
│   │   ├── catalogs.py         # sporter och zoner (EN zon→IF-tabell)
│   │   └── library.py          # pass- och styrkebibliotek med progressionsnivåer
│   ├── data/                   # all extern IO
│   │   ├── intervals.py        # hämta + synka (bulk, idempotent)
│   │   ├── weather.py
│   │   └── state_store.py      # läs/skriv state (fil idag, extern lagring senare)
│   ├── analysis/               # "Var står atleten nu?"
│   │   ├── load.py             # CTL/ATL/TSB, ramp, volym per sport
│   │   ├── readiness.py        # HRV (ln rMSSD + SWC), sömn, vilopuls, readiness
│   │   ├── history.py          # compliance, passkvalitet, polarisering
│   │   └── snapshot.py         # AthleteSnapshot: allt ovan i ett objekt
│   ├── planning/               # "Vad ska göras?" (deterministiskt)
│   │   ├── periodization.py    # mesocykel, WeekTarget per vecka, taper
│   │   ├── skeleton.py         # dagsroller per vecka (nyckel/lång/lätt/vila)
│   │   ├── sessions.py         # välj biblioteksspass, skala mot dagens TSS-intervall
│   │   ├── rules.py            # säkerhetsregler: hard-easy, HRV, skada, sportgränser
│   │   └── autoregulation.py   # justera idag/imorgon utifrån readiness
│   ├── ai/                     # LLM i kanten, alltid valfri
│   │   ├── providers.py        # leverantörer + structured output
│   │   ├── prompts.py          # små, fokuserade promptar
│   │   └── coach.py            # enrich_plan(), triage_injury(), weekly_feedback()
│   ├── reporting/              # terminalutskrift, daglig coachanteckning, veckorapport
│   └── app.py                  # orkestrering, se flödet nedan
└── tests/
    ├── unit/                   # en testfil per modul
    ├── golden/                 # fixture → förväntad plan (fångar oavsiktliga ändringar)
    └── fixtures/
```

### Flödet i målstrukturen

```text
app.run()
  1. data.intervals.fetch_all()            → rådata
  2. analysis.snapshot.build(rådata)       → AthleteSnapshot (load, readiness, historik, tävlingar)
  3. app.decide_mode(snapshot, kalender)   → NONE | ADJUST_TODAY | REPLAN_WEEK
       NONE         → klart (inga AI-anrop)
       ADJUST_TODAY → planning.autoregulation på idag/imorgon
       REPLAN_WEEK  → steg 4–6
  4. planning.periodization.targets(...)   → [WeekTarget] för 4 veckor (deload, taper ingår)
  5. planning.skeleton + sessions + rules  → WeekPlan (7–10 dagar, giltig utan AI)
  6. ai.coach.enrich_plan(WeekPlan)        → beskrivningar och val bland godkända alternativ
       └─ planning.rules.validate() igen; vid fel används WeekPlan oförändrad
  7. data.intervals.sync(plan)             → idempotent upsert av event
  8. reporting.*                           → daglig anteckning, veckorapport
  9. data.state_store.save(state)
```

### Mappning: nuvarande → mål

| Nuvarande | Mål | Kommentar |
|---|---|---|
| `core/models.py`, `core/catalogs.py` | `domain/models.py`, `domain/catalogs.py` | Slå ihop zon→IF-tabellerna (ROADMAP #10) |
| `core/config.py`, `core/common.py`, `core/cli.py` | `config.py`, `main.py` | `common.py` med `import *` försvinner |
| `integrations/intervals_client.py`, `intervals_events.py` | `data/intervals.py` | Bulk/upsert (#15) |
| `integrations/weather.py` | `data/weather.py` | Oförändrad |
| `integrations/notes.py` | `reporting/` | |
| `engine/analysis/data.py` | `analysis/readiness.py` | HRV enligt ln(rMSSD) och SWC (#11) |
| `engine/analysis/load.py` (utom `tss_budget`) | `analysis/load.py` | |
| `engine/planning/metrics.py`, `learning.py` | `analysis/history.py` | |
| `engine/planning/state.py` (mesocykel), `engine/analysis/load.py` `tss_budget`, `engine/planning/metrics.py` `ctl_trajectory`, `engine/analysis/strategy.py` (taper, tävlingsvecka) | `planning/periodization.py` | Veckomål per vecka (#6) |
| `engine/skeleton.py` | `planning/skeleton.py` | Får `WeekTarget` som indata |
| `engine/planning/workouts.py`, `engine/libraries.py` | `domain/library.py`, `planning/sessions.py` | |
| `engine/postprocess/*`, `engine/validation/*` | `planning/rules.py`, `planning/autoregulation.py` | Regler körs *före* AI i stället för efter |
| `engine/ai/client.py`, `parsing.py` | `ai/providers.py` | Structured output (#5) |
| `engine/prompt/*`, `engine/pipeline/*` | `ai/prompts.py`, `ai/coach.py` | Kraftigt bantat: inga kandidater, ingen review-loop |
| `engine/insights/*` | `analysis/` eller borttaget | Behåll bara det som ändrar beslut (#19) |
| `engine/context.py` (`PromptContext`) | `analysis/snapshot.py` (`AthleteSnapshot`) | |
| `app/main.py` | `app.py` | Uppdelad i steg 1–9 ovan |

### Migrera stegvis, inte i ett svep
Att flytta filerna först ger mest dubbelarbete, eftersom planeringskärnan ändå ska skrivas om. Rekommenderad
ordning (samma som i ROADMAP):

1. ✅ Buggfixar och CI i nuvarande struktur.
2. ✅ Veckomål per vecka (`engine/periodization.py`), inkopplade i skelettet och planeraren.
3. ✅ Kortare horisont (10 dagar) och ett AI-anrop i stället för cirka 20.
4. ✅ Deterministiskt passval där AI:n berikar (`engine/planner.py` + `engine/pipeline/enrich.py`).
   Legacy-pipelinen finns kvar bakom `--engine legacy`. Den kan tas bort när den nya motorn har fungerat
   ett tag.
5. ⬜ Flytta det som återstår till målstrukturen och ta bort fasaderna. `periodization.py` och `planner.py`
   blir då `planning/periodization.py` och `planning/sessions.py`, och `enrich.py` blir `ai/coach.py`.
6. ⬜ Rensa insikter och lägg till backtesting.

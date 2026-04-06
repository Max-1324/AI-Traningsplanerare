# Projektstruktur

Det här projektet innehåller två parallella körvägar:

- [training_plan_generator.py](./training_plan_generator.py): originalfilen som är kvar oförändrad.
- [main.py](./main.py) + paketet [`training_plan/`](./training_plan): den nya uppdelade versionen med samma ansvar fördelat på flera filer och mappar.

## Trädstruktur

```text
AI-Traningsplanerare/
├── main.py
├── server.py
├── requirements.txt
├── training_plan_generator.py
├── .env
├── .coach_state.json
├── .weather_cache.json
├── .github/
│   └── workflows/
│       └── morning.yml
└── training_plan/
    ├── __init__.py
    ├── app/
    │   ├── __init__.py
    │   └── main.py
    ├── core/
    │   ├── __init__.py
    │   ├── catalogs.py
    │   ├── cli.py
    │   ├── common.py
    │   ├── config.py
    │   └── models.py
    ├── engine/
    │   ├── __init__.py
    │   ├── ai.py
    │   ├── analysis.py
    │   ├── insights.py
    │   ├── libraries.py
    │   ├── pipeline.py
    │   ├── planning.py
    │   └── postprocess.py
    └── integrations/
        ├── __init__.py
        └── services.py
```

## Huvudmappar

### `training_plan/app`
- Ansvar: applikationens startflöde.
- Innehåller: [main.py](./training_plan/app/main.py), som kör hela planeringsflödet.
- Exempel på kod här: uppstart, anropsordning mellan analys, AI och sparning.

### `training_plan/core`
- Ansvar: gemensam grund som resten av koden bygger på.
- Innehåller:
  - [cli.py](./training_plan/core/cli.py): argumentparser.
  - [config.py](./training_plan/core/config.py): miljövariabler och konfiguration.
  - [catalogs.py](./training_plan/core/catalogs.py): konstanter, sportnamn, zoner, uppslagstabeller.
  - [models.py](./training_plan/core/models.py): Pydantic-modeller som `PlanDay` och `AIPlan`.
  - [common.py](./training_plan/core/common.py): delade imports, logging och gemensamma hjälpresurser.
- Lägg ny kod här när den är generell och används av flera delar av systemet.

### `training_plan/engine`
- Ansvar: själva planeringsmotorn.
- Innehåller:
  - [libraries.py](./training_plan/engine/libraries.py): träningsbibliotek och constraints.
  - [planning.py](./training_plan/engine/planning.py): state, mesocykler, progression och planeringslogik.
  - [analysis.py](./training_plan/engine/analysis.py): readiness, ACWR, TSS-budget, race/taper-analyser.
  - [insights.py](./training_plan/engine/insights.py): forecast, benchmark-system, kapacitetskarta, MED, friktion, individualisering och säsongsplan.
  - [pipeline.py](./training_plan/engine/pipeline.py): generate/review/score/decision-pipeline, revisionsloop och outcome tracking.
  - [postprocess.py](./training_plan/engine/postprocess.py): tvingande regler som justerar planen efter AI-svaret.
  - [ai.py](./training_plan/engine/ai.py): promptbygge, AI-anrop, parsing och utskrift.
- Lägg ny kod här när den ändrar eller utvärderar träningsplanen.

## Planeringspipeline

Den nya vägen i `training_plan/` använder nu en tydlig kvalitetsgate:

`generate_plan() -> review_plan() -> score_plan() -> accept/revise/reject`

- `generate_plan()`: skapar nu tre kandidatplaner från träningsdata, mål, constraints och race demands.
- `review_plan()`: kör en separat skeptisk roll som granskar målkoppling, nyckelpass, effektivitet, risk, individualisering och race specificity.
- `score_plan()`: sätter numeriska betyg för effekt, risk, specificitet, enkelhet och confidence.
- `accept/revise/reject`: låter review/scoring välja bästa kandidaten, och avgör sedan i kod om den får användas direkt, måste revideras eller ska byggas om.
- Outcome tracking och historisk validering sparas i state så att framtida planer kan kalibreras mot hur tidigare planer faktiskt föll ut.

## Modellkarta

Tabellen nedan sammanfattar nuläget i modellen, vad som redan finns, vad som fortfarande saknas för en mer komplett coachmodell och hur mycket som går att bygga från historisk data.

| Område | Finns redan | Saknas för full modell | Kan byggas från dina 2 år data? | Behöver subjektiv data? | Svårighet |
|---|---|---|---|---|---|
| Belastning och återhämtning | CTL/TSB, ACWR, per-sport ACWR, ramp, HRV, readiness, deload/taper, autoreglering | Individuell toleransmodell per fas, sport och passkategori | Ja | Hjälper | Medel |
| Race demands | Race demands-analys, gap mot målet, långpass/fueling-logik, must-have sessions | Tydligare kapacitetskarta per mål: tröskel, durability, pacing, climbing, fueling | Ja | Nej | Medel |
| Passkvalitet | Session quality, NP/IF, progression, compliance, yesterday-analysis | Objektiv kvalitetsbedömning mot planerat nyckelpass och faktisk adaptation | Ja | Hjälper mycket | Medel |
| Individualisering | Learned patterns, motivation, response profile, outcome tracking | Modell för hur just du svarar på olika stimuli över tid | Ja, delvis | Ja, för full precision | Hög |
| Prestationsforecast | CTL-trajektoria, vissa readiness/race-proxyer, historisk outcome tracking | Prognos för tröskel, durability, race readiness och sannolik utveckling | Delvis | Hjälper | Hög |
| Psykologisk belastning | Motivation, feel, RPE, compliance-mönster | Modell för mental kostnad/friktion av pass, veckor och block | Svagt | Ja | Medel |
| Nutrition readiness | CHO-regler, fueling-gap, periodiserad nutrition, race nutrition-logik | Verklig fueling-tolerans, magtolerans och nutrition readiness score | Delvis | Ja, helst notes | Medel |
| Coach confidence / uncertainty | Coach confidence, review-score-revise-pipeline, historical validation, outcome tracking | Osäkerhetsmodell per delbeslut, kalibrering av confidence | Ja | Lite | Medel |
| Datakvalitet / observability | Datakvalitetskontroll och warnings finns | Mer granular trust-modell per datakälla/metric, bättre observability | Ja | Nej | Medel |
| Execution robustness / planfriktion | Must-hit vs flex, simplicity score, constraints, compliance, 3 kandidater i pipeline | Explicit friktionsscore och fallback-struktur per pass/dag | Ja | Hjälper | Medel |
| Skaderisk / tissue-specific risk | Injury flag, biometric vetoes, return-to-play, prehab, per-sport ACWR | Modell för vävnadsspecifik risk och återfallsrisk | Delvis | Ja | Hög |
| Decision quality / governance | Generate -> review -> score -> revise, 3 planalternativ, candidate selection | Mätning av om review faktiskt förbättrar framtida utfall | Ja | Nej | Medel |

## Nya coachlager

Följande lager är nu implementerade i `training_plan/` och används i prompt, review-kontext, terminalutskrift och veckorapport:

- `Prestationsforecast`: prognos för tröskel, durability och race readiness.
- `Benchmark-system`: prioriterade checkpoints som FTP-test, durability-check och fueling benchmark.
- `Block learning`: explicit sammanfattning av vad som fungerat, vad som inte gjort det och vilken bias nästa block ska ha.
- `Kapacitetskarta`: score per förmåga, med starkaste och svagaste områden.
- `Race readiness score`: sammanvägt mått för hur redo atleten är mot målet.
- `Minimum effective dose`: lägsta effektiva struktur när återhämtning, motivation eller compliance är skör.
- `Individualisering`: historiska preferenser, svaga fönster och responsstil används aktivt.
- `Nutrition readiness`: score för hur redo fueling och race-nutrition är.
- `Friktionsscore`: bedömning av hur svår planen är att genomföra i vardagen.
- `Säsongsplan`: 4-16 veckors blockkarta med fokus, milstolpar och benchmark-punkter.

### `training_plan/integrations`
- Ansvar: kopplingar mot externa system.
- Innehåller: [services.py](./training_plan/integrations/services.py).
- Exempel på kod här: `fetch_activities`, `fetch_wellness`, `fetch_weather`, `save_workout`, `save_event`, rapport- och notesparning till intervals.icu.
- Lägg ny kod här när den pratar med API:er, cache eller externa tjänster.

## Rotfiler

- [main.py](./main.py): tunn entrypoint till den nya strukturen.
- [server.py](./server.py): webhook-server som kan trigga generatorn.
- [training_plan_generator.py](./training_plan_generator.py): originalversionen i en fil.
- [.github/workflows/morning.yml](./.github/workflows/morning.yml): schemalagd körning i GitHub Actions.
- [.coach_state.json](./.coach_state.json): sparat state för planeringslogik.
- [.weather_cache.json](./.weather_cache.json): lokal vädercache.
- [.env](./.env): lokala miljövariabler.

## Hur originalfilen delades upp

Originalet i [training_plan_generator.py](./training_plan_generator.py) har delats upp efter ansvar:

- CLI och uppstart flyttades till [main.py](./main.py), [training_plan/app/main.py](./training_plan/app/main.py) och [training_plan/core/cli.py](./training_plan/core/cli.py).
- Gemensamma modeller, konstanter och konfiguration lades i [`training_plan/core/`](./training_plan/core).
- Träningsbibliotek, planering, analys, AI och post-process lades i [`training_plan/engine/`](./training_plan/engine).
- API-anrop, väderhämtning och sparning mot intervals.icu lades i [`training_plan/integrations/`](./training_plan/integrations).

Målet med uppdelningen är att göra samma kod lättare att hitta och underhålla, utan att originalfilen tas bort.

## Riktlinjer för nya filer

- Lägg ny start- eller orchestreringskod i `training_plan/app/`.
- Lägg delade modeller, konstanter och config i `training_plan/core/`.
- Lägg ren domän- och planeringslogik i `training_plan/engine/`.
- Lägg API-klienter, webhook-kod och annan extern IO i `training_plan/integrations/`.
- Låt [training_plan_generator.py](./training_plan_generator.py) vara referens/original om syftet är att bevara den gamla en-filsversionen.

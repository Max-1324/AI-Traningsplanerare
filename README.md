# Projektstruktur

Det här projektet körs via [main.py](./main.py), som är en tunn entrypoint till paketet [`training_plan/`](./training_plan). Koden är uppdelad efter ansvar så att större flöden kan hittas via kompatibilitetsfiler, medan implementationen ligger i mindre moduler.

## Trädstruktur

```text
AI-Traningsplanerare/
├── main.py
├── server.py
├── requirements.txt
├── tests/
│   ├── test_coaching_logic.py
│   ├── test_postprocess_rules.py
│   ├── test_prompt_builders.py
│   ├── test_trust_pipeline.py
│   └── test_validation_behavioral.py
└── training_plan/
    ├── app/
    │   └── main.py
    ├── core/
    │   ├── catalogs.py
    │   ├── cli.py
    │   ├── common.py
    │   ├── config.py
    │   └── models.py
    ├── engine/
    │   ├── ai/
    │   │   ├── __init__.py
    │   │   ├── client.py
    │   │   ├── display.py
    │   │   └── parsing.py
    │   ├── prompt/
    │   │   ├── __init__.py
    │   │   ├── inputs.py
    │   │   ├── sections.py
    │   │   └── generation.py
    │   ├── prompt_builders.py
    │   ├── pipeline/
    │   │   ├── __init__.py
    │   │   ├── core.py
    │   │   ├── prompts.py
    │   │   ├── reviews.py
    │   │   ├── scoring.py
    │   │   ├── candidates.py
    │   │   └── outcomes.py
    │   ├── postprocess/
    │   │   ├── __init__.py
    │   │   ├── safety.py
    │   │   ├── injury.py
    │   │   ├── recovery.py
    │   │   ├── load.py
    │   │   └── nutrition.py
    │   ├── validation/
    │   │   ├── __init__.py
    │   │   ├── structure.py
    │   │   ├── rules.py
    │   │   └── adapters.py
    │   ├── analysis/
    │   │   ├── __init__.py
    │   │   ├── data.py
    │   │   ├── load.py
    │   │   ├── strategy.py
    │   │   └── athlete.py
    │   ├── insights/
    │   │   ├── __init__.py
    │   │   ├── common.py
    │   │   ├── profiles.py
    │   │   ├── execution.py
    │   │   └── forecast.py
    │   ├── planning/
    │   │   ├── __init__.py
    │   │   ├── state.py
    │   │   ├── metrics.py
    │   │   ├── learning.py
    │   │   └── workouts.py
    │   ├── libraries.py
    │   ├── skeleton.py
    │   └── utils.py
    └── integrations/
        ├── services.py
        ├── intervals_client.py
        ├── intervals_events.py
        ├── notes.py
        └── weather.py
```

## Huvudmappar

### `training_plan/app`
- Ansvar: applikationens startflöde.
- [main.py](./training_plan/app/main.py) hämtar data, kör analyser, bygger prompt, kör AI-pipeline, validerar, skriver ut och sparar planen.
- Importerna är explicita för att göra flödet lättare att läsa och följa.

### `training_plan/core`
- Ansvar: gemensam grund som resten av koden bygger på.
- [catalogs.py](./training_plan/core/catalogs.py): sportkatalog, zoner, minimitider och uppslagstabeller.
- [cli.py](./training_plan/core/cli.py): argumentparser.
- [common.py](./training_plan/core/common.py): logging, delade imports och gemensamma resurser.
- [config.py](./training_plan/core/config.py): miljövariabler och konfiguration.
- [models.py](./training_plan/core/models.py): Pydantic-modeller som `PlanDay`, `AIPlan`, `PlanReview` och `PlanDecisionTrace`.

### `training_plan/engine`
- Ansvar: planeringsmotorn, analysen och alla regler som ändrar eller bedömer en träningsplan.
- [analysis/](./training_plan/engine/analysis): analysfacad och analysmoduler.
  - `data.py`: datakvalitet, motivation, HRV och readiness.
  - `load.py`: RPE/NP/IF, ACWR, sportvolym, sportbudget, ramp och TSS-budget.
  - `strategy.py`: development needs, block objective, race week, RTP och taper.
  - `athlete.py`: zoner, nutrition-hjälpare, yesterday analysis och atletprofil.
- [planning/](./training_plan/engine/planning): planeringsfacad och planeringsmoduler.
  - `state.py`: state, failure memory, AI-taggar och mesocykler.
  - `metrics.py`: passklassificering, polarization, session quality, race demands, coach confidence och CTL-trajektoria.
  - `learning.py`: compliance, learned patterns och workout-library data.
  - `workouts.py`: prehab, progression, autoreglering och FTP-testlogik.
- [insights/](./training_plan/engine/insights): coach insight-facad och insight-lager.
  - `common.py`: små hjälpfunktioner för insight-modulerna.
  - `profiles.py`: capacity map, individualization och nutrition readiness.
  - `execution.py`: minimum effective dose, execution friction och training frequency target.
  - `forecast.py`: benchmark-system, block learning, performance forecast, race readiness och season plan.
- [libraries.py](./training_plan/engine/libraries.py): träningsbibliotek och constraints.
- [skeleton.py](./training_plan/engine/skeleton.py): veckoskelett för planeringsprompten.
- [utils.py](./training_plan/engine/utils.py): små delade hjälpfunktioner.

### `training_plan/integrations`
- Ansvar: externa system, API-anrop, väder och sparning.
- [intervals_client.py](./training_plan/integrations/intervals_client.py): Intervals.icu-hämtning.
- [intervals_events.py](./training_plan/integrations/intervals_events.py): spara, uppdatera och ta bort events/workouts.
- [notes.py](./training_plan/integrations/notes.py): morgon-wellness, daglig coachlogg och veckorapport.
- [weather.py](./training_plan/integrations/weather.py): väderhämtning från met.no.
- [services.py](./training_plan/integrations/services.py): kompatibilitetsfacad som exporterar de gamla funktionsnamnen.

## Kompatibilitetsfiler

Flera gamla importvägar är medvetet kvar som tunna facader. Det gör att appen, tester och externa scripts kan fortsätta importera samma namn medan implementationen är uppdelad i mindre filer.

- [engine/ai/](./training_plan/engine/ai): exporterar promptbygge, AI-klient, parsing och utskrift från:
  - `client.py`
  - `parsing.py`
  - `display.py`
- [engine/prompt/](./training_plan/engine/prompt): promptpaket för morgonfrågor, promptsektioner och hela planeringsprompten.
  - `inputs.py`
  - `sections.py`
  - `generation.py`
- [engine/prompt_builders.py](./training_plan/engine/prompt_builders.py): promptfacad för morgonfrågor, promptsektioner och hela planeringsprompten.
- [engine/analysis/](./training_plan/engine/analysis): analysfacad för data-, load-, strategy- och athlete-moduler.
- [engine/planning/](./training_plan/engine/planning): planeringsfacad för state, metrics, learning och workout progression.
- [engine/insights/](./training_plan/engine/insights): insight-facad för profiles, execution och forecast-moduler.
- [engine/pipeline/](./training_plan/engine/pipeline): exporterar pipeline-API:t och kör `run_plan_pipeline`, medan hjälplogik ligger i:
  - `core.py`
  - `prompts.py`
  - `reviews.py`
  - `scoring.py`
  - `candidates.py`
  - `outcomes.py`
- [engine/postprocess/](./training_plan/engine/postprocess): exporterar `post_process`, TSS-hjälpare och `enforce_*`-regler från:
  - `safety.py`
  - `injury.py`
  - `recovery.py`
  - `load.py`
  - `nutrition.py`
- [engine/validation/](./training_plan/engine/validation): exporterar validering och reparation från:
  - `structure.py`
  - `rules.py`
  - `adapters.py`
- [integrations/services.py](./training_plan/integrations/services.py): exporterar integrationsfunktioner från de mindre integrationsmodulerna.

## Planeringspipeline

Den centrala kvalitetsgaten är:

`generate_plan() -> review_plan() -> compute_scores_from_review() -> accept/revise/reject`

- `generate_plan()`: skapar kandidatplaner från träningsdata, mål, constraints och race demands.
- `review_plan()`: kör en separat skeptisk granskning av mål, nyckelpass, effektivitet, risk, individualisering och race specificity.
- `compute_scores_from_review()`: räknar deterministiska betyg från review-resultatet och kontextsignaler.
- `decide_plan()`: avgör om planen accepteras, revideras eller förkastas.
- `validate_postprocessed_plan()`: stoppar planer som bryter deterministiska struktur- och säkerhetsregler.
- `record_plan_decision()` och `update_plan_outcome_tracking()`: sparar historik så framtida planer kan kalibreras mot verkliga utfall.

## Coachlager

Följande lager används i prompt, review-kontext, terminalutskrift och rapporter:

- `Prestationsforecast`: prognos för tröskel, durability och race readiness.
- `Benchmark-system`: checkpoints som FTP-test, durability-check och fueling benchmark.
- `Block learning`: vad som fungerat, vad som inte fungerat och nästa block-bias.
- `Kapacitetskarta`: score per förmåga, med starkaste och svagaste områden.
- `Race readiness score`: sammanvägt mått för redohet mot mål.
- `Minimum effective dose`: lägsta effektiva struktur när återhämtning, motivation eller compliance är skör.
- `Individualisering`: historiska preferenser, svaga fönster och responsstil.
- `Nutrition readiness`: redohet för fueling och race-nutrition.
- `Friktionsscore`: hur svår planen är att genomföra i vardagen.
- `Säsongsplan`: 4-16 veckors blockkarta med fokus, milstolpar och benchmark-punkter.

## Tester

Kör den nuvarande testsviten med:

```bash
python -m unittest discover -s tests -p 'test_*.py' -v
```

`pytest` finns i [requirements.txt](./requirements.txt), men om det inte är installerat i aktuell venv fungerar `unittest`-kommandot ovan utan extra beroenden.

Använd gärna riktade testkörningar efter mindre ändringar:

```bash
python -m unittest tests.test_prompt_builders -v
python -m unittest tests.test_postprocess_rules -v
python -m unittest tests.test_validation_behavioral tests.test_trust_pipeline -v
```

## Riktlinjer För Nya Filer

- Lägg start- och orkestreringskod i `training_plan/app/`.
- Lägg delade modeller, konstanter och konfiguration i `training_plan/core/`.
- Lägg ren domänlogik, planeringslogik, post-processing, validering och AI-pipeline i `training_plan/engine/`.
- Lägg API-klienter, väderhämtning och extern IO i `training_plan/integrations/`.
- Behåll kompatibilitetsfacaderna om du flyttar kod vidare, så gamla importvägar inte bryts i onödan.

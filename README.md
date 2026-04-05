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
    │   ├── libraries.py
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
  - [postprocess.py](./training_plan/engine/postprocess.py): tvingande regler som justerar planen efter AI-svaret.
  - [ai.py](./training_plan/engine/ai.py): promptbygge, AI-anrop, parsing och utskrift.
- Lägg ny kod här när den ändrar eller utvärderar träningsplanen.

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

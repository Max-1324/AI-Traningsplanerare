# Roadmap: vad som bör göras, sorterat efter ROI

Resultatet av en genomgång av hela kodbasen (oktober 2026). ROI = förväntad nytta / insats.
Status: ✅ åtgärdat · 🟡 delvis åtgärdat · ⬜ kvar.

Bakgrunden till metodvalen finns i [TRAINING_MODEL.md](TRAINING_MODEL.md), och strukturen i [ARCHITECTURE.md](ARCHITECTURE.md).

## Nivå 1: högst ROI (liten insats, stor effekt)

| # | Status | Vad | Var | Varför / åtgärd |
|---|---|---|---|---|
| 1 | ✅ | **Mesocykeln avancerade aldrig vid daglig körning** | [`engine/planning/state.py`](../training_plan/engine/planning/state.py) `determine_mesocycle` | Villkoret "måndag *och* senaste körning äldre än igår" var alltid falskt när programmet körs varje dag. Mesocykeln fastnade i vecka 1, eller i deload (vecka 4) för alltid efter en tvingad deload. Nu räknas antalet passerade ISO-veckor. Test: `tests/test_regressions.py` |
| 2 | ✅ | **Uppdateringsläget avgörs före AI-pipelinen** | [`app/main.py`](../training_plan/app/main.py), `engine/ai/display.py` `resolve_update_mode` | Hela pipelinen (upp till cirka 25 LLM-anrop) kördes förut och kastades sedan om läget blev "none". Nu avslutas körningen direkt, utom vid `--dry-run` och på måndagar (veckorapporten behöver AI-feedback). Viktigt för webhook-servern, som kan trigga många gånger per dag |
| 3 | ⬜ | **Tävlingsfiltret `category == "RACE"`** | [`integrations/intervals_client.py:62`](../training_plan/integrations/intervals_client.py) | intervals.icu använder (enligt min kännedom) `RACE_A`, `RACE_B` och `RACE_C`. Stämmer det hittas **inga** tävlingar, och då fungerar varken taper, A-tävling, fas eller CTL-trajektoria. Verifiera först (se nedan). Gör sedan filtret tolerant (`category.startswith("RACE")`) och läs prioriteten från kategorin i stället för namnprefixet `b:`/`c:` |
| 4 | ✅ | **Saknad HRV raderade hela wellness-raden** | [`engine/analysis/data.py`](../training_plan/engine/analysis/data.py) `validate_data_quality`, `clean_wellness` | Sömn, vilopuls och CTL försvann för alla dagar utan HRV, och gårdagens rad räknades som "idag" i HRV-analysen. Nu nollställs bara det ogiltiga fältet |
| 5 | 🟡 | **AI-klientens robusthet** | [`engine/ai/client.py`](../training_plan/engine/ai/client.py) | ✅ Ingen krasch längre när `GEMINI_MODELS`/`GROQ_MODELS` saknas. ✅ `max_tokens` för Anthropic (6000) och `num_predict` för Ollama (4096) trunkerade troligen en 29-dagarsplan och styrs nu av env. ✅ Fallback till Mistral tappar inte längre temperaturen. ⬜ Kvar: använd inbyggd *structured output* (Gemini `response_schema`, OpenAI `json_schema`, Anthropic tool use) i stället för fri JSON plus reparation, och lägg till retry för Anthropic/OpenAI |
| 6 | ⬜ | **Periodisering över horisonten** | [`engine/analysis/load.py`](../training_plan/engine/analysis/load.py) `tss_budget`, [`engine/skeleton.py`](../training_plan/engine/skeleton.py), [`engine/postprocess/load.py`](../training_plan/engine/postprocess/load.py) `enforce_tss` | Aktuell veckas mesocykelfaktor gäller alla 29 dagar, och budgeten delas jämnt per vecka. Är det deloadvecka idag blir hela horisonten deload. Annars finns ingen deload alls i horisonten, samtidigt som prompten säger "vecka 4 = deload", så AI och regler motsäger varandra. **Åtgärd:** en funktion som ger `WeekTarget(start, load_factor, tss_min, tss_max, max_intensity)` per vecka. Skelettet, `enforce_tss` och prompten använder den |
| 7 | ⬜ | **Drift och state** | [`server.py`](../server.py), [`.github/workflows/morning.yml`](../.github/workflows/morning.yml) | Docstringen säger `webhook_server:app`, men filen heter `server.py` (gunicorn-kommandot blir `server:app`). Timeouten på 300 s räcker inte för pipelinen. Render har ett flyktigt filsystem, så `.coach_state.json` försvinner och glider isär från GitHub Actions-cachens kopia. Välj **en** körmiljö och **en** state-lagring (t.ex. en privat gist, en liten bucket eller ett NOTE-event i intervals.icu) |

### Verifiera tävlingskategorin (#3)
```bash
curl -s -u "API_KEY:$INTERVALS_API_KEY" \
  "https://intervals.icu/api/v1/athlete/$INTERVALS_ATHLETE_ID/events?oldest=2026-01-01&newest=2027-12-31" \
  | python -m json.tool | grep '"category"' | sort | uniq -c
```
Visar utskriften `RACE_A`/`RACE_B`/`RACE_C` men inte `RACE` är punkt 3 en riktig bugg.

## Nivå 2: medel-ROI

| # | Status | Vad | Var |
|---|---|---|---|
| 8 | ⬜ | **Färre LLM-anrop.** `PLAN_CANDIDATE_COUNT=1`, högst 2 rundor, ingen parvis jämförelse. Den deterministiska valideringen är den riktiga spärren. Logga antal anrop och tokens per körning | `app/main.py`, `engine/pipeline/` |
| 9 | ⬜ | **Kortare detaljhorisont.** Konkreta pass 7–10 dagar framåt, resten som veckomål. Läget `extend` sparar idag bara de nya datumen från en helt ny plan, så kalendern blandar pass från olika planer | `app/main.py`, `engine/ai/display.py` `plan_update_mode` |
| 10 | ⬜ | **En sanning för TSS.** Räkna TSS per steg (Σ timmar × IF² × 100) i stället för med linjärt vägd IF, som underskattar intervaller. Det finns idag tre olika zon→IF-tabeller: `core/catalogs.py` `ZONE_INTENSITY`, `postprocess/load.py` `ZONE_NP_RATIO` (Z1 0,55 mot 0,50, Z5 1,05 mot 1,15) och promptens fusklapp i `prompt/generation.py` | `engine/postprocess/load.py` `estimate_tss_coggan` |
| 11 | ⬜ | **HRV enligt ln(rMSSD) och SWC** (se TRAINING_MODEL.md). `enforce_hrv` gäller listindex 0–1 i stället för datumen idag och imorgon, vilket blir fel vid dubbelpass eller låsta dagar | `engine/analysis/data.py` `calculate_hrv`, `engine/postprocess/recovery.py` `enforce_hrv` |
| 12 | ⬜ | **Sluta jaga TSS.** Låt målet vara ett intervall (cirka 85–105 %) och fyll aldrig vilodagar med "TSS repair"-pass | `engine/postprocess/load.py` `repair_low_tss`, `engine/pipeline/candidates.py` `_apply_tss_gap_revision` |
| 13 | ⬜ | **Konfigurerbar ramp** (standard +3–5 CTL/vecka) och en rimligare standard för `TARGET_CTL` än 85 | `engine/analysis/load.py` `choose_target_ramp`, `core/config.py` |
| 14 | ⬜ | **ACWR blir information i stället för veto.** Lägg till veckoprogression per sport (löpning max cirka +10 %/vecka) | `engine/postprocess/recovery.py` `enforce_per_sport_acwr_veto` |
| 15 | ⬜ | **Idempotent synk mot intervals.icu.** Idag raderas och återskapas event med ett HTTP-anrop per pass, och AI-pass känns igen på en textsträng i beskrivningen (går sönder om du redigerar passet). Använd bulk-anrop, `tags` och, om API:t stöder det, upsert med `external_id` (verifiera). `fetch_fitness` och `fetch_wellness` anropar dessutom samma endpoint två gånger | `integrations/intervals_events.py`, `integrations/intervals_client.py` |

## Nivå 3: lägre ROI / långsiktigt

| # | Status | Vad |
|---|---|---|
| 16 | ⬜ | **Kodhygien.** 55 `import *`, kompatibilitetsfasader (`engine/ai`, `engine/prompt_builders.py`, `integrations/services.py`), globalerna `common.args` och `os.environ["_USED_MODEL"]`, och en `main()` på cirka 820 rader. Byt till explicita imports, ett `AthleteSnapshot`-objekt och dela upp main i steg |
| 17 | 🟡 | **Tester och CI.** Regressionstester för buggarna ovan finns nu. Kvar: ett golden-test (fixture → plan) samt ruff och pytest i en GitHub Actions-workflow som körs på PR |
| 18 | ⬜ | **Konfiguration.** En typad `Settings` (pydantic-settings) på ett ställe plus `.env.example`. Idag läses cirka 60 miljövariabler på olika ställen |
| 19 | ⬜ | **Rensa insiktslagret.** Elva "planner insights" (kapacitetskarta, prognos, friktion, säsongsplan …) med handsatta vikter matar mest prompttext. Mät vilka som faktiskt ändrar beslut och ta bort resten |
| 20 | ⬜ | **Använd intervals.icu-native data.** eFTP, effektkurva och `SICK`/`INJURED`-event i stället för namnparsning (`b:`, `[Ride]`) och fritext |
| 21 | ⬜ | **Utvärdering och backtesting.** Spela upp historiken: planerat mot genomfört, CTL-kurva och missade nyckelpass. Först då går det att veta om en ändring är en förbättring |

Två mindre observationer: `injury.py` och `recovery.py` definierar samma tre konstanter var för sig, och
intervals.icu stöder enligt min kännedom `Nx`-repetitioner i passtext, tvärtemot kommentaren i
`intervals_events.py` (värt att testa, eftersom det ger renare pass i klockan).

## Föreslagen ordning (varje steg går att leverera för sig)

1. **Buggfixar och CI**: #1, #2, #4 och #5 är klara. Kvar: verifiera #3, åtgärda #7 och lägg till CI (#17).
2. **Veckomål per vecka** (#6, #13): ny `planning/periodization.py` som skelett, TSS-tak och prompt använder.
3. **Billigare och lugnare körningar** (#8, #9, #12): kortare detaljhorisont, färre anrop och ingen TSS-jakt.
4. **Deterministiskt passval** där AI:n bara berikar (se TRAINING_MODEL.md).
5. **Flytt till ny struktur** (se ARCHITECTURE.md) och borttagning av fasader (#16, #18). Görs nu på mindre kod.
6. **Rensning och mätning** (#19, #21).

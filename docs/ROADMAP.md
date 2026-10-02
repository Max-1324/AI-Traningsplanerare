# Roadmap: vad som bör göras, sorterat efter ROI

Resultatet av en genomgång av hela kodbasen (oktober 2026). ROI = förväntad nytta / insats.
Status: ✅ åtgärdat · 🟡 delvis åtgärdat · ⬜ kvar.

Bakgrunden till metodvalen finns i [TRAINING_MODEL.md](TRAINING_MODEL.md), och strukturen i [ARCHITECTURE.md](ARCHITECTURE.md).

## Nivå 1: högst ROI (liten insats, stor effekt)

| # | Status | Vad | Var | Varför / åtgärd |
|---|---|---|---|---|
| 1 | ✅ | **Mesocykeln avancerade aldrig vid daglig körning** | [`engine/planning/state.py`](../training_plan/engine/planning/state.py) `determine_mesocycle` | Villkoret "måndag *och* senaste körning äldre än igår" var alltid falskt när programmet körs varje dag. Mesocykeln fastnade i vecka 1, eller i deload (vecka 4) för alltid efter en tvingad deload. Nu räknas antalet passerade ISO-veckor. Test: `tests/test_regressions.py` |
| 2 | ✅ | **Uppdateringsläget avgörs före AI-pipelinen** | [`app/main.py`](../training_plan/app/main.py), `engine/ai/display.py` `resolve_update_mode` | Hela pipelinen (upp till cirka 25 LLM-anrop) kördes förut och kastades sedan om läget blev "none". Nu avslutas körningen direkt, utom vid `--dry-run` och på måndagar (veckorapporten behöver AI-feedback). Viktigt för webhook-servern, som kan trigga många gånger per dag |
| 3 | ✅ | **Tävlingsfiltret `category == "RACE"`** | [`engine/utils.py`](../training_plan/engine/utils.py) `is_race_event`, `race_priority` | intervals.icu använder (enligt min kännedom) `RACE_A`, `RACE_B` och `RACE_C`. Med det gamla filtret hittades då **inga** tävlingar. Filtret accepterar nu både `RACE` och `RACE_A/B/C`, och prioriteten läses från kategorin, med namnprefixet `b:`/`c:` som reserv. Det fungerar oavsett vilket format kontot använder. Kommandot nedan visar vilket det är |
| 4 | ✅ | **Saknad HRV raderade hela wellness-raden** | [`engine/analysis/data.py`](../training_plan/engine/analysis/data.py) `validate_data_quality`, `clean_wellness` | Sömn, vilopuls och CTL försvann för alla dagar utan HRV, och gårdagens rad räknades som "idag" i HRV-analysen. Nu nollställs bara det ogiltiga fältet |
| 5 | 🟡 | **AI-klientens robusthet** | [`engine/ai/client.py`](../training_plan/engine/ai/client.py) | ✅ Ingen krasch längre när `GEMINI_MODELS`/`GROQ_MODELS` saknas. ✅ `max_tokens` för Anthropic (6000) och `num_predict` för Ollama (4096) trunkerade troligen en 29-dagarsplan och styrs nu av env. ✅ Fallback till Mistral tappar inte längre temperaturen. ⬜ Kvar: använd inbyggd *structured output* (Gemini `response_schema`, OpenAI `json_schema`, Anthropic tool use) i stället för fri JSON plus reparation, och lägg till retry för Anthropic/OpenAI |
| 6 | ✅ | **Periodisering över horisonten** | [`engine/periodization.py`](../training_plan/engine/periodization.py), [`engine/skeleton.py`](../training_plan/engine/skeleton.py), [`engine/planner.py`](../training_plan/engine/planner.py) | Förut gällde aktuell veckas mesocykelfaktor alla 29 dagar: under en deloadvecka blev hela horisonten deload, och annars fanns ingen deload alls. Nu får varje kalendervecka ett eget `WeekTarget` (mesocykelposition, deload, taper, tävling och ramp), och skelettet och planeraren följer det. Legacy-motorn använder fortfarande `tss_budget` |
| 7 | 🟡 | **Drift och state** | [`server.py`](../server.py), [`.github/workflows/morning.yml`](../.github/workflows/morning.yml) | ✅ Docstringen säger nu `gunicorn server:app`. ✅ Timeouten styrs av `GENERATOR_TIMEOUT_SEC` (standard 900 s, och den nya motorn behöver bara ett AI-anrop). ⬜ Kvar: Render har ett flyktigt filsystem, så `.coach_state.json` försvinner och glider isär från GitHub Actions-cachens kopia. Välj **en** körmiljö och **en** state-lagring (t.ex. en privat gist, en liten bucket eller ett NOTE-event i intervals.icu) |

### Se vilket tävlingsformat ditt konto använder (#3)
```bash
curl -s -u "API_KEY:$INTERVALS_API_KEY" \
  "https://intervals.icu/api/v1/athlete/$INTERVALS_ATHLETE_ID/events?oldest=2026-01-01&newest=2027-12-31" \
  | python -m json.tool | grep '"category"' | sort | uniq -c
```
Visar utskriften `RACE_A`/`RACE_B`/`RACE_C` hittade den gamla koden inga tävlingar alls. Den nya koden hanterar båda formaten.

## Nivå 2: medel-ROI

| # | Status | Vad | Var |
|---|---|---|---|
| 8 | ✅ | **Färre LLM-anrop.** Den deterministiska motorn gör **ett** berikningsanrop per körning, och bara för pass som ska sparas (plus skadeklassning när det finns en skadenotering). `--no-ai` kör helt utan AI. Legacy-motorn är oförändrad | `engine/pipeline/enrich.py`, `app/deterministic.py` |
| 9 | ✅ | **Kortare detaljhorisont.** 10 dagar med konkreta pass (`DETAIL_HORIZON_DAYS`), vecka 2–4 finns som veckomål. Planeraren är deterministisk, så dagar som läggs till i `extend`-läget hänger ihop med resten av veckan. Full omplanering varje måndag och vid stora händelser | `app/main.py`, `core/cli.py` |
| 10 | 🟡 | **En sanning för TSS.** ✅ TSS räknas per steg (Σ timmar × IF² × 100), och zonnamn normaliseras först. ⬜ Kvar: `core/catalogs.py` `ZONE_INTENSITY` (klassning) och `postprocess/load.py` `ZONE_NP_RATIO` (TSS) har olika värden, och legacy-promptens fusklapp är en tredje källa | `engine/postprocess/load.py` `estimate_tss_coggan` |
| 11 | ✅ | **HRV enligt ln(rMSSD) och SWC.** 7-dagarssnittet jämförs med en egen baslinje (dag 8–60) ± SWC = 0,5 × SD. Med under 14 dagars baslinje används de gamla procentgränserna. `enforce_hrv` matchar nu idag/imorgon på datum i stället för listposition | `engine/analysis/data.py` `calculate_hrv`, `engine/postprocess/recovery.py` `enforce_hrv` |
| 12 | ✅ | **Sluta jaga TSS.** Den deterministiska motorn fyller aldrig vilodagar. Volymen kommer från skelettet (långpass och uthållighetsdagar) med tak per dag, och ikappträning begränsas av `CATCH_UP_CAP`. Legacy-motorns `repair_low_tss` finns kvar men används bara där | `engine/planner.py` |
| 13 | 🟡 | **Konfigurerbar ramp.** ✅ `RAMP_CTL_PER_WEEK` (standard 4,0) och `RAMP_CTL_MAX` (6,0); mot en A-tävling används den ramp som krävs, inom taket. Rampen planeras aldrig förbi `TARGET_CTL`. ⬜ Standardvärdet för `TARGET_CTL` är fortfarande 85, och legacy-motorns `choose_target_ramp` är oförändrad | `engine/periodization.py` |
| 14 | 🟡 | **ACWR som information.** Den deterministiska planeraren planerar inte sporter med ACWR i DANGER (skadebenägna sporter begränsas av sportbudgeten, max cirka +10 %/vecka), så vetot behöver aldrig slå till. ⬜ Legacy-motorn har kvar vetot | `engine/planner.py`, `engine/postprocess/recovery.py` |
| 15 | 🟡 | **Idempotent synk mot intervals.icu.** ✅ Wellness hämtas en gång och fitness räknas ur samma svar. ✅ AI-pass raderas med bulk-anrop, 50 åt gången. ⬜ Kvar: `tags` eller upsert med `external_id` (verifiera mot API:t) i stället för att känna igen AI-pass på en textsträng i beskrivningen | `integrations/intervals_events.py`, `integrations/intervals_client.py` |

## Nivå 3: lägre ROI / långsiktigt

| # | Status | Vad |
|---|---|---|
| 16 | ⬜ | **Kodhygien.** 55 `import *`, kompatibilitetsfasader (`engine/ai`, `engine/prompt_builders.py`, `integrations/services.py`), globalerna `common.args` och `os.environ["_USED_MODEL"]`, och en `main()` på cirka 820 rader. Byt till explicita imports, ett `AthleteSnapshot`-objekt och dela upp main i steg |
| 17 | ✅ | **Tester och CI.** [`tests.yml`](../.github/workflows/tests.yml) kör testsviten vid push och pull request. Scenario- och egenskapstester (`tests/test_deterministic_planner.py`) kör planeraren i flera hundra situationer, och planen ska alltid klara reglerna och valideringen. Ruff är inte tillagt |
| 18 | 🟡 | **Konfiguration.** ✅ [`.env.example`](../.env.example) med alla variabler, grupperade. ⬜ Kvar: en typad `Settings` (pydantic-settings) i stället för cirka 90 `os.getenv` på olika ställen |
| 19 | ⬜ | **Rensa insiktslagret.** Elva "planner insights" (kapacitetskarta, prognos, friktion, säsongsplan …) med handsatta vikter matar mest prompttext. Mät vilka som faktiskt ändrar beslut och ta bort resten |
| 20 | ⬜ | **Använd intervals.icu-native data.** eFTP, effektkurva och `SICK`/`INJURED`-event i stället för namnparsning (`b:`, `[Ride]`) och fritext |
| 21 | ⬜ | **Utvärdering och backtesting.** Spela upp historiken: planerat mot genomfört, CTL-kurva och missade nyckelpass. Först då går det att veta om en ändring är en förbättring |

Två mindre observationer: `injury.py` och `recovery.py` definierar samma tre konstanter var för sig, och
intervals.icu stöder enligt min kännedom `Nx`-repetitioner i passtext, tvärtemot kommentaren i
`intervals_events.py` (värt att testa, eftersom det ger renare pass i klockan).

## Föreslagen ordning (varje steg går att leverera för sig)

1. ✅ **Buggfixar och CI**: #1–#4, #17 och delar av #5 och #7.
2. ✅ **Veckomål per vecka** (#6, #13).
3. ✅ **Billigare och lugnare körningar** (#8, #9, #12).
4. ✅ **Deterministiskt passval** där AI:n bara berikar (se TRAINING_MODEL.md).
5. ⬜ **En plats för state** (#7) och **structured output** för AI-svaren (#5).
6. ⬜ **Flytt till ny struktur** (se ARCHITECTURE.md) och borttagning av fasader och legacy-motorn (#16, #18),
   när den nya motorn har fungerat ett tag.
7. ⬜ **Rensning och mätning** (#19, #21).

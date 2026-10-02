# Träningsmodell och metodval

Det här dokumentet beskriver *hur* planeraren bör tänka: vilka delar som är vetenskap (deterministisk kod)
och vilka som är omdöme och språk (AI). Det förklarar också varför arbetssättet rekommenderas framför
alternativen.

## Grundidén

> AI:n gör passen och lägger dem på rätt dagar, medan training load, fitness och fatigue från intervals.icu
> håller belastningen på rätt nivå.

**Uppdelningen är rätt i grunden.** Vetenskapen ska styra *hur mycket* och *hur hårt*, och AI:n ska stå för det
som kräver omdöme och språk. I nuvarande kod är rollerna dock i praktiken omvända. AI:n planerar alla 29 dagar,
inklusive TSS-räkning, periodisering och placering. Sedan rättar ett 20-tal deterministiska regler planen i
efterhand, och en AI-granskare och en AI-domare bedömer resultatet. Det ger:

- många dyra och långsamma LLM-anrop (upp till cirka 20–25 per körning) som ger olika svar varje gång;
- regler som slåss mot AI:n. Git-historiken är full av "fix RTP", "force …" och "veto …";
- **TSS-jakt**: `repair_low_tss` gör vilodagar till 45 min Z2 för att nå en siffra, och prompten säger att under
  90 % av budgeten är "för lite". Det driver skräpvolym.

## Rekommendation: deterministisk kärna och AI i kanten

| Lager | Vem | Ansvar |
|---|---|---|
| **Makro** | Kod | TSS-mål **per vecka** i horisonten: CTL-mål, 3:1-mesocykel där varje vecka har sin egen faktor och deload ingår, ramp-tak och taper |
| **Mikro** | Kod | Veckoskelett: nyckelpass, långpass, lätta dagar och vila. Tar hänsyn till låsta dagar, constraints, hard-easy och intensitetsfördelning |
| **Passval** | Kod + bibliotek | Välj pass ur passbiblioteket på rätt progressionsnivå och skala längden mot dagens TSS-intervall |
| **Berikning** | AI (1–2 anrop) | Välj mellan *godkända* alternativ när fritext spelar roll (anteckningar, skada, preferenser, väderavvägningar). Skriver passbeskrivningar, coachfeedback och veckorapport |
| **Autoreglering** | Kod | HRV och readiness justerar bara **idag och imorgon** (kör, modifiera eller vila) |

Viktiga principer:

1. **Planen ska vara giltig utan AI.** AI-svaret valideras mot samma regler. Om AI:n misslyckas används den
   deterministiska planen. Därmed försvinner de flesta veto-, reparations- och revisionsrundorna.
2. **Kort detaljhorisont.** 7–10 dagar planeras med konkreta pass, och vecka 2–4 finns bara som veckomål och
   skelett. Det betyder mindre kalenderbrus, färre tokens och inga planer som blandas ihop.
3. **Stabil vecka, flexibel dag.** Veckan planeras om en gång i veckan, eller vid stora händelser (missat
   nyckelpass, sjukdom, ny tävling). Dagligen justeras bara de närmaste dagarna.
4. **TSS är ett intervall, inte ett mål att jaga.** Volym kommer från skelettet (långpass och uthållighetsdagar),
   inte från att fylla vilodagar.

### Status: implementerat
Modellen ovan är nu standardmotorn (`PLANNER_ENGINE=deterministic`):

| Lager | Kod |
|---|---|
| Makro | `engine/periodization.py`: `build_week_targets()` |
| Mikro | `engine/skeleton.py`: `build_week_skeleton(week_targets=…)` |
| Passval | `engine/planner.py`: `build_deterministic_plan()` |
| Berikning | `engine/pipeline/enrich.py`: ett AI-anrop med begränsade val och reservkedja |
| Autoreglering | Restriktion av idag/imorgon i `app/deterministic.py` + `apply_safety_rules()` som skyddsnät |

HRV-analysen använder ln(rMSSD) mot egen baslinje ± SWC (se nedan). ACWR används bara som information:
planeraren planerar inte in sporter i riskzonen. Den gamla AI-först-pipelinen finns kvar som `--engine legacy`.

## Vetenskapliga kommentarer

### CTL/ATL/TSB (Banister / Performance Manager Chart)
Det är ett bra **räcke** för makrobelastning: ramp, trötthet och form inför tävling. Det ska dock inte vara
målfunktionen, av flera skäl:
- Modellen beskriver belastning, inte prestation. Samma TSS kan ge helt olika anpassning.
- TSS är inte jämförbart mellan sporter (hrTSS vid löpning mot effektbaserad TSS på cykel).
- Den fångar varken intensitetsfördelning eller durability.

**Rekommendation:** CTL-mål och rampgräns styr veckomålen, och TSB-golv och HRV styr när det ska bromsas.
Legacy-motorns ramp på +5–7 CTL/vecka som "normalläge" (`choose_target_ramp`) är aggressiv för en motionär.
Den deterministiska motorn använder `RAMP_CTL_PER_WEEK` (standard **+4**, tak `RAMP_CTL_MAX` = 6) och
planerar aldrig förbi `TARGET_CTL`. Sänk `TARGET_CTL` om 85 är högt för dig.

### ACWR (acute:chronic workload ratio)
Den är vetenskapligt ifrågasatt. Kritiken gäller matematisk koppling mellan täljare och nämnare och svag
prediktiv förmåga för skador. Behåll den som *information* men inte som hårt veto. För skadebenägna sporter
(löpning) är en enkel **veckoprogression per sport** (till exempel max +10 % tid per vecka) både enklare och
bättre förankrad.

### HRV-styrd träning
Den har bra stöd: HRV-styrd planering ger minst lika bra, ofta bättre, anpassning än en förutbestämd plan.
Gör det enligt etablerad metodik:
- använd **ln(rMSSD)**, inte råa millisekunder;
- jämför ett **7-dagars rullande snitt** med en **60-dagars baslinje** (utan de senaste 7 dagarna);
- definiera "normalt" som baslinje ± **SWC** (smallest worthwhile change, cirka 0,5 × SD);
- följ även **CV** för ln(rMSSD) över 7 dagar, eftersom ökande variation är en tidig varningssignal.

`calculate_hrv` gör nu så (LOW under −2·SWC för 7-dagarssnittet). Tidigare användes råa %-trösklar mot en baslinje som innehöll de senaste dagarna, och de används fortfarande som reserv när historiken är kortare än 14 dagar.

### Intensitetsfördelning
Pyramidal fördelning (mest Z1–Z2, en del Z3, lite Z4+) eller polariserad (cirka 80/20) är väl underbyggd för
uthållighetsidrottare. Den bör vara en **begränsning i veckoskelettet** (antal nyckelpass och tid i zon), inte
bara prompttext. `polarization_analysis` mäter redan utfallet.

### Övrigt
- **Session-RPE** (RPE × minuter) ger jämförbar belastning för styrka och pass utan effektmätare.
  **Monotoni och strain** (veckomedel / SD respektive veckobelastning × monotoni) är billiga tillägg för att
  upptäcka för likformiga veckor.
- **eFTP och effektkurva** från intervals.icu kan ersätta täta FTP-tester, och testpass blir då verifikation
  snarare än nödvändighet.
- **Durability** (effektfall efter X kJ) är ofta den viktigaste begränsningen för långa motionslopp och mäts
  bäst med ett återkommande benchmarkpass.

## Alternativ som övervägts

| Alternativ | Bedömning |
|---|---|
| *Ren regelmotor* (à la TrainerRoad/Xert) | Förutsägbar och billig, men saknar AI:ns förmåga att förstå fritext och resonera kring preferenser. Hybriden ovan behåller den förmågan |
| *Anpassad impuls-respons-modell* (Banister med personligt skattade parametrar) | Kräver regelbundna prestationstester och blir brusig. Inte värt det för ett hobbyprojekt |
| *Matematisk optimering* (t.ex. OR-Tools CP-SAT) | Elegant men överkurs. En girig, mallbaserad schemaläggare räcker gott |
| *LLM-agent med verktyg* (AI anropar `get_load()`, `validate()` …) | Intressant men mindre förutsägbar och svårare att testa. Inte rekommenderat som kärna |

## Läsvärt
- Banister m.fl. (1975): *A systems model of training for athletic performance.*
- Allen & Coggan: *Training and Racing with a Power Meter* (TSS, CTL/ATL/TSB).
- Impellizzeri m.fl. (2020): *Acute:Chronic Workload Ratio: Conceptual Issues and Fundamental Pitfalls*, IJSPP.
- Lolli m.fl. (2019): *Mathematical coupling causes spurious correlation within the conventional ACWR*, BJSM.
- Kiviniemi m.fl. (2007): *Endurance training guided individually by daily heart rate variability measurements*, EJAP.
- Vesterinen m.fl. (2016): *Individual Endurance Training Prescription with Heart Rate Variability*, MSSE.
- Javaloyes m.fl. (2019): *Training Prescription Guided by Heart-Rate Variability in Cycling*, IJSPP.
- Plews m.fl. (2013): *Training adaptation and heart rate variability in elite endurance athletes*, Sports Medicine.
- Seiler (2010): *What is best practice for training intensity and duration distribution in endurance athletes?*, IJSPP.
- Foster (1998): *Monitoring training in athletes with reference to overtraining syndrome*, MSSE.
- Foster m.fl. (2001): *A new approach to monitoring exercise training*, JSCR (session-RPE).

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
prediktiv förmåga för skador (Impellizzeri m.fl. 2020). Den visas bara som *information*. För löpning används
i stället två enkla regler: en **veckobudget** som växer cirka 10 % från faktisk volym (stödet för just 10 % är
svagt, Buist m.fl. 2008) och ett **passtak** på 1,1 × längsta löppasset de senaste 30 dagarna. Det senare har
bäst stöd: enskilda pass som var mycket längre än det längsta senaste månaden ökade skaderisken, medan
förändring vecka för vecka inte gjorde det (Frandsen m.fl. 2025, BJSM).

### HRV-styrd träning
Den har måttligt stöd: HRV-styrd planering ger minst lika bra anpassning som en förutbestämd plan, med små
fördelar och färre som inte svarar på träningen (Granero-Gallegos m.fl. 2020, Düking m.fl. 2021).
Gör det enligt etablerad metodik:
- använd **ln(rMSSD)**, inte råa millisekunder;
- jämför ett **7-dagars rullande snitt** med en **60-dagars baslinje** (utan de senaste 7 dagarna);
- definiera "normalt" som baslinje ± **SWC** (smallest worthwhile change, cirka 0,5 × SD);
- följ även **CV** för ln(rMSSD) över 7 dagar, eftersom ökande variation är en tidig varningssignal.

`calculate_hrv` gör så. Under −1·SWC (SLIGHTLY_LOW, som i studiernas protokoll) blir idag och imorgon lugna utan
att passen kortas; under −2·SWC (LOW) blir de också kortare och veckan får bara ett hårt pass. CV räknas på ln. Tidigare användes råa %-trösklar mot en baslinje som innehöll de senaste dagarna, och de används fortfarande som reserv när historiken är kortare än 14 dagar.

**Saknade mätningar.** 7-dagarssnittet räknas på kalenderdagar och kräver minst 3 mätningar den senaste veckan.
Saknas de (ingen eller trasig klocka) antas HRV vara normal (`measured: False`) i stället för att gamla värden
används. Två veckor eller mer utan mätningar startar en ny baslinje, eftersom en ny klocka sällan mäter på samma
nivå. Readiness gör likadant: sömn räknas bara från senaste natten, vilopuls från senaste veckan, och det som inte
mäts räknas som normalt (70/100) och listas inte som begränsning.

### Intensitetsfördelning
Pyramidal fördelning (mest Z1–Z2, en del Z3, lite Z4+) och polariserad (cirka 80/20) har båda stöd. Polariserad
slår tröskeltung träning (Rosenblat m.fl. 2019), men har ingen säker fördel mot pyramidal (Oliveira m.fl. 2024).
Basfasens tempopass gör planen pyramidal, vilket är i linje med det. Den bör vara en **begränsning i veckoskelettet** (antal nyckelpass och tid i zon), inte
bara prompttext. `polarization_analysis` mäter redan utfallet.

### Hårda pass: hur många och vilka (`engine/intensity.py`)
**Utgångsläge: två hårda pass i veckan, ett VO2max-pass och ett tröskelpass, resten lugnt.** Ungefär så tränar
uthållighetseliten året runt (Seiler 2010), och polariserad träning med VO2max-pass har gett större förbättring
än tröskeltung träning (Stöggl & Sperlich 2014). Regeln skalar av sig
själv: mer tid ger fler lugna timmar, inte fler hårda pass.

Antalet är ingen fast siffra. Det går inte att räkna fram det optimala antalet för en person i förväg, så
planeraren utgår från två och justerar efter hur du svarar, ungefär som HRV-styrd träning (Kiviniemi m.fl. 2007,
Javaloyes m.fl. 2019). Gränserna är relativa till din egen baslinje och CTL. Trösklarna för tre pass (6 dagar,
cirka 10 h) och för ett pass (form under −30 %, mer än 40 % missade) är tumregler, inte forskningsresultat.

| Läge | Hårda pass |
|---|---|
| Återhämtningsvecka | 0 |
| Tävlingsvecka och nedtrappning | Som tidigare (korta, skarpa pass) |
| HRV under baslinjen (7-dagarssnitt), form under −30 % av CTL, risk för utbrändhet, RTP, sjuk senaste veckan, mer än 40 % missade nyckelpass senaste 4 veckorna, högst 2 träningsdagar | 1 |
| Normalfallet | 2 |
| Du har gjort två hårda pass i veckan i 3 av de senaste 4 veckorna, HRV normal, form över −20 % av CTL, minst 6 träningsdagar och cirka 10 h | 3 (helgblock: lördag hårt, söndag långpass) |

`KEY_SESSIONS_PER_WEEK` är bara ett tak. HRV justerar dessutom idag och imorgon som tidigare.

**Fasen styr formatet, inte vilka passtyper som finns.** Base: tempo/sweet spot och korta VO2max-intervaller
(1 min hårt / 1 min lätt). Build: tröskelintervaller och klassiska VO2max-intervaller (3–5 min). Fasen tas från
årsplanen, annars från närmaste A-tävling. Utan båda byts formatet var sjätte vecka för variationens skull
(en tumregel; vi har inte hittat stöd för att VO2max-effekten planar ut efter just 6–8 veckor). Varje format har egna progressionsnivåer. Med ett pass i
veckan turas VO2max och tröskel om vecka för vecka. Med tre tillkommer ett tempo- eller tröskelpass.

### Flera idrotter (`engine/sport_mix.py`)
Du anger total tid per vecka och dina tävlingar; planeraren fördelar tiden mellan sporterna. Utan mål bär cykel
volymen (lägst skaderisk) medan löpning och skidor ligger kvar med minst ett par pass i veckan. Uthållighet kan
behållas länge med cirka två pass i veckan om intensiteten finns kvar (Spiering m.fl. 2021), och träningseffekt är
delvis sportspecifik (Millet m.fl. 2002). Därför tar målsporten gradvis över inför en A-tävling (upp till 65 %),
nyckelpassen flyttar dit och de andra sporterna behåller sina underhållspass. Löpning hålls igång året runt
eftersom den belastar skelettet (cyklister har lägre bentäthet än löpare) och för att undvika att börja om från noll.
Last som en sport inte kan ta (åtkomst, väder, budget) flyttas till nästa sport och till sist till cykel. Talen
är tumregler.

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
- Stöggl & Sperlich (2014): *Polarized training has greater impact on key endurance variables than threshold, high intensity, or high volume training*, Frontiers in Physiology.
- Muñoz m.fl. (2014): *Does polarized training improve performance in recreational runners?*, IJSPP.
- Helgerud m.fl. (2007): *Aerobic high-intensity intervals improve VO2max more than moderate training*, MSSE.
- Rønnestad m.fl. (2014): *Block periodization of high-intensity aerobic intervals provides superior training effects in trained cyclists*, Scand J Med Sci Sports.
- Foster (1998): *Monitoring training in athletes with reference to overtraining syndrome*, MSSE.
- Foster m.fl. (2001): *A new approach to monitoring exercise training*, JSCR (session-RPE).

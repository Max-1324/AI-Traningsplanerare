# Jämförelse med andra träningsverktyg

Genomgång i oktober 2026 av kommersiella träningsplanerare och andra AI-coacher som bygger på intervals.icu.
Syftet är att se vad den här planeraren redan gör bra och vad som saknas. Uppgifterna kommer från
tillverkarnas sidor, recensioner och forumtrådar (se källor längst ned). Jag har inte provat verktygen själv.

## Verktygen i korthet

| Verktyg | Sport | Hur planen anpassas | Utmärker sig med |
|---|---|---|---|
| **TrainerRoad** | Cykel | *Adaptive Training*: progressionsnivå per zon, svårighetsnivå 1–10 per pass och en enkät efter passet (Easy … All Out) | Automatisk FTP från vanliga pass (*AI FTP Detection*), *Plan Builder* utifrån träningshistorik, *TrainNow* (välj pass här och nu) |
| **Xert** | Cykel | *Adaptive Training Advisor* justerar målen löpande och periodiserar mot ett eventdatum | *Fitness Signature* (tröskel, anaerob kapacitet, toppeffekt); passen anpassas i realtid under körningen |
| **JOIN** | Cykel (+ löpning) | Du anger tillgänglighet per vecka och dag, och planen räknas om direkt när den ändras eller när du hoppar över eller gör mer | eFTP-prognos, 400+ pass |
| **Athletica.ai** | Uthållighet | AI-coach som analyserar HRV enligt etablerad praxis | Synkar två veckors plan till intervals.icu |
| **Humango** | Multisport | AI-coachen läser pass, puls, sömn, trötthet och ditt schema | Schemamedveten planering, triathlon i en och samma plan |
| **TriDot** | Triathlon | Planen optimeras mot en individuell stresstålighet (*Training Stress Profile*) | *EnviroNorm*: tempo- och effektmål justeras för värme, luftfuktighet, höjd och vind |
| **Garmin** | Löpning/cykel | *Training Readiness* (sömn, HRV, återhämtning, akut belastning) och dagliga passförslag | Föreslår pass som fyller luckor i fördelningen av låg aerob, hög aerob och anaerob träning. Förklarar varför planen ändras |
| **Runna** | Löpning | Tempomålen justeras om du konsekvent missar eller slår dem. "Not feeling 100%" lättar planen tillfälligt | *Adapt for Heat* (tempo efter värme och fukt), visar bästa tid på dagen |
| **Wahoo SYSTM** | Cykel | Mest fasta planer | 4DP-profil (NM, AC, MAP, FTP) från ett test |
| **TrainingPeaks** | Alla | Årsplan (ATP) med TSS-mål per vecka | Prognos av CTL på tävlingsdagen |
| **IntervalCoach** (bygger på intervals.icu) | Cykel/löpning/sim | Veckoplan och daglig anpassning efter dagsform | Välj metodik (polariserat, pyramidalt, norsk modell); *TrainNow* storleksanpassat efter TSS; analys efter varje pass; Whoop |
| **PacePartner** (bygger på intervals.icu) | Uthållighet | Konversation: "jag har bara 45 min idag, vad ska jag göra?" | Dagsläge i klartext, tar hänsyn till A/B/C-tävlingar |

Deltagarna i intervals.icu-forumet värderar enkelhet, att passen hamnar i kalendern automatiskt, att kunna
fråga sin coach och att få stöd att träna regelbundet. För motionärer är regelbundenheten den största
faktorn.

## Det vi redan gör lika bra eller bättre

- **Belastning från vetenskapen:** veckomål från CTL och mesocykel, eller från din årsplan i intervals.icu
  (som TrainingPeaks och intervals.icu:s egen ATP).
- **Daglig anpassning efter HRV, sömn och readiness** (som Garmin, Athletica och IntervalCoach). Bara idag
  och imorgon påverkas, så veckan står stilla.
- **Progressionsnivåer per passtyp**, en enklare variant av TrainerRoads system.
- **Väder:** ute eller inne, och tid på dagen, valt efter prognosen. Få verktyg gör detta.
- **Dina egna pass, tävlingsveckor, RTP, skador och semester/sjukdom från kalendern** respekteras redan när
  planen byggs.
- **Fungerar helt utan AI.** AI:n gör inte planen, bara texterna och val mellan godkända alternativ.
- **Gratis, öppet och anpassningsbart**, och all data ligger kvar i intervals.icu.

## Det som saknas, sorterat efter ROI

| # | Saknas | Vem har det | Varför det spelar roll | Insats |
|---|---|---|---|---|
| 1 | **Tillgänglighet per veckodag** (t.ex. mån 0, tis 60, ons 90, lör 240 min) | JOIN, Humango, IntervalCoach | Idag finns bara ett vardagstak och dagens tid ur wellness-kommentaren. Planen borde följa din vecka och inte bara ett schablonskelett | Liten |
| 2 | **Alternativen synliga för dig på dagen** ("ont om tid: 45 min Z2", "trött: lätt 30 min") | TrainerRoad TrainNow, Runna "Not feeling 100%" | Planeraren räknar redan fram likvärdiga alternativ men visar dem bara för AI:n. Skrivna i passbeskrivningen kan du byta själv | Liten |
| 3 | **Progression som läser alla genomförda pass och kan gå ned** | TrainerRoad, Runna | Idag avgörs nivån bara av gårdagens pass, och den går bara uppåt. Missade dagar och misslyckade pass påverkar inte nivån | Medel |
| 4 | **Subjektiv återkoppling** (RPE, Feel, wellness-fälten fatigue, soreness, mood, motivation) | TrainerRoad-enkäten, Garmin | Data finns redan i intervals.icu men används bara delvis. Bättre readiness och progression utan extra jobb för dig | Liten–medel |
| 5 | **FTP utan test** via intervals.icu:s eFTP | TrainerRoad AI FTP, JOIN, Xert | Färre tester. Planeraren kan föreslå zonuppdatering när eFTP drar ifrån din satta FTP | Liten–medel |
| 6 | **Värmejustering av intensiteten** | TriDot, Runna | Vi anpassar näring och ute/inne, men inte målen. Vid hög temperatur bör puls- och effektmålen sänkas eller intervallerna kortas | Liten |
| 7 | **Förklaring per ändrat pass** ("flyttat från tisdag: låg HRV") | Garmin | Bygger förtroende. Informationen finns redan i planeraren | Liten |
| 8 | **Val av metodik** (polariserat, pyramidalt, tröskelbetonat/norskt) | IntervalCoach, Xert | Styr vilka nyckelpass som väljs och hur zonfördelningen ser ut | Medel |
| 9 | **Analys av varje pass mot planen** (kvalitetspoäng per intervall) | IntervalCoach, TrainerRoad | Gårdagsfeedback finns, men ingen strukturerad poäng som också styr progressionen | Medel |
| 10 | **Konversation med coachen** ("jag har bara 45 min") | PacePartner, IntervalCoach, Athletica | Idag går det via wellness-kommentaren och gäller från nästa körning. En chatt (t.ex. en Telegram-bot) är bekvämare | Stor |
| 11 | Mer avancerad fysiologisk modell (Fitness Signature, 4DP) | Xert, Wahoo | FTP-baserade zoner räcker långt för en motionär | Stor, låg nytta |
| 12 | Realtidsanpassning under passet | Xert | Kräver en egen app på cykeln eller klockan | Utanför projektet |
| 13 | Triathlon- och simspecifika pass | Humango, TriDot | Bara om du börjar med triathlon | Medel |

Punkt 1, 2, 4, 6 och 7 är små ändringar i planeraren och ger mest för insatsen. De ligger också i
[ROADMAP.md](ROADMAP.md).

## Källor
- [TrainerRoad: A Beginner's Guide](https://www.trainerroad.com/blog/trainerroad-a-beginners-guide-to-smarter-cycling-training/),
  [Adaptive Training-enkäten](https://trainerroad.com/forum/t/adaptive-training-feedback-questions-after-a-workout/66841)
- [TrainerRoad vs. Xert](https://www.trainerroad.com/blog/?p=76342),
  [Xert Magic Buckets](https://the5krunner.com/2025/02/02/xerts-new-magic-buckets-training-tool/)
- [JOIN – How JOIN works](https://join.cc/how-join-works/)
- [Athletica × intervals.icu](https://athletica.ai/announcing-athleticas-integration-with-intervals-icu/)
- [Humango (App Store)](https://apps.apple.com/app/id1554430755)
- [TriDot – What TriDot delivers](https://tps.tridot.com/what-tridot-delivers)
- [Garmin Coach vs Daily Suggested Workouts](https://www.wareable.com/garmin/garmin-coach-vs-daily-suggested-workouts-key-differences),
  [Garmin adaptiva planer med förklaringar](https://the5krunner.com/2024/11/12/garmin-adaptive-plans-get-improved-explanations/)
- [Is Runna AI?](https://the5krunner.com/2026/09/28/is-runna-ai/),
  [Runna Adapt for Heat](https://the5krunner.com/2026/07/29/runna-launches-adapt-for-heat-to-adjust-pace-for-weather/)
- [Wahoo SYSTM långtidstest](https://slowtwitch.com/training/wahoo-systm-long-term-review/)
- [TrainingPeaks ATP-metoder](https://help.trainingpeaks.com/hc/en-us/articles/224662768-Annual-Training-Plan-Methodologies)
- [IntervalCoach](https://the5krunner.com/2026/06/23/intervalcoach-ai-training-app/),
  [forumtråd](https://forum.intervals.icu/t/intervalcoach-ai-workouts-that-adapt-daily-to-your-recovery-and-goals/120045)
- [PacePartner](https://forum.intervals.icu/t/tool-pacepartner-app-an-ai-coach-that-reads-your-intervals-icu-data-and-adapts-your-plan-on-the-fly/123736)
- [Forum: jämförelser av AI-verktyg för intervals.icu](https://forum.intervals.icu/t/any-reviews-or-comparisons-of-the-wealth-of-ai-tools-for-intervals/123739?page=4)
- [intervals.icu – Annual Training Plan Builder](https://www.intervals.icu/features/annual-training-plan/)

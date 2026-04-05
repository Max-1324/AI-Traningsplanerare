from training_plan.core.common import *

# ══════════════════════════════════════════════════════════════════════════════
# STRENGTH EXERCISE LIBRARY
# ══════════════════════════════════════════════════════════════════════════════

STRENGTH_LIBRARY = {
    # ── Periodiserade faser (väljs automatiskt baserat på mesocykelvecka) ──────
    "bas_styrka": {
        "name": "Fas 1 – Basstyrka (hög rep, kroppsvikt)",
        "phase": 1,
        "exercises": [
            {"exercise": "Knäböj (kroppsvikt)", "sets": 3, "reps": "20-25", "rest_sec": 60,
             "notes": "Fokus på djup och knäkontroll. Full ROM. Aktivera glutes i toppen."},
            {"exercise": "Glute bridge (tvåben)", "sets": 3, "reps": "20", "rest_sec": 45,
             "notes": "Pressa högt, squeeze 2s. Bra bas för sadelstabilitet."},
            {"exercise": "Calf raises (stående, tvåben)", "sets": 3, "reps": "25", "rest_sec": 30,
             "notes": "Full ROM. Långsam excentrisk (3 sek ner). Förebygger hälseneproblem."},
            {"exercise": "Planka (framlänges)", "sets": 3, "reps": "45s", "rest_sec": 30,
             "notes": "Neutral rygg. Spän core och glutes. Andas normalt."},
            {"exercise": "Superman/rygglyft", "sets": 3, "reps": "15", "rest_sec": 30,
             "notes": "Håll 2s uppe. Aktiverar ryggextensorer mot ländryggsproblem."},
            {"exercise": "Sidoplanka", "sets": 2, "reps": "30s/sida", "rest_sec": 20,
             "notes": "Höften uppe. Obligatorisk för IT-band-prevention."},
        ],
    },
    "bygg_styrka": {
        "name": "Fas 2 – Byggstyrka (lägre rep, svårare kroppsvikt)",
        "phase": 2,
        "exercises": [
            {"exercise": "Bulgarska utfall", "sets": 4, "reps": "8-10/ben", "rest_sec": 75,
             "notes": "Bakre fot på stol/bänk. Djupt, kontrollerat. Excentrisk fas 3 sek."},
            {"exercise": "Pistol squat (assisterat om nödvändigt)", "sets": 3, "reps": "6-8/ben", "rest_sec": 90,
             "notes": "Håll i vägg. Excentrisk fas 4 sek ner. Viktigaste enbensstyrkeövningen."},
            {"exercise": "Enbensstående calf raises (excentrisk)", "sets": 3, "reps": "10-12/ben", "rest_sec": 60,
             "notes": "Upp på två, ner på ett (4 sek). Starkaste förebyggandet mot hälsenebesvär."},
            {"exercise": "Nordic hamstring curl (håll i dörr)", "sets": 3, "reps": "6-8", "rest_sec": 90,
             "notes": "Excentrisk hamstring. Knä på mjukt underlag, håll anklar fast. Förebygger hamstringsbrott."},
            {"exercise": "Glute bridge (enben)", "sets": 3, "reps": "12-15/ben", "rest_sec": 45,
             "notes": "Pressa högt, håll 2s. Enbensfokus för muskelobalans."},
            {"exercise": "Planka shoulder tap", "sets": 3, "reps": "10/sida", "rest_sec": 45,
             "notes": "Planka-position, tap axel utan att vrida kroppen. Core-antirotation."},
        ],
    },
    "underhall_styrka": {
        "name": "Fas 3 – Underhållsstyrka (enbensstabilitet, taper/race)",
        "phase": 3,
        "exercises": [
            {"exercise": "Single-leg balance (ögon stängda)", "sets": 2, "reps": "45s/ben", "rest_sec": 15,
             "notes": "Enkel men effektiv. Aktiverar fotledsproprioception. Inget tröttande."},
            {"exercise": "Enbensstående calf raise (lugnt)", "sets": 2, "reps": "15/ben", "rest_sec": 20,
             "notes": "Underhåller senan utan att trötta ut."},
            {"exercise": "Glute activation walk (band runt knän)", "sets": 2, "reps": "15 steg/sida", "rest_sec": 15,
             "notes": "Band strax ovanför knän. Förhindrar gluteus medius-avslappning."},
            {"exercise": "Cat-cow mobilitet", "sets": 2, "reps": "10 reps", "rest_sec": 0,
             "notes": "Rygg- och höftmobilitet. Inget tröttande inför tävling."},
        ],
    },

    # ── Sportspecifika program (väljs av AI baserat på context) ───────────────
    "cycling_strength": {
        "name": "Cykelspecifik styrka (kroppsvikt)",
        "exercises": [
            {"exercise": "Pistol squats (eller assisterade)", "sets": 3, "reps": "6-10/ben", "rest_sec": 90,
             "notes": "Kontrollerad excentrisk fas. Håll i vägg om nödvändigt."},
            {"exercise": "Bulgarska utfall",   "sets": 3, "reps": "10-12/ben", "rest_sec": 60,
             "notes": "Bakre foten på stol/bänk. Djupt, kontrollerat."},
            {"exercise": "Glute bridges (enben)", "sets": 3, "reps": "12-15/ben", "rest_sec": 45,
             "notes": "Pressa genom hälen. Squeeze glutes i toppen 2s."},
            {"exercise": "Calf raises (enben)", "sets": 3, "reps": "15-20/ben", "rest_sec": 30,
             "notes": "Full range of motion. Långsam excentrisk."},
            {"exercise": "Planka (framlänges)", "sets": 3, "reps": "30-60s", "rest_sec": 30,
             "notes": "Håll rak linje. Spän core."},
            {"exercise": "Sidoplanka",          "sets": 2, "reps": "30-45s/sida", "rest_sec": 30,
             "notes": "Höften uppe. Aktivera obliques."},
        ],
    },
    "runner_strength": {
        "name": "Löpspecifik styrka (kroppsvikt)",
        "exercises": [
            {"exercise": "Knäböj (kroppsvikt)", "sets": 3, "reps": "15-20", "rest_sec": 60,
             "notes": "Full djup. Knäna över tårna OK."},
            {"exercise": "Step-ups (stol/bänk)", "sets": 3, "reps": "10-12/ben", "rest_sec": 60,
             "notes": "Driv upp med framlår. Kontrollerad nedåt."},
            {"exercise": "Rumänsk marklyft (enben, kroppsvikt)", "sets": 3, "reps": "10-12/ben", "rest_sec": 60,
             "notes": "Balans + hamstringsaktivering. Rak rygg."},
            {"exercise": "Calf raises (enben)", "sets": 3, "reps": "15-20/ben", "rest_sec": 30,
             "notes": "Full range. Explosiv uppåt, långsam nedåt."},
            {"exercise": "Planka med höftdips",  "sets": 3, "reps": "10-12/sida", "rest_sec": 30,
             "notes": "Planka-position, rotera höfterna sida till sida."},
            {"exercise": "Clamshells (med band om möjligt)", "sets": 2, "reps": "15-20/sida", "rest_sec": 30,
             "notes": "Gluteus medius. Viktigt för knästabilitet."},
        ],
    },
    "general_strength": {
        "name": "Generell kroppsviktsstyrka",
        "exercises": [
            {"exercise": "Armhävningar",        "sets": 3, "reps": "10-20", "rest_sec": 60,
             "notes": "Full range. Variant: knäståend om för svårt."},
            {"exercise": "Dips (stol/bänk)",     "sets": 3, "reps": "8-15", "rest_sec": 60,
             "notes": "90° armbågsvinkel nedåt. Press up."},
            {"exercise": "Chins/pull-ups (om tillgängligt)", "sets": 3, "reps": "5-10", "rest_sec": 90,
             "notes": "Alternativ: inverterade rows med bord."},
            {"exercise": "Planka (framlänges)", "sets": 3, "reps": "45-60s", "rest_sec": 30,
             "notes": "Spän allt. Andas normalt."},
            {"exercise": "Superman/rygglyft",    "sets": 3, "reps": "12-15", "rest_sec": 30,
             "notes": "Lyft armar+ben 2s, sänk kontrollerat."},
            {"exercise": "Dead bugs",            "sets": 3, "reps": "10/sida", "rest_sec": 30,
             "notes": "Ländrygg i golvet. Långsam kontroll."},
        ],
    },
}

# ══════════════════════════════════════════════════════════════════════════════
# PREHAB LIBRARY – skadeförebyggande rörlighetsövningar per sport
# ══════════════════════════════════════════════════════════════════════════════

PREHAB_LIBRARY = {
    "cyclist": {
        "name": "Cyklist-prehab (10-15min)",
        "exercises": [
            {"exercise": "Hip flexor stretch (pigeon pose)", "sets": 2, "reps": "60s/sida",
             "notes": "Höger fot framför, vänster ben baksträckt. Håll 60s. Motverkar tight höftböjare från sadel."},
            {"exercise": "IT-band foam roll", "sets": 1, "reps": "90s/ben",
             "notes": "Rulla längs yttre låret. Pausa 5s på ömma punkter. Förebygger IT-band-syndrom."},
            {"exercise": "Cat-cow ryggmobilitet", "sets": 2, "reps": "10 reps",
             "notes": "På alla fyra. Rund rygg → sänkt rygg. Segmentera varje kotled. Viktigt efter långpass."},
            {"exercise": "Knee tracking lunge", "sets": 2, "reps": "8/ben",
             "notes": "Knät pekar rakt fram, INTE inåt. Aktiverar VMO och förebygger patellasyndrom."},
        ],
    },
    "runner": {
        "name": "Löpar-prehab (10-15min)",
        "exercises": [
            {"exercise": "Glute activation – side-lying clam", "sets": 2, "reps": "15/sida",
             "notes": "Ligg på sidan. Hälar ihop. Lyft knät utan att rulla höften. Gluteus medius mot knäsmärta."},
            {"exercise": "Eccentric calf lowering (enben)", "sets": 3, "reps": "12/ben",
             "notes": "Upp på två ben, ner på ett. 4 sek excentrisk fas. Förebygger hälseneproblem."},
            {"exercise": "Hip stability – single leg balance", "sets": 2, "reps": "30s/ben",
             "notes": "Stå på ett ben. Ögon stängda för svårare variant. Fotledsaktivering."},
            {"exercise": "Ankle circles + toe raises", "sets": 2, "reps": "20/riktning",
             "notes": "Fotledscirklar + ståendes upp på tårna. Fotledsmobilitet för löpsteg."},
        ],
    },
    "general": {
        "name": "Generell prehab (10min)",
        "exercises": [
            {"exercise": "Thoracic spine rotation", "sets": 2, "reps": "10/sida",
             "notes": "Sitt på knä, hand bakom huvud, rotera bröstkorgen. Motverkar rörstyvhet."},
            {"exercise": "90/90 hip stretch", "sets": 2, "reps": "60s/sida",
             "notes": "Frambenet och bakbenet i 90°. Yttre och inre höftrotation. Sitter på golvet."},
        ],
    },
}

# ══════════════════════════════════════════════════════════════════════════════
# SCHEDULE CONSTRAINTS (via intervals.icu NOTE-events)
# ══════════════════════════════════════════════════════════════════════════════
#
# Atleten skapar NOTE-events i intervals.icu med namn som:
#   "Bara: löpning"          → bara Run tillåten den dagen
#   "Bara: löpning, styrka"  → bara Run + WeightTraining
#   "Ej: cykling, rullskidor" → blockera Ride + RollerSki
#   "Bara löpning"           → fungerar utan kolon också
#   "Bara: jogga"            → synonym för Run
#
# Skriptet läser dessa automatiskt och enforcar begränsningarna.
# ══════════════════════════════════════════════════════════════════════════════




def _parse_sport_names(text: str) -> list[str]:
    """Mappar svenska sportnamn till intervals_type. Returnerar lista."""
    text = text.lower().strip().rstrip(".")
    parts = re.split(r"[,&+/]+|\soch\s", text)
    result = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        # Direktmatch
        if part in SPORT_NAME_MAP:
            result.extend(SPORT_NAME_MAP[part])
            continue
        # Partiell match (t.ex. "rullskid" matchar "rullskidor")
        for key, types in SPORT_NAME_MAP.items():
            if key.startswith(part) or part.startswith(key):
                result.extend(types)
                break
    return list(dict.fromkeys(result))  # Behåll ordning, ta bort dubbletter


def parse_constraints_from_events(events: list) -> list[dict]:
    """
    Skannar intervals.icu-events efter NOTE-events med begränsningsnamn.

    Söker efter events vars namn börjar med:
      "Bara:" / "Bara " → allowed_types (bara dessa sporter)
      "Ej:" / "Ej "     → blocked_types (dessa sporter blockerade)

    Returnerar lista av {date, allowed_types, blocked_types, reason}.
    """
    constraints = []
    for e in events:
        name = (e.get("name") or "").strip()
        if not name:
            continue

        name_lower = name.lower()

        # Kolla om det matchar ett constraint-prefix
        mode = None  # "bara" eller "ej"
        sport_text = ""

        for prefix in CONSTRAINT_PREFIXES:
            if name_lower.startswith(prefix):
                mode = "bara" if prefix.startswith(("bara", "only")) else "ej"
                sport_text = name[len(prefix):].strip()
                break

        if not mode or not sport_text:
            continue

        # Extrahera datum
        start_str_full = e.get("start_date_local") or ""
        end_str_full = e.get("end_date_local") or ""
        start_str = start_str_full[:10]
        end_str = end_str_full[:10]
        if not start_str:
            continue
        if not end_str:
            end_str = start_str

        # Parsa sporter
        sport_types = _parse_sport_names(sport_text)
        if not sport_types:
            log.warning(f"Kunde inte tolka sporter i constraint: '{name}' → '{sport_text}'")
            continue

        # Beskrivning/reason
        reason = (e.get("description") or "").split("\n")[0][:100] or name

        try:
            start_date = datetime.strptime(start_str, "%Y-%m-%d").date()
            end_date = datetime.strptime(end_str, "%Y-%m-%d").date()
        except ValueError:
            continue
            
        # Om eventet slutar exakt vid midnatt (T00:00:00) gäller det fram till dagen innan
        if end_str_full.endswith("T00:00:00") and end_date > start_date:
            end_date -= timedelta(days=1)

        if end_date < start_date:
            end_date = start_date
            
        current_date = start_date
        while current_date <= end_date:
            constraint = {
                "date": current_date.isoformat(),
                "reason": reason,
            }
            if mode == "bara":
                constraint["allowed_types"] = sport_types
            else:
                constraint["blocked_types"] = sport_types

            constraints.append(constraint)
            log.info(f"📅 Constraint: {current_date.isoformat()} → {mode.upper()} {', '.join(sport_types)} ({reason})")
            current_date += timedelta(days=1)

    return constraints


def enforce_schedule_constraints(days: list[PlanDay], constraints: list[dict]) -> tuple[list[PlanDay], list[str]]:
    """Tillämpar dagsspecifika sportbegränsningar från intervals.icu NOTEs."""
    if not constraints:
        return days, []

    # Bygg datum → constraint lookup
    constraint_by_date: dict[str, list[dict]] = {}
    for c in constraints:
        d = c.get("date", "")
        if d:
            constraint_by_date.setdefault(d, []).append(c)

    changes = []
    for i, day in enumerate(days):
        if day.intervals_type == "Rest" or day.duration_min == 0:
            continue

        day_constraints = constraint_by_date.get(day.date, [])
        if not day_constraints:
            continue

        for c in day_constraints:
            allowed = set(c.get("allowed_types", []))
            blocked = set(c.get("blocked_types", []))
            reason = c.get("reason", "Schema-begränsning")

            sport_blocked = False
            if allowed and day.intervals_type not in allowed:
                sport_blocked = True
            if blocked and day.intervals_type in blocked:
                sport_blocked = True

            if sport_blocked:
                # Välj ersättning: bästa tillåtna sport
                if allowed:
                    replacement = list(allowed)[0]
                else:
                    # Om blocked, välj en icke-blockerad sport
                    fallback_order = ["VirtualRide", "Run", "Ride", "WeightTraining"]
                    replacement = next(
                        (s for s in fallback_order if s not in blocked and s != day.intervals_type),
                        "Rest"
                    )

                old_type = day.intervals_type
                new_dur = min(day.duration_min, 60) if replacement == "Run" else day.duration_min
                days[i] = day.model_copy(update={
                    "intervals_type": replacement,
                    "duration_min": new_dur,
                    "title": f"{day.title} [→ {replacement}]",
                    "description": day.description + f"\n\n📅 Anpassat: {reason}. {old_type} → {replacement}.",
                    "vetoed": False,
                })
                changes.append(f"SCHEMA: {day.date} {old_type} → {replacement} ({reason})")
                break

    return days, changes


def format_constraints_for_prompt(constraints: list[dict], horizon_dates: list[str]) -> str:
    """Formaterar aktiva constraints till prompttext."""
    if not constraints:
        return ""

    lines = ["SCHEMALAGDA BEGRÄNSNINGAR (från atletens NOTE-events i intervals.icu):"]
    for c in constraints:
        d = c.get("date", "")
        if d not in horizon_dates:
            continue
        allowed = c.get("allowed_types", [])
        blocked = c.get("blocked_types", [])
        reason = c.get("reason", "")

        if allowed:
            lines.append(f"  {d} → BARA: {', '.join(allowed)}. ({reason})")
        elif blocked:
            lines.append(f"  {d} → EJ: {', '.join(blocked)}. ({reason})")

    if len(lines) == 1:
        return ""
    lines.append("  RESPEKTERA dessa begränsningar – atleten har lagt in dem själv.")
    lines.append("  VIKTIGT: Ordet 'resa' eller 'semester' betyder oftast bara logistik (t.ex. cykel saknas) – planera då normal och kvalitativ träning. MEN, om beskrivningen specifikt nämner utmattande faktorer (t.ex. 'långresa', 'flygresa 10h', 'jetlag' eller 'trött'), DÅ ska du absolut sänka intensiteten och lägga in återhämtning/vila!")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# PERSISTENT COACH STATE
# ══════════════════════════════════════════════════════════════════════════════


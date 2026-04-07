from training_plan.core.common import *

# ══════════════════════════════════════════════════════════════════════════════
# STRENGTH EXERCISE LIBRARY
# ══════════════════════════════════════════════════════════════════════════════

STRENGTH_LIBRARY = {
    # ── Periodiserade faser (väljs automatiskt baserat på mesocykelvecka) ──────
    "bas_styrka": {
        "name": "Phase 1 - Base strength (high rep, bodyweight)",
        "phase": 1,
        "exercises": [
            {"exercise": "Squats (bodyweight)", "sets": 3, "reps": "20-25", "rest_sec": 60,
             "notes": "Focus on depth and knee control. Full ROM. Activate glutes at the top."},
            {"exercise": "Glute bridge (two legs)", "sets": 3, "reps": "20", "rest_sec": 45,
             "notes": "Press high, squeeze 2s. Good base for saddle stability."},
            {"exercise": "Calf raises (standing, two legs)", "sets": 3, "reps": "25", "rest_sec": 30,
             "notes": "Full ROM. Slow eccentric (3 sec down). Prevents Achilles tendon problems."},
            {"exercise": "Plank (front)", "sets": 3, "reps": "45s", "rest_sec": 30,
             "notes": "Neutral back. Tense core and glutes. Breathe normally."},
            {"exercise": "Superman/back extensions", "sets": 3, "reps": "15", "rest_sec": 30,
             "notes": "Hold 2s at the top. Activates back extensors against lower back problems."},
            {"exercise": "Side plank", "sets": 2, "reps": "30s/side", "rest_sec": 20,
             "notes": "Hips up. Mandatory for IT band prevention."},
        ],
    },
    "bygg_styrka": {
        "name": "Phase 2 - Build strength (lower rep, harder bodyweight)",
        "phase": 2,
        "exercises": [
            {"exercise": "Bulgarian split squats", "sets": 4, "reps": "8-10/leg", "rest_sec": 75,
             "notes": "Back foot on chair/bench. Deep, controlled. Eccentric phase 3 sec."},
            {"exercise": "Pistol squat (assisted if necessary)", "sets": 3, "reps": "6-8/leg", "rest_sec": 90,
             "notes": "Hold on to wall. Eccentric phase 4 sec down. Most important single-leg strength exercise."},
            {"exercise": "Single-leg calf raises (eccentric)", "sets": 3, "reps": "10-12/leg", "rest_sec": 60,
             "notes": "Up on two, down on one (4 sec). Strongest prevention against Achilles tendon issues."},
            {"exercise": "Nordic hamstring curl (hold in door)", "sets": 3, "reps": "6-8", "rest_sec": 90,
             "notes": "Eccentric hamstring. Knees on soft surface, hold ankles firm. Prevents hamstring tears."},
            {"exercise": "Glute bridge (single-leg)", "sets": 3, "reps": "12-15/leg", "rest_sec": 45,
             "notes": "Press high, hold 2s. Single-leg focus for muscle imbalance."},
            {"exercise": "Plank shoulder tap", "sets": 3, "reps": "10/side", "rest_sec": 45,
             "notes": "Plank position, tap shoulder without twisting the body. Core anti-rotation."},
        ],
    },
    "underhall_styrka": {
        "name": "Phase 3 - Maintenance strength (single-leg stability, taper/race)",
        "phase": 3,
        "exercises": [
            {"exercise": "Single-leg balance (eyes closed)", "sets": 2, "reps": "45s/leg", "rest_sec": 15,
             "notes": "Simple but effective. Activates ankle proprioception. Not fatiguing."},
            {"exercise": "Single-leg calf raise (easy)", "sets": 2, "reps": "15/leg", "rest_sec": 20,
             "notes": "Maintains the tendon without fatiguing."},
            {"exercise": "Glute activation walk (band around knees)", "sets": 2, "reps": "15 steps/side", "rest_sec": 15,
             "notes": "Band just above knees. Prevents gluteus medius relaxation."},
            {"exercise": "Cat-cow mobility", "sets": 2, "reps": "10 reps", "rest_sec": 0,
             "notes": "Back and hip mobility. Not fatiguing before race."},
        ],
    },

    # ── Sportspecifika program (väljs av AI baserat på context) ───────────────
    "cycling_strength": {
        "name": "Cycling-specific strength (bodyweight)",
        "exercises": [
            {"exercise": "Pistol squats (or assisted)", "sets": 3, "reps": "6-10/leg", "rest_sec": 90,
             "notes": "Controlled eccentric phase. Hold on to wall if necessary."},
            {"exercise": "Bulgarian split squats",   "sets": 3, "reps": "10-12/leg", "rest_sec": 60,
             "notes": "Back foot on chair/bench. Deep, controlled."},
            {"exercise": "Glute bridges (single-leg)", "sets": 3, "reps": "12-15/leg", "rest_sec": 45,
             "notes": "Press through the heel. Squeeze glutes at the top 2s."},
            {"exercise": "Calf raises (single-leg)", "sets": 3, "reps": "15-20/leg", "rest_sec": 30,
             "notes": "Full range of motion. Slow eccentric."},
            {"exercise": "Plank (front)", "sets": 3, "reps": "30-60s", "rest_sec": 30,
             "notes": "Hold straight line. Tense core."},
            {"exercise": "Side plank",          "sets": 2, "reps": "30-45s/side", "rest_sec": 30,
             "notes": "Hips up. Activate obliques."},
        ],
    },
    "runner_strength": {
        "name": "Running-specific strength (bodyweight)",
        "exercises": [
            {"exercise": "Squats (bodyweight)", "sets": 3, "reps": "15-20", "rest_sec": 60,
             "notes": "Full depth. Knees over toes OK."},
            {"exercise": "Step-ups (chair/bench)", "sets": 3, "reps": "10-12/leg", "rest_sec": 60,
             "notes": "Drive up with quads. Controlled down."},
            {"exercise": "Romanian deadlift (single-leg, bodyweight)", "sets": 3, "reps": "10-12/leg", "rest_sec": 60,
             "notes": "Balance + hamstring activation. Straight back."},
            {"exercise": "Calf raises (single-leg)", "sets": 3, "reps": "15-20/leg", "rest_sec": 30,
             "notes": "Full range. Explosive up, slow down."},
            {"exercise": "Plank with hip dips",  "sets": 3, "reps": "10-12/side", "rest_sec": 30,
             "notes": "Plank position, rotate hips side to side."},
            {"exercise": "Clamshells (with band if possible)", "sets": 2, "reps": "15-20/side", "rest_sec": 30,
             "notes": "Gluteus medius. Important for knee stability."},
        ],
    },
    "general_strength": {
        "name": "General bodyweight strength",
        "exercises": [
            {"exercise": "Push-ups",        "sets": 3, "reps": "10-20", "rest_sec": 60,
             "notes": "Full range. Variant: on knees if too hard."},
            {"exercise": "Dips (chair/bench)",     "sets": 3, "reps": "8-15", "rest_sec": 60,
             "notes": "90° elbow angle down. Press up."},
            {"exercise": "Chins/pull-ups (if available)", "sets": 3, "reps": "5-10", "rest_sec": 90,
             "notes": "Alternative: inverted rows with table."},
            {"exercise": "Plank (front)", "sets": 3, "reps": "45-60s", "rest_sec": 30,
             "notes": "Tense everything. Breathe normally."},
            {"exercise": "Superman/back extensions",    "sets": 3, "reps": "12-15", "rest_sec": 30,
             "notes": "Lift arms+legs 2s, lower controlled."},
            {"exercise": "Dead bugs",            "sets": 3, "reps": "10/side", "rest_sec": 30,
             "notes": "Lower back to floor. Slow control."},
        ],
    },
}

# ══════════════════════════════════════════════════════════════════════════════
# PREHAB LIBRARY – skadeförebyggande rörlighetsövningar per sport
# ══════════════════════════════════════════════════════════════════════════════

PREHAB_LIBRARY = {
    "cyclist": {
        "name": "Cyclist prehab (10-15min)",
        "exercises": [
            {"exercise": "Hip flexor stretch (pigeon pose)", "sets": 2, "reps": "60s/side",
             "notes": "Right foot in front, left leg extended back. Hold 60s. Counteracts tight hip flexors from saddle."},
            {"exercise": "IT-band foam roll", "sets": 1, "reps": "90s/leg",
             "notes": "Roll along outer thigh. Pause 5s on sore spots. Prevents IT band syndrome."},
            {"exercise": "Cat-cow back mobility", "sets": 2, "reps": "10 reps",
             "notes": "On all fours. Round back → arched back. Segment each vertebra. Important after long rides."},
            {"exercise": "Knee tracking lunge", "sets": 2, "reps": "8/leg",
             "notes": "Knee points straight ahead, NOT inwards. Activates VMO and prevents patellofemoral syndrome."},
        ],
    },
    "runner": {
        "name": "Runner prehab (10-15min)",
        "exercises": [
            {"exercise": "Glute activation – side-lying clam", "sets": 2, "reps": "15/side",
             "notes": "Lie on side. Heels together. Lift knee without rolling hip. Gluteus medius against knee pain."},
            {"exercise": "Eccentric calf lowering (single-leg)", "sets": 3, "reps": "12/leg",
             "notes": "Up on two legs, down on one. 4 sec eccentric phase. Prevents Achilles tendon problems."},
            {"exercise": "Hip stability – single leg balance", "sets": 2, "reps": "30s/leg",
             "notes": "Stand on one leg. Eyes closed for harder variant. Ankle activation."},
            {"exercise": "Ankle circles + toe raises", "sets": 2, "reps": "20/direction",
             "notes": "Ankle circles + standing up on toes. Ankle mobility for running stride."},
        ],
    },
    "general": {
        "name": "General prehab (10min)",
        "exercises": [
            {"exercise": "Thoracic spine rotation", "sets": 2, "reps": "10/side",
             "notes": "Sit on knees, hand behind head, rotate rib cage. Counteracts stiffness."},
            {"exercise": "90/90 hip stretch", "sets": 2, "reps": "60s/side",
             "notes": "Front leg and back leg at 90°. Outer and inner hip rotation. Sitting on floor."},
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
    parts = re.split(r"[,&+/]+|\soch\s|\sand\s", text)
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
            log.warning(f"Could not parse sports in constraint: '{name}' → '{sport_text}'")
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
            reason = c.get("reason", "Schedule constraint")

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
                    "description": day.description + f"\n\n📅 Adjusted: {reason}. {old_type} → {replacement}.",
                    "vetoed": False,
                })
                changes.append(f"SCHEDULE: {day.date} {old_type} → {replacement} ({reason})")
                break

    return days, changes


def format_constraints_for_prompt(constraints: list[dict], horizon_dates: list[str]) -> str:
    """Formaterar aktiva constraints till prompttext."""
    if not constraints:
        return ""

    lines = ["SCHEDULED CONSTRAINTS (from athlete's NOTE-events in intervals.icu):"]
    for c in constraints:
        d = c.get("date", "")
        if d not in horizon_dates:
            continue
        allowed = c.get("allowed_types", [])
        blocked = c.get("blocked_types", [])
        reason = c.get("reason", "")

        if allowed:
            lines.append(f"  {d} → ONLY: {', '.join(allowed)}. ({reason})")
        elif blocked:
            lines.append(f"  {d} → NOT: {', '.join(blocked)}. ({reason})")

    if len(lines) == 1:
        return ""
    lines.append("  RESPECT these constraints - the athlete has added them personally.")
    lines.append("  IMPORTANT: The word 'resa' or 'semester' (trip/vacation) usually just means logistics (e.g. no bike available) - plan normal and qualitative training. BUT, if the description specifically mentions exhausting factors (e.g. 'long trip', '10h flight', 'jetlag', or 'tired'), THEN you must absolutely lower the intensity and add recovery/rest!")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# PERSISTENT COACH STATE
# ══════════════════════════════════════════════════════════════════════════════

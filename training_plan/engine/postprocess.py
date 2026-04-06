from training_plan.core.common import *
from training_plan.engine.analysis import *
from training_plan.engine.planning import classify_session_category

# ══════════════════════════════════════════════════════════════════════════════
# POST-PROCESSING – tvingande regler
# ══════════════════════════════════════════════════════════════════════════════

HARD_THRESHOLD = 0.20

def enforce_illness(days, today_wellness):
    """Om atleten är sjuk, byt ut alla pass mot vila."""
    if not today_wellness or not today_wellness.get("sick"):
        return days, []
    changes = ["Sjukdom rapporterad – alla pass konverteras till vila."]
    new_days = []
    for day in days:
        if day.intervals_type != "Rest":
            changes.append(f"  {day.date}: {day.title} → Vila (sjuk)")
        new_days.append(PlanDay(
            date=day.date,
            title="Vila (Sjuk)",
            intervals_type="Rest",
            duration_min=0,
            description="Automatisk vila pga sjukdomsrapport i intervals.icu. Krya på dig!",
            vetoed=True,
        ))
    return new_days, changes

def enforce_rtp(days, rtp_status):
    """Tvinga igenom ett Return-to-Play-protokoll efter flera vilodagar."""
    if not rtp_status or not rtp_status.get("is_active"):
        return days, []
    protocol = [
        {"d": 1, "title": "RTP Dag 1: Test", "type": "VirtualRide", "dur": 30, "steps": [{"duration_min": 30, "zone": "Z1", "description": "Mycket lätt, testa kroppen"}]},
        {"d": 2, "title": "RTP Dag 2: Bekräfta", "type": "VirtualRide", "dur": 45, "steps": [{"duration_min": 45, "zone": "Z2", "description": "Lätt, bekräfta pulsrespons"}]},
        {"d": 3, "title": "RTP Dag 3: Öppna upp", "type": "VirtualRide", "dur": 60, "steps": [{"duration_min": 50, "zone": "Z2", "description": "Grundtempo"}, {"duration_min": 1, "zone": "Z3", "description": "Öppna upp"}, {"duration_min": 9, "zone": "Z2", "description": "Lugnt igen"}]},
    ]
    changes = [f"🚑 RETURN TO PLAY ({rtp_status['days_off']} vilodagar) – tvingande protokoll appliceras."]
    for i, p in enumerate(protocol):
        if i >= len(days):
            break
        target_date = days[i].date
        rtp_day = PlanDay(
            date=target_date,
            title=p["title"],
            intervals_type=p["type"],
            duration_min=p["dur"],
            description=f"Return-to-Play protokoll efter {rtp_status['days_off']} vilodagar.",
            workout_steps=[WorkoutStep(**step) for step in p["steps"]],
            vetoed=False,
        )
        days[i] = rtp_day
        changes.append(f"  {target_date}: Ersatt med '{p['title']}'")
    return days, changes

def intensity_rating(day: PlanDay) -> float:
    if not day.workout_steps or day.duration_min == 0:
        return 0.0
    intense_min = sum(s.duration_min for s in day.workout_steps if s.zone in INTENSE)
    return intense_min / day.duration_min

def is_intense(day: PlanDay) -> bool:
    return intensity_rating(day) >= HARD_THRESHOLD

def enforce_max_consecutive_rest(days):
    """Ersätter tredje vilodagen i rad med ett lätt Z1-pass (30min)."""
    changes = []
    # Bygg en ordnad lista av unika datum med deras "vila"-status
    sorted_days = sorted(days, key=lambda d: d.date)
    is_rest = {d.date: (d.intervals_type == "Rest" or d.duration_min == 0) for d in sorted_days}
    dates = sorted(is_rest.keys())
    consecutive = 0
    rest_streak = []
    for d in dates:
        if is_rest[d]:
            consecutive += 1
            rest_streak.append(d)
        else:
            consecutive = 0
            rest_streak = []
        if consecutive >= 3:
            # Ersätt den tredje vilodagen med ett kort aktiv-vila-pass
            target_date = rest_streak[-1]
            for i, day in enumerate(days):
                if day.date == target_date and (day.intervals_type == "Rest" or day.duration_min == 0):
                    days[i] = day.model_copy(update={
                        "intervals_type": "Run",
                        "duration_min": 30,
                        "title": "Aktiv vila (lätt rörlighet)",
                        "description": "Lätt rörlighetspass eller promenad för att hålla igång cirkulationen utan belastning.",
                        "workout_steps": [WorkoutStep(duration_min=30, zone="Z1", description="Lugn aktivitet")],
                    })
                    changes.append(f"MAX-VILA: {target_date} – 3 vilodagar i rad ersatt med 30min Z1")
                    is_rest[target_date] = False
                    consecutive = 0
                    rest_streak = []
                    break
    return days, changes


def _time_available_minutes(value: str) -> int | None:
    if not value:
        return None
    text = value.strip().lower()
    hours_match = re.search(r"(\d+(?:[.,]\d+)?)\s*h", text)
    mins_match = re.search(r"(\d+)\s*m", text)
    if hours_match:
        return round(float(hours_match.group(1).replace(",", ".")) * 60)
    if mins_match:
        return int(mins_match.group(1))
    if text.isdigit():
        return int(text)
    return None


def _trim_workout_steps(day: PlanDay, new_duration: int) -> list[WorkoutStep]:
    if new_duration <= 0 or not day.workout_steps:
        return []
    remaining = new_duration
    trimmed = []
    for step in day.workout_steps:
        if remaining <= 0:
            break
        step_duration = min(step.duration_min, remaining)
        if step_duration <= 0:
            continue
        trimmed.append(step.model_copy(update={"duration_min": step_duration}))
        remaining -= step_duration
    return trimmed


def enforce_hard_easy(days):
    from datetime import date as _date
    changes = []
    for i in range(1, len(days)):
        r_prev = intensity_rating(days[i-1])
        r_curr = intensity_rating(days[i])
        if r_prev >= HARD_THRESHOLD and r_curr >= HARD_THRESHOLD:
            # Hoppa över om det är mer än 1 dags mellanrum (vilodag emellan)
            try:
                gap = (_date.fromisoformat(days[i].date) - _date.fromisoformat(days[i-1].date)).days
                if gap > 1:
                    continue
            except (ValueError, TypeError):
                pass
            if days[i-1].date == days[i].date and days[i-1].slot == "AM" and days[i-1].intervals_type == "WeightTraining":
                continue
            old = days[i].title
            days[i] = days[i].model_copy(update={
                "title": f"{days[i].title} → Z1 (HARD-EASY)",
                "workout_steps": [WorkoutStep(
                    duration_min=days[i].duration_min,
                    zone="Z1",
                    description=f"Lugnt tempo – HARD-EASY-regel "
                                f"(föregående dag: {round(r_prev*100)}% Z4+)"
                )],
                "nutrition": "",
                "description": f"⚠️ KOD-VETO: AI:n försökte lägga ett hårt pass här, men Python-koden ändrade det till återhämtning (Hard-Easy-regeln).\n\nOriginalidé från AI:n: {days[i].description}",
                "vetoed": True,
            })
            changes.append(
                f"HARD-EASY: {days[i].date} '{old}' "
                f"({round(r_curr*100)}% Z4+) konverterat till Z1"
            )
    return days, changes

def apply_injury_rules(days, injury_note: str):
    if not injury_note or injury_note.lower() in ("", "nej", "n", "inga"):
        return days, []
    inj = injury_note.lower()
    avoid_map = [
        (["knä", "höft", "lår", "vad", "fot", "ankel"],  {"Run", "RollerSki"},     "VirtualRide"),
        (["axel", "armbåge", "handled", "arm"],           {"Ride", "VirtualRide"},  "Run"),
        (["rygg", "ländrygg", "nacke"],                   {"Run", "Ride"},          "VirtualRide"),
        (["skena", "shin"],                               {"Run"},                  "VirtualRide"),
    ]
    affected, replacement = set(), "VirtualRide"
    for keywords, sports, repl in avoid_map:
        if any(k in inj for k in keywords):
            affected |= sports
            replacement = repl
    if not affected:
        affected = {"Run", "RollerSki"}
    changes = []
    for i, day in enumerate(days):
        if day.intervals_type in affected:
            new_dur = min(day.duration_min, 45)
            days[i] = day.model_copy(update={
                "title":          f"{day.title} [→ {replacement}, skada]",
                "intervals_type": replacement,
                "duration_min":   new_dur,
                "description":    day.description + f"\n\n⚠️ Anpassat pga skaderapport: '{injury_note}'",
            })
            changes.append(f"SKADA: {day.date} '{day.intervals_type}' → '{replacement}' ({new_dur}min)")
    return days, changes

def enforce_hrv(days, hrv):
    # Veto endast vid tydligt LOW – SLIGHTLY_LOW och UNSTABLE-ensamt informeras bara AI:n
    if hrv["state"] != "LOW":
        return days, []

    changes = []
    for i, day in enumerate(days):
        # Applicera HRV-veto ENDAST på de första 2 dagarna (idag och imorgon)
        if i <= 1 and is_intense(day):
            recovery_step = WorkoutStep(
                duration_min=day.duration_min,
                zone="Z1",
                description=f"Lugn återhämtning – HRV är LOW ({hrv['deviation_pct']}% under baseline)",
            )
            days[i] = day.model_copy(update={
                "title": f"{day.title} -> Z1 (HRV-VETO)",
                "workout_steps": [recovery_step],
                "nutrition": "",
                "vetoed": True,
            })
            changes.append(f"HRV-VETO: {day.date} - ersatt med Z1 återhämtning (HRV LOW).")
    return days, changes

def enforce_sport_budget(days, budgets):
    accumulated = {st: 0 for st in budgets}
    changes = []
    for i, day in enumerate(days):
        st = day.intervals_type
        if st not in budgets or day.duration_min == 0: continue
        b = budgets[st]
        if accumulated[st] + day.duration_min > b["remaining"]:
            changes.append(f"VOLYMSPÄRR ({st}): {day.date} - {day.duration_min}min överstiger budget ({b['remaining']}min kvar). Konverterar till VirtualRide.")
            days[i] = day.model_copy(update={
                "intervals_type": "VirtualRide",
                "title": f"{day.title} -> Zwift (volymspärr)",
                "vetoed": True,
            })
        else:
            accumulated[st] += day.duration_min
    return days, changes

def enforce_locked(days, locked):
    clean   = [d for d in days if d.date not in locked]
    removed = [d.date for d in days if d.date in locked]
    changes = [f"LÅST DATUM: {d} togs bort (manuellt pass finns)." for d in removed]
    return clean, changes

ZONE_NP_RATIO = {
    "Z1": 0.50, "Zon 1": 0.50,
    "Z2": 0.70, "Zon 2": 0.70,
    "Z3": 0.85, "Zon 3": 0.85,
    "Z4": 1.00, "Zon 4": 1.00,
    "Z5": 1.15, "Zon 5": 1.15,
    "Z6": 1.30, "Z7": 1.50,
}

def ftp_for_sport(sport_type: str, athlete: dict) -> float:
    fallbacks = {
        "VirtualRide": ["VirtualRide", "Ride"],
        "RollerSki":   ["RollerSki", "NordicSki"],
        "NordicSki":   ["NordicSki", "RollerSki"],
        "Run":         ["Run"],
        "Ride":        ["Ride", "VirtualRide"],
    }
    candidates = fallbacks.get(sport_type, [sport_type])
    ftp_map = {}
    for ss in athlete.get("sportSettings", []):
        ftp_val = ss.get("ftp")
        if ftp_val and ftp_val > 0:
            stypes = ss.get("types", []) if isinstance(ss.get("types"), list) else [ss.get("type")]
            for t in stypes:
                if t: ftp_map[t] = float(ftp_val)
    for c in candidates:
        if c in ftp_map:
            return ftp_map[c]
    return 200.0

def estimate_tss_coggan(day, athlete: dict) -> float:
    if day.duration_min == 0 or day.intervals_type == "Rest":
        return 0.0
    if day.intervals_type == "WeightTraining":
        return round(day.duration_min * 0.5, 1)  # ~20 TSS för 40min styrka
    ftp      = ftp_for_sport(day.intervals_type, athlete)
    dur_sek  = day.duration_min * 60
    if day.workout_steps:
        total_min = sum(s.duration_min for s in day.workout_steps) or day.duration_min
        weighted_ratio = sum(
            ZONE_NP_RATIO.get(s.zone, 0.70) * s.duration_min
            for s in day.workout_steps
        ) / total_min
    else:
        weighted_ratio = 0.70
    np_est = weighted_ratio * ftp
    IF     = np_est / ftp
    tss    = (dur_sek * np_est * IF) / (ftp * 3600) * 100
    return round(tss, 1)

def enforce_tss(days, budget, athlete, floor_pct=1.00, ceil_pct=1.00, base_tss_by_date=None, horizon_days=None):
    """Enforcer TSS-golv och -tak per kalendervecka. base_tss_by_date = TSS från befintliga events per datum."""
    from datetime import date as _date

    changes = []
    base_tss_by_date = base_tss_by_date or {}

    # Gruppera dagar per ISO-vecka
    weeks: dict[tuple, list] = {}
    for i, day in enumerate(days):
        try:
            wk = _date.fromisoformat(day.date).isocalendar()[:2]  # (år, veckonummer)
        except (ValueError, TypeError):
            wk = (0, 0)
        weeks.setdefault(wk, []).append(i)

    # Totalt antal kalenderdagar i horisonten (fast nämnare, oberoende av rest/dubbelpass)
    total_days = max(horizon_days or len({d.date for d in days}), 1)
    result = list(days)
    week_summaries = []

    for wk, indices in sorted(weeks.items()):
        # Kalenderdagar i denna vecka som finns i planen (unika datum)
        week_dates = {result[i].date for i in indices}
        week_days_count = len(week_dates)
        # Veckobudget proportionell mot totala horisonten (fast nämnare)
        wk_budget = round(budget * week_days_count / total_days)
        wk_base   = round(sum(base_tss_by_date.get(d, 0) for d in week_dates))

        wk_tss = sum(estimate_tss_coggan(result[i], athlete) for i in indices)

        # TAK: skär ner lättaste passen i veckan
        if wk_tss + wk_base > wk_budget:
            surplus = wk_tss + wk_base - wk_budget
            light = sorted(
                [(i, result[i]) for i in indices
                 if result[i].intervals_type not in ("Rest", "WeightTraining")
                 and result[i].duration_min > _MIN_DURATION.get(result[i].intervals_type, 30)],
                key=lambda x: (
                    {
                        "recovery": 0,
                        "general": 1,
                        "endurance": 2,
                        "long_ride": 3,
                        "threshold": 4,
                        "vo2": 5,
                        "ftp_test": 6,
                    }.get(classify_session_category(x[1].model_dump()), 2),
                    estimate_tss_coggan(x[1], athlete),
                )
            )
            for idx, day in light:
                if surplus <= 0: break
                category = classify_session_category(day.model_dump())
                min_duration = _MIN_DURATION.get(day.intervals_type, 30)
                old_tss = estimate_tss_coggan(day, athlete)
                if category in ("recovery", "general") and day.duration_min <= max(min_duration + 20, 50):
                    result[idx] = day.model_copy(update={
                        "intervals_type": "Rest",
                        "duration_min": 0,
                        "workout_steps": [],
                        "nutrition": "",
                        "title": f"{day.title} [filler borttagen]",
                        "description": (
                            day.description
                            + "\n\nTSS-justering: lagprioriterad fyllnadsvolym togs bort for att skydda viktigare struktur."
                        ),
                        "vetoed": True,
                    })
                    surplus -= old_tss
                    changes.append(f"  {day.date}: filler-pass borttaget -> TAK v{wk[1]}")
                    continue
                reduction = min(30, day.duration_min - min_duration,
                                round(surplus / ((0.65**2 * 100) / 60)))
                if reduction < 10: continue
                new_dur   = day.duration_min - reduction
                new_steps = list(day.workout_steps)
                if new_steps:
                    last = new_steps[-1]
                    new_steps[-1] = last.model_copy(
                        update={"duration_min": max(5, last.duration_min - reduction)})
                result[idx] = day.model_copy(update={
                    "duration_min": new_dur, "workout_steps": new_steps,
                    "title": day.title + f" (-{reduction}min)",
                })
                surplus -= old_tss - estimate_tss_coggan(result[idx], athlete)
                changes.append(f"  {day.date}: -{reduction}min → TAK v{wk[1]}")
            wk_tss = sum(estimate_tss_coggan(result[i], athlete) for i in indices)

        pct    = round((wk_tss + wk_base) / wk_budget * 100) if wk_budget > 0 else 0
        status = "✅" if wk_tss + wk_base <= wk_budget else "⚠️"
        week_summaries.append(f"v{wk[1]}: {round(wk_tss + wk_base)} TSS inkl. låsta pass {status} ({pct}% av {wk_budget})")

    total = sum(estimate_tss_coggan(d, athlete) for d in result)
    changes.append("TSS-AUDIT " + " | ".join(week_summaries) + f" | Totalt {round(total)} TSS")
    return result, changes


def enforce_today_time_budget(days: list[PlanDay], time_available_text: str) -> tuple[list[PlanDay], list[str]]:
    available_min = _time_available_minutes(time_available_text)
    if available_min is None:
        return days, []

    today_str = date.today().isoformat()
    today_indices = [
        idx for idx, day in enumerate(days)
        if day.date == today_str and day.intervals_type != "Rest" and day.duration_min > 0
    ]
    if not today_indices:
        return days, []

    def removable_priority(day: PlanDay) -> tuple[int, int]:
        category = classify_session_category(day.model_dump())
        min_duration = _MIN_DURATION.get(day.intervals_type, 0)
        return (
            0 if min_duration > available_min else 1,
            {
                "recovery": 0,
                "general": 1,
                "endurance": 2,
                "strength": 3,
                "long_ride": 4,
                "threshold": 5,
                "vo2": 6,
                "ftp_test": 7,
            }.get(category, 2),
            -day.duration_min,
        )

    changes = []
    total_today = sum(days[idx].duration_min for idx in today_indices)
    if total_today <= available_min:
        return days, []

    active_today = list(today_indices)
    for idx in sorted(active_today, key=lambda item: removable_priority(days[item])):
        if total_today <= available_min or len(active_today) <= 1:
            break
        day = days[idx]
        days[idx] = day.model_copy(update={
            "intervals_type": "Rest",
            "duration_min": 0,
            "workout_steps": [],
            "strength_steps": [],
            "nutrition": "",
            "title": f"{day.title} [tidsbudget]",
            "description": (
                day.description
                + f"\n\nTidsjustering: dagens totala tid behovde rymmas inom {available_min} min."
            ),
            "vetoed": True,
        })
        total_today -= day.duration_min
        active_today.remove(idx)
        changes.append(f"TIDSBUDGET: {day.date} tog bort '{day.title}' for att rymmas inom {available_min}min idag")

    if total_today > available_min and active_today:
        idx = max(active_today, key=lambda item: days[item].duration_min)
        day = days[idx]
        other_total = total_today - day.duration_min
        new_duration = max(available_min - other_total, 0)
        min_duration = _MIN_DURATION.get(day.intervals_type, 0)
        if new_duration < min_duration:
            days[idx] = day.model_copy(update={
                "intervals_type": "Rest",
                "duration_min": 0,
                "workout_steps": [],
                "strength_steps": [],
                "nutrition": "",
                "title": f"{day.title} [tidsbudget -> vila]",
                "description": (
                    day.description
                    + f"\n\nTidsjustering: dagens tid ({available_min} min) rackte inte for minimilangd."
                ),
                "vetoed": True,
            })
            changes.append(f"TIDSBUDGET: {day.date} ersatte '{day.title}' med vila eftersom {available_min}min ar under minimilangd")
        else:
            days[idx] = day.model_copy(update={
                "duration_min": new_duration,
                "workout_steps": _trim_workout_steps(day, new_duration),
                "title": f"{day.title} ({day.duration_min}->{new_duration}min)",
                "description": (
                    day.description
                    + f"\n\nTidsjustering: dagens totala tid klamptes till {available_min} min."
                ),
                "vetoed": True,
            })
            changes.append(f"TIDSBUDGET: {day.date} kortade '{day.title}' till {new_duration}min for att rymmas inom {available_min}min idag")

    return days, changes

# ══════════════════════════════════════════════════════════════════════════════
# NÄRINGSSTYRNING (periodiserad)
# ══════════════════════════════════════════════════════════════════════════════

def calculate_nutrition_periodization(phase_name: str, days_to_race: Optional[int],
                                       workout_day, tss_estimate: float,
                                       weight_kg: float | None = None) -> str:
    """
    Returnerar näringsstrategi baserat på träningsfas, tävlingsproximitet och passets belastning.
    Kompletterar miljöbaserad nutrition med periodiserade rekommendationer.
    """
    dur = workout_day.duration_min
    sport = workout_day.intervals_type

    if sport in ("Rest", "WeightTraining") or dur < 30:
        return ""

    def cho_range(low_g_kg: float, high_g_kg: float) -> str:
        if weight_kg:
            lo = round(low_g_kg * weight_kg)
            hi = round(high_g_kg * weight_kg)
            return f"{lo}–{hi}g CHO ({low_g_kg}–{high_g_kg}g/kg × {round(weight_kg)}kg)"
        return f"{low_g_kg}–{high_g_kg}g CHO/kg kroppsvikt"

    # Tävlingsdag
    if days_to_race == 0:
        return ("TÄVLINGSDAG: Start 300ml sportdryck. 60-90g CHO/h under loppet (gels + bars). "
                "500mg Na/h. Koffein 200mg vid t-1h. Drick 500ml i mål.")

    # Kolhydratladning 3 dagar före
    if days_to_race is not None and 1 <= days_to_race <= 3:
        dag = 4 - days_to_race
        return (f"KOLHYDRATLADNING dag {dag}/3: {cho_range(8, 10)} idag. "
                f"Ris, pasta, havregryn, bröd. Undvik fiber och fett. Drick 2-3L.")

    # Hög TSS-dag
    if tss_estimate > 100:
        return (f"HIGH-CARB: {round(tss_estimate)} TSS planerat – {cho_range(6, 8)} idag. "
                f"Frukost: havregryn + banan + honung. Under: 60-90g CHO/h.")

    # Basfas + Z2-pass (fasted training OK)
    is_z2_only = all(s.zone in ("Z1", "Z2") for s in workout_day.workout_steps) if workout_day.workout_steps else True
    if phase_name in ("Base", "Grundtraning") and is_z2_only and 60 <= dur <= 90:
        return ("FASTED OK: Morgonpass 60-90min Z2 kan göras fastad för fettadaptation. "
                "Max 30g CHO/h om du är hungrig. Ha en gel redo.")

    # Standard baserat på duration
    if dur < 60:
        return ""
    elif dur <= 90:
        return f"30-60g CHO/h under passet ({dur}min). Sportdryck eller 1 gel/45min."
    else:
        return f"60-90g CHO/h under passet ({dur}min). Testa race-dag-nutrition."


def add_env_nutrition(days, weather, phase=None, races=None, athlete=None, wellness=None):
    weight_kg: float | None = None
    if wellness:
        for w in reversed(wellness):
            if w.get("weight"):
                weight_kg = float(w["weight"])
                break
    wmap = {w["date"]: w for w in weather}
    for i, day in enumerate(days):
        if day.duration_min < 60 or day.intervals_type in ("Rest","WeightTraining"): continue
        w = wmap.get(day.date, {})
        fz = day.workout_steps[0].zone if day.workout_steps else "Z2"

        if day.slot == "AM":
            temp = w.get("temp_morning", w.get("temp_min", 10))
        else:
            temp = w.get("temp_afternoon", w.get("temp_max", 15))

        extra = env_nutrition(temp, day.duration_min, fz)
        nutr_parts = [day.nutrition] if day.nutrition else []
        if extra:
            nutr_parts.append(" ".join(extra))

        # Periodiserad nutrition
        if phase and races is not None:
            d2r = None
            future_races = [r for r in races if r.get("start_date_local","")[:10] >= day.date]
            if future_races:
                try:
                    rd = datetime.strptime(future_races[0]["start_date_local"][:10], "%Y-%m-%d").date()
                    day_date = datetime.strptime(day.date, "%Y-%m-%d").date()
                    d2r = (rd - day_date).days
                except Exception:
                    pass
            tss_est = 0.0
            if athlete:
                try:
                    tss_est = estimate_tss_coggan(day, athlete)
                except Exception:
                    pass
            phase_name = phase.get("phase", "Base") if isinstance(phase, dict) else str(phase)
            perio = calculate_nutrition_periodization(phase_name, d2r, day, tss_est, weight_kg)
            if perio:
                nutr_parts.append(perio)

        new_nutr = "\n".join(p for p in nutr_parts if p).strip()
        if new_nutr != day.nutrition:
            days[i] = day.model_copy(update={"nutrition": new_nutr})
    return days

def enforce_strength_limit(days, max_strength=2, min_gap=2):
    changes = []
    strength_count = 0
    last_strength_idx = -99
    for i, day in enumerate(days):
        if day.intervals_type != "WeightTraining": continue
        too_close  = (i - last_strength_idx) < min_gap
        too_many   = strength_count >= max_strength
        if too_many or too_close:
            reason = "styrkegräns (max 2)" if too_many else f"för tätt (< {min_gap} dagar sedan förra)"
            days[i] = day.model_copy(update={
                "title":          day.title + f" → Zwift Z1 ({reason})",
                "intervals_type": "VirtualRide",
                "duration_min":   45,
                "workout_steps":  [WorkoutStep(duration_min=45, zone="Z1", description="Lätt återhämtningscykling @ <120W")],
                "strength_steps": [],
                "description":    day.description + f"\n\n⚠️ Konverterad – {reason}.",
                "vetoed": True,
            })
            changes.append(f"STYRKEGRÄNS: {day.date} → Zwift Z1 ({reason})")
        else:
            strength_count  += 1
            last_strength_idx = i
    return days, changes

def enforce_rollski_limit(days, max_per_week=1):
    changes = []
    rollski_days = [(i, day) for i, day in enumerate(days) if day.intervals_type == "RollerSki"]
    seen_weeks = set()
    to_convert = set()
    for i, day in rollski_days:
        week = datetime.strptime(day.date, "%Y-%m-%d").isocalendar()[1]
        if week in seen_weeks:
            to_convert.add(i)
        else:
            seen_weeks.add(week)
    for i in to_convert:
        day = days[i]
        days[i] = day.model_copy(update={
            "title":          day.title + " → Cykling (rullskidsgräns)",
            "intervals_type": "Ride",
            "description":    day.description + "\n\n⚠️ Konverterad – max 1 rullskidspass/vecka.",
            "vetoed": True,
        })
        changes.append(f"RULLSKIDSGRÄNS: {day.date} → Ride (max 1/vecka)")
    return days, changes


_MIN_DURATION = MIN_DURATION_BY_SPORT

def enforce_min_duration(days: list) -> list:
    """Klampar duration till minimum per sport – hoppar över vetade/återhämtningspass."""
    for i, day in enumerate(days):
        if day.vetoed:
            continue
        min_dur = _MIN_DURATION.get(day.intervals_type)
        if min_dur and 0 < day.duration_min < min_dur:
            days[i] = day.model_copy(update={"duration_min": min_dur})
            log.debug(f"enforce_min_duration: {day.date} {day.intervals_type} {day.duration_min}→{min_dur}min")
    return days


def ensure_warmup(days: list) -> list:
    """Lägger till en sportspecifik uppvärmningstext i description för varje träningspass."""
    for i, day in enumerate(days):
        if day.intervals_type in ("Rest", "WeightTraining") or day.duration_min == 0:
            continue
        if "uppvärmning" in day.description.lower():
            continue
        warmup_text = WARMUP_BY_SPORT.get(day.intervals_type, WARMUP_DEFAULT)
        new_desc = warmup_text + "\n\n" + day.description if day.description else warmup_text
        days[i] = day.model_copy(update={"description": new_desc})
    return days


def enforce_deload(days, mesocycle: dict, athlete: dict):
    if not mesocycle["is_deload"]:
        return days, []
    changes = [f"🟡 DELOAD-VECKA (vecka {mesocycle['week_in_block']}/4, "
               f"block {mesocycle['block_number']}). Sänker volym och intensitet."]
    for i, day in enumerate(days):
        modified = False
        updates = {}
        if day.duration_min > 0 and day.intervals_type != "Rest":
            new_dur = round(day.duration_min * 0.65)
            updates["duration_min"] = new_dur
            modified = True
        if day.workout_steps:
            new_steps = []
            for s in day.workout_steps:
                if s.zone in INTENSE:
                    new_steps.append(s.model_copy(update={
                        "zone": "Z2",
                        "description": f"[DELOAD] {s.description} – sänkt från {s.zone}",
                        "duration_min": round(s.duration_min * 0.7),
                    }))
                    modified = True
                else:
                    new_steps.append(s.model_copy(update={
                        "duration_min": round(s.duration_min * 0.7),
                    }))
            updates["workout_steps"] = new_steps
        if day.intervals_type == "WeightTraining":
            updates["intervals_type"] = "VirtualRide"
            updates["duration_min"]   = 30
            updates["workout_steps"]  = [WorkoutStep(duration_min=30, zone="Z1", description="Lätt spinning – deload")]
            updates["strength_steps"] = []
            updates["title"]          = f"{day.title} → Zwift Z1 (deload)"
            modified = True
        if modified:
            if "title" not in updates:
                updates["title"] = f"{day.title} [DELOAD -35%]"
            days[i] = day.model_copy(update=updates)
            changes.append(f"  {day.date}: {day.title} → deload-justerad")
    return days, changes


def enforce_motivation_state(days: list, motivation: dict) -> tuple:
    """
    Vid BURNOUT_RISK: sänker intensitet till max Z3 och volymen med 20%.
    Förebygger psykologisk utmattning och träningsavhopp.
    """
    if not motivation or motivation.get("state") != "BURNOUT_RISK":
        return days, []
    changes = [
        f"BURNOUT-RISK: Snittkänsla {motivation['avg_feel']:.1f}/5, "
        f"{motivation['weeks_declining']} veckor sjunkande. Sänker intensitet och volym."
    ]
    for i, day in enumerate(days):
        updates = {}
        if day.duration_min > 0:
            updates["duration_min"] = round(day.duration_min * 0.80)
        if day.workout_steps:
            new_steps = []
            for s in day.workout_steps:
                if s.zone in INTENSE:
                    new_steps.append(s.model_copy(update={
                        "zone": "Z2",
                        "description": f"[BURNOUT-VETO → Z2] {s.description}",
                    }))
                else:
                    new_steps.append(s)
            updates["workout_steps"] = new_steps
        if updates:
            updates["title"] = day.title + " [BURNOUT-VETO]"
            updates["vetoed"] = True
            days[i] = day.model_copy(update=updates)
            changes.append(f"  {day.date}: intensitet/volym sänkt")
    return days, changes


def enforce_per_sport_acwr_veto(days: list, per_sport: dict) -> tuple:
    """
    Om en sports ACWR > 1.5: konverterar pass av den sporten till en säkrare sport.
    Exempel: Run ACWR 1.6 → konvertera löppass till VirtualRide.
    """
    if not per_sport:
        return days, []
    danger_sports = {sport for sport, d in per_sport.items() if d.get("zone") == "DANGER"}
    if not danger_sports:
        return days, []
    changes = []
    for i, day in enumerate(days):
        if day.intervals_type not in danger_sports:
            continue
        sport = day.intervals_type
        fallback = "VirtualRide" if sport in ("Run", "RollerSki") else "Run"
        ratio = per_sport[sport]["ratio"]
        days[i] = day.model_copy(update={
            "intervals_type": fallback,
            "title": f"{day.title} [ACWR-VETO {sport}→{fallback}]",
            "description": (day.description +
                f"\n\n⚠️ Konverterad: {sport} ACWR {ratio:.2f} > 1.5 (hög skaderisk). "
                f"Tränar {fallback} istället."),
            "vetoed": True,
        })
        changes.append(f"ACWR-VETO: {day.date} {sport} → {fallback} (ratio {ratio:.2f})")
    return days, changes


def post_process(plan, hrv, budgets, locked, budget, activities, weather, athlete,
                 injury_note="", mesocycle=None, constraints=None, today_wellness=None,
                 rtp_status=None, per_sport_acwr_data=None, motivation=None,
                 phase=None, races=None, wellness=None, base_tss_by_date=None, horizon_days=None,
                 time_available_text=""):
    days = plan.days
    all_c = []

    # Sjukdom och RTP är de mest kritiska, kör dem först.
    if today_wellness:
        days, c = enforce_illness(days, today_wellness); all_c += c
        if c: # If sick, no need to apply other rules
            return plan.model_copy(update={"days": days}), all_c
            
    if rtp_status:
        days, c = enforce_rtp(days, rtp_status); all_c += c
        # After RTP, other rules might still apply to later days, so we continue

    days, c = enforce_locked(days, locked);            all_c += c
    days, c = enforce_hrv(days, hrv);                 all_c += c
    if motivation:
        days, c = enforce_motivation_state(days, motivation); all_c += c
    days, c2 = apply_injury_rules(days, injury_note);  all_c += c2
    if constraints:
        days, c = enforce_schedule_constraints(days, constraints); all_c += c
    if per_sport_acwr_data:
        days, c = enforce_per_sport_acwr_veto(days, per_sport_acwr_data); all_c += c
    days, c = enforce_sport_budget(days, budgets);     all_c += c
    days, c = enforce_hard_easy(days);                 all_c += c
    days, c = enforce_strength_limit(days, max_strength=2); all_c += c
    days, c = enforce_rollski_limit(days, max_per_week=1);  all_c += c
    if mesocycle:
        days, c = enforce_deload(days, mesocycle, athlete);  all_c += c
    days, c = enforce_tss(days, budget, athlete, base_tss_by_date=base_tss_by_date, horizon_days=horizon_days); all_c += c
    days     = ensure_warmup(days)
    days     = add_env_nutrition(days, weather, phase=phase, races=races, athlete=athlete, wellness=wellness)
    days     = enforce_min_duration(days)
    days, c = enforce_today_time_budget(days, time_available_text); all_c += c
    return plan.model_copy(update={"days": days}), all_c

# ══════════════════════════════════════════════════════════════════════════════
# MORGONCHECK
# ══════════════════════════════════════════════════════════════════════════════

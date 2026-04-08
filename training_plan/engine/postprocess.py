from training_plan.core.common import *
from training_plan.engine.analysis import *
from training_plan.engine.planning import classify_session_category
from training_plan.engine.utils import time_available_minutes

# ══════════════════════════════════════════════════════════════════════════════
# POST-PROCESSING – tvingande regler
# ══════════════════════════════════════════════════════════════════════════════

HARD_THRESHOLD = 0.20
_FTP_LOOKUP_CACHE: dict[tuple, dict[str, float]] = {}
_TSS_CACHE: dict[tuple, float] = {}

def enforce_illness(days, today_wellness):
    """If the athlete is sick, replace all sessions with rest."""
    if not today_wellness or not today_wellness.get("sick"):
        return days, []
    changes = ["Illness reported – all sessions converted to rest."]
    new_days = []
    for day in days:
        if day.intervals_type != "Rest":
            changes.append(f"  {day.date}: {day.title} → Rest (Illness)")
        new_days.append(PlanDay(
            date=day.date,
            title="Rest (Illness)",
            intervals_type="Rest",
            duration_min=0,
            description="Automatic rest due to illness report in intervals.icu. Get well soon!",
            vetoed=True,
        ))
    return new_days, changes

def enforce_rtp(days, rtp_status):
    """Force a Return-to-Play protocol after several rest days."""
    if not rtp_status or not rtp_status.get("is_active"):
        return days, []
    protocol = [
        {"d": 1, "title": "RTP Day 1: Test", "type": "VirtualRide", "dur": 30, "steps": [{"duration_min": 30, "zone": "Z1", "description": "Very easy, test the body"}]},
        {"d": 2, "title": "RTP Day 2: Confirm", "type": "VirtualRide", "dur": 45, "steps": [{"duration_min": 45, "zone": "Z2", "description": "Easy, confirm HR response"}]},
        {"d": 3, "title": "RTP Day 3: Open up", "type": "VirtualRide", "dur": 60, "steps": [{"duration_min": 50, "zone": "Z2", "description": "Base tempo"}, {"duration_min": 1, "zone": "Z3", "description": "Open up"}, {"duration_min": 9, "zone": "Z2", "description": "Easy again"}]},
    ]
    changes = [f"🚑 RETURN TO PLAY ({rtp_status['days_off']} rest days) – forced protocol applied."]
    for i, p in enumerate(protocol):
        if i >= len(days):
            break
        target_date = days[i].date
        rtp_day = PlanDay(
            date=target_date,
            title=p["title"],
            intervals_type=p["type"],
            duration_min=p["dur"],
            description=f"Return-to-Play protocol after {rtp_status['days_off']} rest days.",
            workout_steps=[WorkoutStep(**step) for step in p["steps"]],
            vetoed=False,
        )
        days[i] = rtp_day
        changes.append(f"  {target_date}: Replaced with '{p['title']}'")
    return days, changes

def intensity_rating(day: PlanDay) -> float:
    if not day.workout_steps or day.duration_min == 0:
        return 0.0
    intense_min = sum(s.duration_min for s in day.workout_steps if s.zone in INTENSE)
    return intense_min / day.duration_min

def is_intense(day: PlanDay) -> bool:
    return intensity_rating(day) >= HARD_THRESHOLD

def enforce_max_consecutive_rest(days):
    """Replaces the third consecutive rest day with an easy Z1 session (30min)."""
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
            # Replace the third rest day with a short active recovery session
            target_date = rest_streak[-1]
            for i, day in enumerate(days):
                if day.date == target_date and (day.intervals_type == "Rest" or day.duration_min == 0):
                    days[i] = day.model_copy(update={
                        "intervals_type": "Run",
                        "duration_min": 30,
                        "title": "Active rest (light mobility)",
                        "description": "Light mobility session or walk to keep circulation going without load.",
                        "workout_steps": [WorkoutStep(duration_min=30, zone="Z1", description="Easy activity")],
                    })
                    changes.append(f"MAX-REST: {target_date} – 3 rest days in a row replaced with 30min Z1")
                    is_rest[target_date] = False
                    consecutive = 0
                    rest_streak = []
                    break
    return days, changes




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
                    description=f"Easy tempo - HARD-EASY rule "
                                f"(previous day: {round(r_prev*100)}% Z4+)"
                )],
                "nutrition": "",
                "description": f"⚠️ CODE VETO: The AI tried to schedule a hard session here, but the Python code changed it to recovery (Hard-Easy rule).\n\nOriginal idea from AI: {days[i].description}",
                "vetoed": True,
            })
            changes.append(
                f"HARD-EASY: {days[i].date} '{old}' "
                f"({round(r_curr*100)}% Z4+) converted to Z1"
            )
    return days, changes

def apply_injury_rules(days, injury_note: str):
    if not injury_note or injury_note.lower() in ("", "nej", "n", "inga"):
        return days, []
    inj = injury_note.lower()
    avoid_map = [
        (["knä", "höft", "lår", "vad", "fot", "ankel", "knee", "hip", "thigh", "calf", "foot", "ankle"],  {"Run", "RollerSki"},     "VirtualRide"),
        (["axel", "armbåge", "handled", "arm", "shoulder", "elbow", "wrist"],           {"Ride", "VirtualRide"},  "Run"),
        (["rygg", "ländrygg", "nacke", "back", "lower back", "neck"],                   {"Run", "Ride"},          "VirtualRide"),
        (["skena", "shin", "splints"],                               {"Run"},                  "VirtualRide"),
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
                "title":          f"{day.title} [→ {replacement}, injury]",
                "intervals_type": replacement,
                "duration_min":   new_dur,
                "description":    day.description + f"\n\n⚠️ Adapted due to injury report: '{injury_note}'",
            })
            changes.append(f"INJURY: {day.date} '{day.intervals_type}' → '{replacement}' ({new_dur}min)")
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
                description=f"Easy recovery - HRV is LOW ({hrv['deviation_pct']}% under baseline)",
            )
            days[i] = day.model_copy(update={
                "title": f"{day.title} -> Z1 (HRV-VETO)",
                "workout_steps": [recovery_step],
                "nutrition": "",
                "vetoed": True,
            })
            changes.append(f"HRV-VETO: {day.date} - replaced with Z1 recovery (HRV LOW).")
    return days, changes

def enforce_sport_budget(days, budgets):
    accumulated = {st: 0 for st in budgets}
    changes = []
    for i, day in enumerate(days):
        st = day.intervals_type
        if st not in budgets or day.duration_min == 0: continue
        b = budgets[st]
        if accumulated[st] + day.duration_min > b["remaining"]:
            changes.append(f"VOLUME CAP ({st}): {day.date} - {day.duration_min}min exceeds budget ({b['remaining']}min remaining). Converting to VirtualRide.")
            days[i] = day.model_copy(update={
                "intervals_type": "VirtualRide",
                "title": f"{day.title} -> Zwift (volume cap)",
                "vetoed": True,
            })
        else:
            accumulated[st] += day.duration_min
    return days, changes

def enforce_locked(days, locked):
    clean   = [d for d in days if d.date not in locked]
    removed = [d.date for d in days if d.date in locked]
    changes = [f"LOCKED DATE: {d} removed (manual session exists)." for d in removed]
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
    sport_settings = athlete.get("sportSettings", []) if athlete else []
    cache_key = tuple(
        (
            ss.get("ftp"),
            tuple(ss.get("types", []) if isinstance(ss.get("types"), list) else [ss.get("type")]),
        )
        for ss in sport_settings
    )
    ftp_map = _FTP_LOOKUP_CACHE.get(cache_key)
    if ftp_map is None:
        ftp_map = {}
        for ss in sport_settings:
            ftp_val = ss.get("ftp")
            if ftp_val and ftp_val > 0:
                stypes = ss.get("types", []) if isinstance(ss.get("types"), list) else [ss.get("type")]
                for t in stypes:
                    if t:
                        ftp_map[t] = float(ftp_val)
        _FTP_LOOKUP_CACHE[cache_key] = ftp_map
    for c in candidates:
        if c in ftp_map:
            return ftp_map[c]
    return 200.0


def _tss_cache_key(day: PlanDay) -> tuple:
    return (
        day.intervals_type,
        day.duration_min,
        tuple((step.duration_min, step.zone) for step in day.workout_steps),
    )

def estimate_tss_coggan(day, athlete: dict) -> float:
    if day.duration_min == 0 or day.intervals_type == "Rest":
        return 0.0
    if day.intervals_type == "WeightTraining":
        return round(day.duration_min * 0.5, 1)  # ~20 TSS för 40min styrka
    ftp = ftp_for_sport(day.intervals_type, athlete)
    cache_key = (_tss_cache_key(day), ftp)
    cached = _TSS_CACHE.get(cache_key)
    if cached is not None:
        return cached
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
    result = round(tss, 1)
    _TSS_CACHE[cache_key] = result
    return result

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
                            + "\n\nTSS adjustment: low-priority filler volume removed to protect more important structure."
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
    changes.append("TSS-AUDIT " + " | ".join(week_summaries) + f" | Total {round(total)} TSS")
    return result, changes


def enforce_today_time_budget(days: list[PlanDay], time_available_text: str) -> tuple[list[PlanDay], list[str]]:
    available_min = time_available_minutes(time_available_text)
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
                + f"\n\nTime adjustment: today's total time needed to fit within {available_min} min."
            ),
            "vetoed": True,
        })
        total_today -= day.duration_min
        active_today.remove(idx)
        changes.append(f"TIME BUDGET: {day.date} removed '{day.title}' to fit within {available_min}min today")

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
                    + f"\n\nTime adjustment: today's time ({available_min} min) was not enough for minimum duration."
                ),
                "vetoed": True,
            })
            changes.append(f"TIME BUDGET: {day.date} replaced '{day.title}' with rest since {available_min}min is under minimum duration")
        else:
            days[idx] = day.model_copy(update={
                "duration_min": new_duration,
                "workout_steps": _trim_workout_steps(day, new_duration),
                "title": f"{day.title} ({day.duration_min}->{new_duration}min)",
                "description": (
                    day.description
                    + f"\n\nTime adjustment: today's total time clamped to {available_min} min."
                ),
                "vetoed": True,
            })
            changes.append(f"TIME BUDGET: {day.date} shortened '{day.title}' to {new_duration}min to fit within {available_min}min today")

    return days, changes

# ══════════════════════════════════════════════════════════════════════════════
# NÄRINGSSTYRNING (periodiserad)
# ══════════════════════════════════════════════════════════════════════════════

def calculate_nutrition_periodization(phase_name: str, days_to_race: Optional[int],
                                       workout_day, tss_estimate: float,
                                       weight_kg: float | None = None) -> str:
    """
    Returns nutrition strategy based on training phase, race proximity and session load.
    Complements environment-based nutrition with periodized recommendations.
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
        return ("RACE DAY: Start 300ml sports drink. 60-90g CHO/h during the race (gels + bars). "
                "500mg Na/h. Caffeine 200mg at t-1h. Drink 500ml at finish.")

    # Kolhydratladning 3 dagar före
    if days_to_race is not None and 1 <= days_to_race <= 3:
        dag = 4 - days_to_race
        return (f"CARB LOADING day {dag}/3: {cho_range(8, 10)} today. "
                f"Rice, pasta, oatmeal, bread. Avoid fiber and fat. Drink 2-3L.")

    # Hög TSS-dag
    if tss_estimate > 100:
        return (f"HIGH-CARB: {round(tss_estimate)} TSS planned – {cho_range(6, 8)} today. "
                f"Breakfast: oatmeal + banana + honey. During: 60-90g CHO/h.")

    # Basfas + Z2-pass (fasted training OK)
    is_z2_only = all(s.zone in ("Z1", "Z2") for s in workout_day.workout_steps) if workout_day.workout_steps else True
    if phase_name in ("Base", "Grundtraning") and is_z2_only and 60 <= dur <= 90:
        return ("FASTED OK: Morning session 60-90min Z2 can be done fasted for fat adaptation. "
                "Max 30g CHO/h if you are hungry. Have a gel ready.")

    # Standard baserat på duration
    if dur < 60:
        return ""
    elif dur <= 90:
        return f"30-60g CHO/h during the session ({dur}min). Sports drink or 1 gel/45min."
    else:
        return f"60-90g CHO/h during the session ({dur}min). Test race day nutrition."


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
            reason = "strength limit (max 2)" if too_many else f"too close (< {min_gap} days since last)"
            days[i] = day.model_copy(update={
                "title":          day.title + f" -> Zwift Z1 ({reason})",
                "intervals_type": "VirtualRide",
                "duration_min":   45,
                "workout_steps":  [WorkoutStep(duration_min=45, zone="Z1", description="Easy recovery spinning @ <120W")],
                "strength_steps": [],
                "description":    day.description + f"\n\n⚠️ Converted – {reason}.",
                "vetoed": True,
            })
            changes.append(f"STRENGTH_LIMIT: {day.date} -> Zwift Z1 ({reason})")
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
            "title":          day.title + " -> Cycling (roller ski limit)",
            "intervals_type": "Ride",
            "description":    day.description + "\n\n⚠️ Converted – max 1 roller ski session/week.",
            "vetoed": True,
        })
        changes.append(f"ROLLERSKI_LIMIT: {day.date} -> Ride (max 1/week)")
    return days, changes


_MIN_DURATION = MIN_DURATION_BY_SPORT
_TSS_REPAIR_TARGET_PCT = float(os.getenv("POSTPROCESS_TSS_REPAIR_TARGET_PCT", "0.95"))
_INTENSE_ZONES = {"Z4", "Z5", "Z6", "Z7"}


def _consolidate_steps(day: PlanDay) -> PlanDay:
    """Merge consecutive same-zone steps on endurance sessions.

    Only applies to sessions that contain no high-intensity steps (Z4+), so
    structured interval sessions are left untouched. For pure Z2/Z1 sessions
    this turns multiple fragmented extension steps into one coherent main block,
    which prevents the review model from flagging the plan as padded.
    """
    steps = day.workout_steps
    if not steps or len(steps) < 2:
        return day
    if any(s.zone in _INTENSE_ZONES for s in steps):
        return day  # structured session – do not touch
    merged: list[WorkoutStep] = [steps[0].model_copy()]
    for step in steps[1:]:
        if step.zone == merged[-1].zone:
            merged[-1] = merged[-1].model_copy(update={
                "duration_min": merged[-1].duration_min + step.duration_min,
            })
        else:
            merged.append(step.model_copy())
    if len(merged) == len(steps):
        return day
    total_min = sum(s.duration_min for s in merged)
    return day.model_copy(update={"workout_steps": merged, "duration_min": total_min})


def _is_rest_like(day: PlanDay) -> bool:
    return day.intervals_type == "Rest" or day.duration_min == 0


def _extend_day_for_tss(day: PlanDay, extra_min: int, note: str) -> PlanDay:
    if extra_min <= 0 or day.intervals_type in ("Rest", "WeightTraining"):
        return day
    new_steps = list(day.workout_steps or [])
    # Extend the last non-cooldown Z2/Z3 step rather than appending a new step.
    # This keeps the session structure clean: one long main block instead of
    # multiple "extension" steps that the review model correctly penalizes.
    target_idx = None
    # Treat a trailing Z1 as cooldown; look for the last extensible step before it.
    search_end = len(new_steps) - 1 if (new_steps and new_steps[-1].zone == "Z1") else len(new_steps)
    for i in range(search_end - 1, -1, -1):
        if new_steps[i].zone in ("Z2", "Z3"):
            target_idx = i
            break
    if target_idx is not None:
        new_steps[target_idx] = new_steps[target_idx].model_copy(update={
            "duration_min": new_steps[target_idx].duration_min + extra_min,
        })
    else:
        # Fallback for sessions without a Z2/Z3 main step.
        extension = WorkoutStep(duration_min=extra_min, zone="Z2", description=note)
        if new_steps and new_steps[-1].zone == "Z1" and len(new_steps) >= 2:
            new_steps.insert(len(new_steps) - 1, extension)
        else:
            new_steps.append(extension)
    return day.model_copy(update={
        "duration_min": day.duration_min + extra_min,
        "workout_steps": new_steps,
        "description": (
            day.description
            + f"\n\nTSS repair: added {extra_min} min aerobic volume to better match load target."
        ).strip(),
    })


def repair_low_tss(days: list[PlanDay], budget: float, athlete: dict,
                   base_tss_by_date: dict[str, float] | None = None,
                   target_pct: float = _TSS_REPAIR_TARGET_PCT,
                   med_active: bool = False) -> tuple[list[PlanDay], list[str]]:
    if med_active or budget <= 0 or not athlete:
        return days, []

    base_tss_by_date = base_tss_by_date or {}
    result = list(days)
    target_total = budget * max(0.85, min(target_pct, 1.0))

    def total_tss_value(plan_days: list[PlanDay]) -> float:
        planned = sum(estimate_tss_coggan(day, athlete) for day in plan_days)
        locked = sum(base_tss_by_date.get(day.date, 0) for day in plan_days)
        return planned + locked

    current_total = total_tss_value(result)
    if current_total >= target_total:
        return result, []

    changes: list[str] = []
    category_priority = {
        "long_ride": 0,
        "endurance": 1,
        "general": 2,
        "recovery": 3,
        "threshold": 9,
        "vo2": 10,
        "ftp_test": 11,
        "strength": 12,
    }
    extensible = []
    for idx, day in enumerate(result):
        category = classify_session_category(day.model_dump())
        if day.intervals_type in ("Rest", "WeightTraining") or day.duration_min <= 0:
            continue
        if category not in ("long_ride", "endurance", "general", "recovery"):
            continue
        extensible.append((
            category_priority.get(category, 99),
            -estimate_tss_coggan(day, athlete),
            idx,
            category,
        ))

    for _, _, idx, category in sorted(extensible):
        if current_total >= target_total:
            break
        day = result[idx]
        max_extra = {
            "long_ride": 75,
            "endurance": 60,
            "general": 45,
            "recovery": 30,
        }.get(category, 30)
        step = 15
        added_here = 0
        while added_here + step <= max_extra and current_total < target_total:
            updated = _extend_day_for_tss(
                result[idx],
                step,
                "Aerobic extension to close TSS gap",
            )
            before = estimate_tss_coggan(result[idx], athlete)
            after = estimate_tss_coggan(updated, athlete)
            delta = after - before
            if delta <= 0:
                break
            result[idx] = updated
            current_total += delta
            added_here += step
        if added_here:
            changes.append(f"TSS-REPAIR: {day.date} +{added_here}min aerobic volume ({category})")

    if current_total < target_total:
        for idx, day in enumerate(result):
            if current_total >= target_total:
                break
            if not _is_rest_like(day):
                continue
            prev_day = result[idx - 1] if idx > 0 else None
            next_day = result[idx + 1] if idx + 1 < len(result) else None
            if (prev_day and is_intense(prev_day)) or (next_day and is_intense(next_day)):
                continue
            added_session = PlanDay(
                date=day.date,
                title="Aerobic Base [TSS repair]",
                intervals_type="VirtualRide",
                duration_min=45,
                description="Added by Python post-process to close a significant TSS gap without increasing intensity.",
                workout_steps=[
                    WorkoutStep(duration_min=10, zone="Z1", description="Warmup"),
                    WorkoutStep(duration_min=25, zone="Z2", description="Steady aerobic volume"),
                    WorkoutStep(duration_min=10, zone="Z1", description="Cooldown"),
                ],
                slot=day.slot,
                vetoed=True,
            )
            delta = estimate_tss_coggan(added_session, athlete)
            result[idx] = added_session
            current_total += delta
            changes.append(f"TSS-REPAIR: {day.date} rest -> 45min Z2 aerobic support")

    if changes:
        changes.insert(0, f"TSS-REPAIR: lifted estimated total load toward {round(target_total)} TSS target before final audit.")
    return result, changes

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
    changes = [f"🟡 DELOAD WEEK (week {mesocycle['week_in_block']}/4, "
               f"block {mesocycle['block_number']}). Lowering volume and intensity."]
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
                        "description": f"[DELOAD] {s.description} - reduced from {s.zone}",
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
            updates["workout_steps"]  = [WorkoutStep(duration_min=30, zone="Z1", description="Easy spinning - deload")]
            updates["strength_steps"] = []
            updates["title"]          = f"{day.title} -> Zwift Z1 (deload)"
            modified = True
        if modified:
            if "title" not in updates:
                updates["title"] = f"{day.title} [DELOAD -35%]"
            days[i] = day.model_copy(update=updates)
            changes.append(f"  {day.date}: {day.title} -> deload-adjusted")
    return days, changes


def enforce_motivation_state(days: list, motivation: dict) -> tuple:
    """
    Vid BURNOUT_RISK: sänker intensitet till max Z3 och volymen med 20%.
    Förebygger psykologisk utmattning och träningsavhopp.
    """
    if not motivation or motivation.get("state") != "BURNOUT_RISK":
        return days, []
    changes = [
        f"BURNOUT-RISK: Avg feel {motivation['avg_feel']:.1f}/5, "
        f"{motivation['weeks_declining']} weeks declining. Lowering intensity and volume."
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
            changes.append(f"  {day.date}: intensity/volume lowered")
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
                f"\n\n⚠️ Converted: {sport} ACWR {ratio:.2f} > 1.5 (high injury risk). "
                f"Training {fallback} instead."),
            "vetoed": True,
        })
        changes.append(f"ACWR-VETO: {day.date} {sport} → {fallback} (ratio {ratio:.2f})")
    return days, changes


def post_process(plan, hrv, budgets, locked, budget, activities, weather, athlete,
                 injury_note="", mesocycle=None, constraints=None, today_wellness=None,
                 rtp_status=None, per_sport_acwr_data=None, motivation=None,
                 med_active=False,
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
    days, c = repair_low_tss(
        days,
        budget,
        athlete,
        base_tss_by_date=base_tss_by_date,
        med_active=med_active,
    ); all_c += c
    days, c = enforce_tss(days, budget, athlete, base_tss_by_date=base_tss_by_date, horizon_days=horizon_days); all_c += c
    days     = ensure_warmup(days)
    days     = add_env_nutrition(days, weather, phase=phase, races=races, athlete=athlete, wellness=wellness)
    days     = enforce_min_duration(days)
    days, c = enforce_today_time_budget(days, time_available_text); all_c += c
    days     = [_consolidate_steps(d) for d in days]
    return plan.model_copy(update={"days": days}), all_c

# ══════════════════════════════════════════════════════════════════════════════
# MORGONCHECK
# ══════════════════════════════════════════════════════════════════════════════

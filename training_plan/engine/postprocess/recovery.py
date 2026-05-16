from training_plan.core.common import *
from training_plan.engine.planning import classify_session_category
from training_plan.engine.utils import time_available_minutes

_MIN_DURATION = MIN_DURATION_BY_SPORT
_MAX_ROLLSKI_PER_WEEK = int(os.getenv("MAX_ROLLSKI_PER_WEEK", "1"))
_MAX_STRENGTH_PER_PLAN = int(os.getenv("MAX_STRENGTH_PER_PLAN", "2"))
_MIN_STRENGTH_GAP_DAYS = int(os.getenv("MIN_STRENGTH_GAP_DAYS", "2"))

HARD_THRESHOLD = 0.20
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


def _fit_workout_steps_to_duration(day: PlanDay, new_duration: int) -> list[WorkoutStep]:
    """Resize workout steps to match a new session duration without breaking structure."""
    if new_duration <= 0 or not day.workout_steps:
        return []

    current_total = sum(step.duration_min for step in day.workout_steps)
    if current_total >= new_duration:
        return _trim_workout_steps(day, new_duration)

    steps = [step.model_copy() for step in day.workout_steps]
    extra = new_duration - current_total

    # Prefer extending the main aerobic/quality block rather than the warmup/cooldown.
    candidate_indices = [
        idx for idx, step in enumerate(steps)
        if step.zone not in {"Z1"} and step.zone not in INTENSE
    ]
    if not candidate_indices:
        candidate_indices = [idx for idx, step in enumerate(steps) if step.zone not in {"Z1"}]
    if not candidate_indices:
        candidate_indices = [len(steps) - 1]

    target_idx = max(candidate_indices, key=lambda idx: steps[idx].duration_min)
    target_step = steps[target_idx]
    steps[target_idx] = target_step.model_copy(update={"duration_min": target_step.duration_min + extra})
    return steps


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



def enforce_strength_limit(days, max_strength=None, min_gap=None):
    max_strength = max_strength if max_strength is not None else _MAX_STRENGTH_PER_PLAN
    min_gap      = min_gap      if min_gap      is not None else _MIN_STRENGTH_GAP_DAYS
    fallback     = _pick_fallback_sport(avoid="WeightTraining")
    changes = []
    strength_count = 0
    last_strength_idx = -99
    for i, day in enumerate(days):
        if day.intervals_type != "WeightTraining": continue
        too_close  = (i - last_strength_idx) < min_gap
        too_many   = strength_count >= max_strength
        if too_many or too_close:
            reason = f"strength limit (max {max_strength})" if too_many else f"too close (< {min_gap} days since last)"
            days[i] = day.model_copy(update={
                "title":          day.title + f" -> {fallback} Z1 ({reason})",
                "intervals_type": fallback,
                "duration_min":   45,
                "workout_steps":  [WorkoutStep(duration_min=45, zone="Z1", description="Easy aerobic recovery – no intensity")],
                "strength_steps": [],
                "description":    day.description + f"\n\n⚠️ Converted – {reason}.",
                "vetoed": True,
            })
            changes.append(f"STRENGTH_LIMIT: {day.date} -> {fallback} Z1 ({reason})")
        else:
            strength_count  += 1
            last_strength_idx = i
    return days, changes

def enforce_rollski_limit(days, max_per_week=None):
    max_per_week = max_per_week if max_per_week is not None else _MAX_ROLLSKI_PER_WEEK
    fallback     = _pick_fallback_sport(avoid="RollerSki")
    changes = []
    rollski_days = [(i, day) for i, day in enumerate(days) if day.intervals_type == "RollerSki"]
    seen_weeks: dict[int, int] = {}
    to_convert = set()
    for i, day in rollski_days:
        week = datetime.strptime(day.date, "%Y-%m-%d").isocalendar()[1]
        seen_weeks[week] = seen_weeks.get(week, 0) + 1
        if seen_weeks[week] > max_per_week:
            to_convert.add(i)
    for i in to_convert:
        day = days[i]
        days[i] = day.model_copy(update={
            "title":          day.title + f" -> {fallback} (roller ski limit)",
            "intervals_type": fallback,
            "description":    day.description + f"\n\n⚠️ Converted – max {max_per_week} roller ski session(s)/week.",
            "vetoed": True,
        })
        changes.append(f"ROLLERSKI_LIMIT: {day.date} -> {fallback} (max {max_per_week}/week)")
    return days, changes




# Fallback sport when a sport is over ACWR limit or exceeds rollski/strength limits.
# Picks lowest injury-risk available sport, with env override.
def _pick_fallback_sport(avoid: str | None = None) -> str:
    override = os.getenv("FALLBACK_SPORT", "").strip()
    if override:
        return override
    risk_order = {"low": 0, "medium": 1, "high": 2}
    candidates = [
        s for s in SPORTS
        if s["intervals_type"] not in ("WeightTraining", "Rest", avoid)
    ]
    if not candidates:
        return "VirtualRide"
    return min(candidates, key=lambda s: risk_order.get(s.get("injury_risk", "medium"), 1))["intervals_type"]




def enforce_min_duration(days: list) -> list:
    """Klampar duration till minimum per sport – hoppar över vetade/återhämtningspass."""
    for i, day in enumerate(days):
        if day.vetoed:
            continue
        category = classify_session_category(day.model_dump())
        if category == "recovery":
            continue
        min_dur = _MIN_DURATION.get(day.intervals_type)
        if min_dur and 0 < day.duration_min < min_dur:
            updates = {"duration_min": min_dur}
            if day.workout_steps:
                updates["workout_steps"] = _fit_workout_steps_to_duration(day, min_dur)
            days[i] = day.model_copy(update=updates)
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
        sport    = day.intervals_type
        fallback = _pick_fallback_sport(avoid=sport)
        ratio    = per_sport[sport]["ratio"]
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



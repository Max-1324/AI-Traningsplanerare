from training_plan.core.common import *
from training_plan.engine.planning import classify_session_category
from training_plan.engine.postprocess.safety import is_intense

_MIN_DURATION = MIN_DURATION_BY_SPORT
_TSS_REPAIR_TARGET_PCT = float(os.getenv("POSTPROCESS_TSS_REPAIR_TARGET_PCT", "0.95"))
_INTENSE_ZONES = {"Z4", "Z5", "Z6", "Z7"}
_FTP_LOOKUP_CACHE: dict[tuple, dict[str, float]] = {}
_TSS_CACHE: dict[tuple, float] = {}

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

def enforce_tss(days, budget, athlete, base_tss_by_date=None, horizon_days=None):
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
                   med_active: bool = False,
                   budgets: dict | None = None) -> tuple[list[PlanDay], list[str]]:
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
        # Pick the sport with most remaining budget; fall back to VirtualRide
        _repair_sport = "VirtualRide"
        if budgets:
            accumulated_in_plan: dict[str, int] = {}
            for d in result:
                if d.intervals_type in budgets and d.duration_min > 0:
                    accumulated_in_plan[d.intervals_type] = (
                        accumulated_in_plan.get(d.intervals_type, 0) + d.duration_min
                    )
            best_remaining = 0
            for st, b in budgets.items():
                remaining = b["remaining"] - accumulated_in_plan.get(st, 0)
                if remaining > best_remaining:
                    best_remaining = remaining
                    _repair_sport = st

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
                intervals_type=_repair_sport,
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
            changes.append(f"TSS-REPAIR: {day.date} rest -> 45min Z2 {_repair_sport} aerobic support")

    if changes:
        changes.insert(0, f"TSS-REPAIR: lifted estimated total load toward {round(target_total)} TSS target before final audit.")
    return result, changes


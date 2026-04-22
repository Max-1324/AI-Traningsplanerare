from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime

from training_plan.core.catalogs import INTENSE, VALID_ZONES, ZONE_CANONICAL, ZONE_INTENSITY, ZONE_ORDER
from training_plan.core.models import (
    AIPlan,
    PlanDay,
    PlanReview,
    PlanScores,
    PlanValidationResult,
    ReviewDimension,
    ReviewFix,
    StrengthStep,
    WorkoutStep,
)
from training_plan.engine.planning import classify_session_category
from training_plan.engine.postprocess import HARD_THRESHOLD, estimate_tss_coggan

_SLOT_ORDER = {"AM": 0, "MAIN": 1, "PM": 2}
_HARD_CATEGORIES = {"threshold", "vo2", "ftp_test"}
# Zone constants imported from training_plan.core.catalogs:
#   ZONE_INTENSITY, ZONE_CANONICAL, ZONE_ORDER, VALID_ZONES
_HARD_VETO_MARKERS = (
    "HARD-EASY",
    "HRV-VETO",
    "ACWR-VETO",
    "TIME BUDGET",
    "TIDSBUDGET",
    "STRENGTH_LIMIT",
    "STYRKEGRÄNS",
    "RULLSKIDSGRÄNS",
    "TSS-DEFICIT VETO",
    "TSS-UNDERSKOTT VETO",
    "VOLYMSPÄRR",
    "VOLYMSPARR",
    "VOLYMSPAR",
    "TAK V",
    "BURNOUT-VETO",
)
_ALLOWED_AUTOFIX_MARKERS = (
    "ILLNESS",
    "LOCKED DATE",
    "RETURN TO PLAY",
)
_OPTIONAL_REQUIREMENT_MARKERS = (
    "if recovery allows",
    "if readiness allows",
    "if fatigue allows",
    "if tolerated",
    "if freshness allows",
    "om återhämtning tillåter",
    "om formen tillåter",
)


def _parse_iso_date(value: str):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except Exception:
        return None


def _slot_rank(slot: str) -> int:
    return _SLOT_ORDER.get(slot, 99)


def _day_sort_key(day) -> tuple:
    parsed = _parse_iso_date(day.date) or date.max
    return parsed, _slot_rank(day.slot), day.title


def _normalize_zone(zone: str) -> str:
    return str(zone or "").strip().upper()


def _max_zone_order(day) -> int:
    highest = 0
    for step in day.workout_steps:
        highest = max(highest, ZONE_ORDER.get(_normalize_zone(step.zone), 0))
    return highest


def _intensity_ratio(day) -> float:
    if not day.workout_steps or day.duration_min <= 0:
        return 0.0
    intense_min = sum(step.duration_min for step in day.workout_steps if _normalize_zone(step.zone) in INTENSE or _normalize_zone(step.zone) in {"Z4", "Z5", "Z6", "Z7", "Z4+", "Z5+"})
    return intense_min / max(day.duration_min, 1)


def _is_hard_veto_change(change: str) -> bool:
    text = (change or "").upper()
    if any(marker in text for marker in _ALLOWED_AUTOFIX_MARKERS):
        return False
    return any(marker in text for marker in _HARD_VETO_MARKERS)


def _weighted_plan_intensity(day: PlanDay) -> float | None:
    if not day.workout_steps:
        return None
    total = sum(step.duration_min for step in day.workout_steps) or 0
    if total <= 0:
        return None
    weighted = 0.0
    for step in day.workout_steps:
        weighted += step.duration_min * ZONE_INTENSITY.get(_canonical_zone(step.zone), 0.70)
    return round(weighted / total, 2)


def _plan_category(day: PlanDay) -> str:
    payload = {
        "name": day.title,
        "type": day.intervals_type,
        "moving_time": day.duration_min * 60,
        "icu_intensity": _weighted_plan_intensity(day),
    }
    return classify_session_category(payload)


def _canonical_zone(zone: str) -> str:
    return ZONE_CANONICAL.get(_normalize_zone(zone), _normalize_zone(zone))


def _is_conditionally_optional_requirement(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(marker in lowered for marker in _OPTIONAL_REQUIREMENT_MARKERS)


def _is_hard_day(day: PlanDay) -> bool:
    if day.intervals_type == "Rest" or day.duration_min <= 0:
        return False
    return _intensity_ratio(day) >= HARD_THRESHOLD or _plan_category(day) in _HARD_CATEGORIES


def _fit_steps_to_duration(steps: list[WorkoutStep], target_duration: int) -> list[WorkoutStep]:
    if target_duration <= 0 or not steps:
        return []
    result = [step.model_copy() for step in steps if step.duration_min > 0]
    if not result:
        return []

    total = sum(step.duration_min for step in result)
    if total == target_duration:
        return result

    if total > target_duration:
        overflow = total - target_duration
        for idx in range(len(result) - 1, -1, -1):
            if overflow <= 0:
                break
            step = result[idx]
            reducible = max(0, step.duration_min - 1)
            if reducible <= 0:
                continue
            reduction = min(reducible, overflow)
            result[idx] = step.model_copy(update={"duration_min": step.duration_min - reduction})
            overflow -= reduction
        result = [step for step in result if step.duration_min > 0]
        return result

    result[-1] = result[-1].model_copy(update={"duration_min": result[-1].duration_min + (target_duration - total)})
    return result


def _fallback_workout_steps(duration_min: int, main_zone: str = "Z2") -> list[WorkoutStep]:
    duration_min = max(int(duration_min or 0), 20)
    warm = 10 if duration_min >= 50 else 5
    cool = 10 if duration_min >= 40 else 5
    main = max(5, duration_min - warm - cool)
    return _fit_steps_to_duration(
        [
            WorkoutStep(duration_min=warm, zone="Z1", description="Warmup"),
            WorkoutStep(duration_min=main, zone=main_zone, description="Main block"),
            WorkoutStep(duration_min=cool, zone="Z1", description="Cooldown"),
        ],
        duration_min,
    )


def _default_strength_steps() -> list[StrengthStep]:
    return [
        StrengthStep(exercise="Split squat", sets=3, reps="8-10/leg", rest_sec=60, notes="Controlled"),
        StrengthStep(exercise="Romanian deadlift", sets=3, reps="8-10", rest_sec=60, notes="Hip hinge"),
        StrengthStep(exercise="Push-up", sets=3, reps="8-15", rest_sec=45, notes="Smooth tempo"),
        StrengthStep(exercise="Side plank", sets=3, reps="30-45s/side", rest_sec=30, notes="Brace the trunk"),
    ]


def _normalize_day_structure(day: PlanDay) -> tuple[PlanDay, list[str]]:
    actions: list[str] = []
    day = day.model_copy(deep=True)

    if day.intervals_type == "Rest" or day.duration_min <= 0:
        updates = {}
        if day.intervals_type != "Rest":
            updates["intervals_type"] = "Rest"
            actions.append(f"AUTO-REPAIR: {day.date} converted '{day.title}' to Rest because duration was non-positive.")
        if day.duration_min != 0:
            updates["duration_min"] = 0
        if day.workout_steps:
            updates["workout_steps"] = []
        if day.strength_steps:
            updates["strength_steps"] = []
        if updates:
            day = day.model_copy(update=updates)
        return day, actions

    if day.intervals_type == "WeightTraining":
        strength_steps = [step.model_copy() for step in day.strength_steps]
        if not strength_steps:
            strength_steps = _default_strength_steps()
            actions.append(f"AUTO-REPAIR: {day.date} added a default strength structure to '{day.title}'.")
        updates = {"strength_steps": strength_steps}
        if day.workout_steps:
            updates["workout_steps"] = []
            actions.append(f"AUTO-REPAIR: {day.date} cleared endurance workout steps from strength session '{day.title}'.")
        day = day.model_copy(update=updates)
        return day, actions

    steps = []
    for step in day.workout_steps:
        steps.append(step.model_copy(update={"zone": _canonical_zone(step.zone)}))
    if not steps:
        category = _plan_category(day)
        main_zone = "Z4" if category in {"threshold", "ftp_test"} else "Z5" if category == "vo2" else "Z2"
        steps = _fallback_workout_steps(day.duration_min, main_zone=main_zone)
        actions.append(f"AUTO-REPAIR: {day.date} rebuilt missing workout steps for '{day.title}'.")
    steps = _fit_steps_to_duration(steps, day.duration_min)
    if day.strength_steps:
        actions.append(f"AUTO-REPAIR: {day.date} removed strength steps from endurance session '{day.title}'.")
    return day.model_copy(update={"workout_steps": steps, "strength_steps": []}), actions


def _ensure_session_structure(day: PlanDay) -> tuple[PlanDay, list[str]]:
    if day.intervals_type in {"Rest", "WeightTraining"} or not day.workout_steps or day.duration_min < 30:
        return day, []
    actions: list[str] = []
    steps = [step.model_copy(deep=True) for step in day.workout_steps]
    warm_added = False
    cool_added = False
    warm = 10 if day.duration_min >= 50 else 5
    cool = 10 if day.duration_min >= 45 else 5

    if _canonical_zone(steps[0].zone) not in {"Z1", "Z2"}:
        steps.insert(0, WorkoutStep(duration_min=warm, zone="Z1", description="Warmup"))
        warm_added = True
    if _canonical_zone(steps[-1].zone) != "Z1":
        steps.append(WorkoutStep(duration_min=cool, zone="Z1", description="Cooldown"))
        cool_added = True

    if not warm_added and not cool_added:
        return day, actions

    steps = _fit_steps_to_duration(steps, day.duration_min)
    if warm_added:
        actions.append(f"AUTO-REPAIR: {day.date} inserted a warmup in '{day.title}'.")
    if cool_added:
        actions.append(f"AUTO-REPAIR: {day.date} inserted a cooldown in '{day.title}'.")
    return day.model_copy(update={"workout_steps": steps}), actions


def _infer_required_categories(review_context: dict | None) -> list[str]:
    review_context = review_context or {}
    phrases = []
    for key in ("block_objective", "development_needs", "minimum_effective_dose"):
        bucket = review_context.get(key) or {}
        phrases.extend(bucket.get("must_hit_sessions", []) or [])
    race_demands = review_context.get("race_demands") or {}
    phrases.extend(race_demands.get("must_have_sessions", []) or [])

    categories: list[str] = []
    for phrase in phrases:
        text = str(phrase or "").lower()
        if _is_conditionally_optional_requirement(text):
            continue
        category = None
        if "ftp" in text or "benchmark" in text or "test" in text:
            category = "ftp_test"
        elif "vo2" in text or "oxygen" in text:
            category = "vo2"
        elif "threshold" in text or "tröskel" in text or "sweet spot" in text:
            category = "threshold"
        elif "long" in text or "durability" in text or "progressive long ride" in text:
            category = "long_ride"
        elif "pacing" in text or "strict z2" in text or "endurance" in text:
            category = "endurance"
        elif "strength" in text:
            category = "strength"
        elif "quality" in text:
            category = "threshold"
        if category and category not in categories:
            categories.append(category)
    return categories[:4]


def _preferred_endurance_sport(days: list[PlanDay]) -> str:
    counts = Counter(day.intervals_type for day in days if day.intervals_type not in {"Rest", "WeightTraining"})
    for sport in ("Ride", "VirtualRide", "Run", "RollerSki"):
        if counts.get(sport):
            return sport
    return "VirtualRide"


def _template_day_for_category(day: PlanDay, category: str, fallback_sport: str) -> PlanDay:
    duration = max(day.duration_min, 45)
    if category == "long_ride":
        duration = max(day.duration_min, 180)
        sport = fallback_sport
        steps = _fallback_workout_steps(duration, main_zone="Z2")
        title = "Long ride"
        description = "Deterministic repair inserted a durability anchor session."
    elif category == "threshold":
        sport = fallback_sport
        duration = max(day.duration_min, 60)
        steps = _fit_steps_to_duration(
            [
                WorkoutStep(duration_min=15, zone="Z2", description="Warmup"),
                WorkoutStep(duration_min=8, zone="Z4", description="Threshold interval 1"),
                WorkoutStep(duration_min=3, zone="Z1", description="Recovery"),
                WorkoutStep(duration_min=8, zone="Z4", description="Threshold interval 2"),
                WorkoutStep(duration_min=3, zone="Z1", description="Recovery"),
                WorkoutStep(duration_min=8, zone="Z4", description="Threshold interval 3"),
                WorkoutStep(duration_min=15, zone="Z1", description="Cooldown"),
            ],
            duration,
        )
        title = "Threshold session"
        description = "Deterministic repair inserted a threshold stimulus required by the planning brief."
    elif category == "vo2":
        sport = fallback_sport
        duration = max(day.duration_min, 60)
        steps = _fit_steps_to_duration(
            [
                WorkoutStep(duration_min=15, zone="Z2", description="Warmup"),
                WorkoutStep(duration_min=3, zone="Z5", description="VO2 interval 1"),
                WorkoutStep(duration_min=3, zone="Z1", description="Recovery"),
                WorkoutStep(duration_min=3, zone="Z5", description="VO2 interval 2"),
                WorkoutStep(duration_min=3, zone="Z1", description="Recovery"),
                WorkoutStep(duration_min=3, zone="Z5", description="VO2 interval 3"),
                WorkoutStep(duration_min=3, zone="Z1", description="Recovery"),
                WorkoutStep(duration_min=3, zone="Z5", description="VO2 interval 4"),
                WorkoutStep(duration_min=3, zone="Z1", description="Recovery"),
                WorkoutStep(duration_min=3, zone="Z5", description="VO2 interval 5"),
                WorkoutStep(duration_min=15, zone="Z1", description="Cooldown"),
            ],
            duration,
        )
        title = "VO2 session"
        description = "Deterministic repair inserted a VO2 stimulus required by the planning brief."
    elif category == "ftp_test":
        sport = fallback_sport
        duration = max(day.duration_min, 60)
        steps = _fit_steps_to_duration(
            [
                WorkoutStep(duration_min=15, zone="Z2", description="Warmup"),
                WorkoutStep(duration_min=3, zone="Z4", description="Primer 1"),
                WorkoutStep(duration_min=3, zone="Z1", description="Recovery"),
                WorkoutStep(duration_min=3, zone="Z4", description="Primer 2"),
                WorkoutStep(duration_min=5, zone="Z1", description="Reset"),
                WorkoutStep(duration_min=20, zone="Z4", description="Benchmark effort"),
                WorkoutStep(duration_min=11, zone="Z1", description="Cooldown"),
            ],
            duration,
        )
        title = "FTP benchmark"
        description = "Deterministic repair inserted an FTP benchmark required by the planning brief."
    elif category == "strength":
        return PlanDay(
            date=day.date,
            title="Strength session",
            intervals_type="WeightTraining",
            duration_min=max(day.duration_min, 30),
            description="Deterministic repair inserted a simple strength maintenance session.",
            strength_steps=_default_strength_steps(),
            slot=day.slot,
            vetoed=day.vetoed,
        )
    else:
        sport = fallback_sport
        duration = max(day.duration_min, 60)
        steps = _fallback_workout_steps(duration, main_zone="Z2")
        title = "Endurance session"
        description = "Deterministic repair inserted an aerobic support session."

    return PlanDay(
        date=day.date,
        title=title,
        intervals_type=sport,
        duration_min=duration,
        description=description,
        nutrition=day.nutrition,
        workout_steps=steps,
        strength_steps=[],
        slot=day.slot,
        vetoed=day.vetoed,
    )


def _allowed_hard_days(review_context: dict | None, horizon_days: int) -> int:
    review_context = review_context or {}
    readiness = (review_context.get("readiness") or {}).get("score", 60)
    med = review_context.get("minimum_effective_dose") or {}
    is_deload = ((review_context.get("mesocycle") or {}).get("is_deload") is True)
    med_global = med.get("mode") == "ACTIVE" and med.get("scope") == "GLOBAL"
    # Hard-day tolerance should scale with horizon length. The athlete prompt already
    # limits "today/tomorrow" when freshness is low, so a 4-week plan cannot be
    # judged by the same absolute cap as a 3-7 day microcycle.
    base = max(1, round((max(horizon_days, 1) / 7.0) * 2))
    if readiness < 60 or med_global or is_deload:
        base -= 1
    if readiness < 50 and horizon_days <= 7:
        base = min(base, 1)
    return max(1, base)


def _replacement_respects_hard_rules(
    days: list[PlanDay],
    *,
    target_day: PlanDay,
    replacement: PlanDay,
    allowed_hard_days: int,
) -> bool:
    trial_days = [
        replacement if item.date == target_day.date and item.slot == target_day.slot else item
        for item in days
    ]

    hard_day_count = len({item.date for item in trial_days if _is_hard_day(item)})
    if hard_day_count > allowed_hard_days:
        return False

    sorted_trial = sorted(trial_days, key=_day_sort_key)
    for prev, curr in zip(sorted_trial, sorted_trial[1:]):
        prev_date = _parse_iso_date(prev.date)
        curr_date = _parse_iso_date(curr.date)
        if prev_date is None or curr_date is None:
            continue
        gap = (curr_date - prev_date).days
        if gap > 1:
            continue
        if prev.date == curr.date and prev.slot == "AM" and prev.intervals_type == "WeightTraining":
            continue
        if _is_hard_day(prev) and _is_hard_day(curr):
            return False
    return True


def repair_postprocessed_plan(
    plan: AIPlan,
    *,
    review_context: dict | None = None,
    validation: PlanValidationResult | None = None,
) -> tuple[AIPlan, list[str]]:
    review_context = review_context or {}
    actions: list[str] = []
    days = sorted([day.model_copy(deep=True) for day in plan.days], key=_day_sort_key)

    normalized_days: list[PlanDay] = []
    for day in days:
        repaired_day, day_actions = _normalize_day_structure(day)
        normalized_days.append(repaired_day)
        actions.extend(day_actions)

    days = normalized_days
    structured_days: list[PlanDay] = []
    for day in days:
        repaired_day, day_actions = _ensure_session_structure(day)
        structured_days.append(repaired_day)
        actions.extend(day_actions)
    days = structured_days

    locked_dates = set(review_context.get("locked_dates") or [])
    if locked_dates:
        repaired = []
        for day in days:
            if day.date in locked_dates and day.intervals_type != "Rest" and day.duration_min > 0:
                repaired.append(day.model_copy(update={
                    "title": f"{day.title} [locked date]",
                    "intervals_type": "Rest",
                    "duration_min": 0,
                    "workout_steps": [],
                    "strength_steps": [],
                }))
                actions.append(f"AUTO-REPAIR: {day.date} converted '{day.title}' to Rest because the date is locked.")
            else:
                repaired.append(day)
        days = repaired

    rtp_status = review_context.get("rtp_status") or {}
    if rtp_status.get("is_active"):
        max_duration = [30, 45, 60]
        max_zone = ["Z1", "Z2", "Z3"]
        repaired = []
        for idx, day in enumerate(days):
            if idx >= 3 or day.intervals_type in {"Rest", "WeightTraining"}:
                repaired.append(day)
                continue
            capped_steps = []
            for step in day.workout_steps:
                zone = _canonical_zone(step.zone)
                if ZONE_ORDER.get(zone, 0) > ZONE_ORDER.get(max_zone[idx], 0):
                    zone = max_zone[idx]
                capped_steps.append(step.model_copy(update={"zone": zone}))
            new_duration = min(day.duration_min, max_duration[idx])
            capped_steps = _fit_steps_to_duration(capped_steps, new_duration)
            if new_duration != day.duration_min or capped_steps != day.workout_steps:
                actions.append(f"AUTO-REPAIR: {day.date} capped RTP day {idx + 1} to {new_duration}min and {max_zone[idx]} max.")
            repaired.append(day.model_copy(update={"duration_min": new_duration, "workout_steps": capped_steps}))
        days = repaired

    target = review_context.get("training_frequency_target") or {}
    max_double_days = int(target.get("max_double_days", 99))
    if max_double_days < 99:
        by_date: dict[str, list[PlanDay]] = defaultdict(list)
        for day in days:
            if day.intervals_type != "Rest" and day.duration_min > 0:
                by_date[day.date].append(day)
        double_dates = [d for d, items in by_date.items() if len(items) > 1]
        while len(double_dates) > max_double_days:
            worst_date = max(
                double_dates,
                key=lambda item: len(by_date[item]),
            )
            candidates = sorted(
                [day for day in by_date[worst_date] if day.intervals_type != "WeightTraining"],
                key=lambda item: (_plan_category(item) not in {"recovery", "general", "endurance"}, item.duration_min),
            )
            if not candidates:
                break
            remove_day = candidates[0]
            new_days = []
            for day in days:
                if day.date == remove_day.date and day.slot == remove_day.slot:
                    new_days.append(day.model_copy(update={
                        "title": f"{day.title} [frequency repair]",
                        "intervals_type": "Rest",
                        "duration_min": 0,
                        "workout_steps": [],
                        "strength_steps": [],
                    }))
                    actions.append(f"AUTO-REPAIR: {day.date} removed extra session '{day.title}' to respect double-day limits.")
                else:
                    new_days.append(day)
            days = new_days
            by_date = defaultdict(list)
            for day in days:
                if day.intervals_type != "Rest" and day.duration_min > 0:
                    by_date[day.date].append(day)
            double_dates = [d for d, items in by_date.items() if len(items) > 1]

    hard_dates = []
    seen_hard = set()
    for day in days:
        if _is_hard_day(day) and day.date not in seen_hard:
            seen_hard.add(day.date)
            hard_dates.append(day.date)
    allowed_hard_days = _allowed_hard_days(review_context, len(days))
    if len(hard_dates) > allowed_hard_days:
        hard_candidates = sorted(
            [day for day in days if day.date in hard_dates],
            key=lambda item: (_plan_category(item) in {"ftp_test"}, _plan_category(item) in {"threshold", "vo2"}, item.date),
            reverse=True,
        )
        downgraded_dates: set[str] = set()
        for day in hard_candidates:
            if len(hard_dates) <= allowed_hard_days:
                break
            if day.date in downgraded_dates:
                continue
            category = _plan_category(day)
            if category == "ftp_test":
                continue
            replacement = day.model_copy(update={
                "title": "Aerobic support session",
                "description": (
                    "Deterministic repair downgraded this session to aerobic endurance "
                    "to respect hard-day limits."
                ),
                "workout_steps": _fallback_workout_steps(max(day.duration_min, 45), main_zone="Z2"),
            })
            days = [
                replacement if item.date == day.date and item.slot == day.slot else item
                for item in days
            ]
            downgraded_dates.add(day.date)
            hard_dates = []
            seen_hard = set()
            for item in days:
                if _is_hard_day(item) and item.date not in seen_hard:
                    seen_hard.add(item.date)
                    hard_dates.append(item.date)
            actions.append(f"AUTO-REPAIR: {day.date} downgraded '{day.title}' to aerobic endurance to reduce hard-day load.")

    required_categories = _infer_required_categories(review_context)
    if required_categories:
        fallback_sport = _preferred_endurance_sport(days)
        existing_categories = Counter(_plan_category(day) for day in days if day.intervals_type != "Rest")
        today_str = review_context.get("today") or date.today().isoformat()
        time_available_min = review_context.get("time_available_min")

        for category in required_categories:
            if existing_categories.get(category, 0) > 0:
                continue
            candidates = sorted(
                [
                    day for day in days
                    if day.date not in locked_dates
                    and not (day.date == today_str and isinstance(time_available_min, int) and time_available_min < 60)
                    and _plan_category(day) in {"recovery", "general", "endurance"}
                ] or [
                    day for day in days
                    if day.date not in locked_dates
                    and not (day.date == today_str and isinstance(time_available_min, int) and time_available_min < 60)
                    and day.intervals_type == "Rest"
                ],
                key=lambda item: (_plan_category(item) == "endurance", item.duration_min, item.date),
            )
            if not candidates:
                continue
            selected: tuple[PlanDay, PlanDay] | None = None
            for candidate in candidates:
                replacement = _template_day_for_category(candidate, category, fallback_sport)
                if category in _HARD_CATEGORIES and not _replacement_respects_hard_rules(
                    days,
                    target_day=candidate,
                    replacement=replacement,
                    allowed_hard_days=allowed_hard_days,
                ):
                    continue
                selected = (candidate, replacement)
                break
            if selected is None:
                continue
            target_day, replacement = selected
            days = [
                replacement if item.date == target_day.date and item.slot == target_day.slot else item
                for item in days
            ]
            existing_categories[category] += 1
            actions.append(f"AUTO-REPAIR: {target_day.date} replaced '{target_day.title}' with a deterministic {category} session to cover a must-hit stimulus.")

    return plan.model_copy(update={"days": days}), actions


def validate_postprocessed_plan(
    plan: AIPlan,
    *,
    athlete: dict | None = None,
    base_tss_by_date: dict[str, float] | None = None,
    tss_budget: float = 0,
    review_context: dict | None = None,
    postprocess_changes: list[str] | None = None,
) -> PlanValidationResult:
    review_context = review_context or {}
    postprocess_changes = postprocess_changes or []
    base_tss_by_date = base_tss_by_date or {}

    hard_failures: list[str] = []
    warnings: list[str] = []

    if not plan.days:
        hard_failures.append("Plan contains no scheduled days.")
        return PlanValidationResult(
            passed=False,
            summary="Deterministic validation failed: the plan is empty.",
            hard_failures=hard_failures,
            warnings=warnings,
        )

    sorted_days = sorted(plan.days, key=_day_sort_key)
    if [day.date for day in sorted_days] != [day.date for day in plan.days]:
        warnings.append("Plan days were not sorted chronologically before validation.")

    seen_slots: set[tuple[str, str]] = set()
    locked_dates = set(review_context.get("locked_dates") or [])

    for day in sorted_days:
        date_key = _parse_iso_date(day.date)
        if date_key is None:
            hard_failures.append(f"{day.title}: invalid date '{day.date}'.")
            continue

        slot_key = (day.date, day.slot)
        if slot_key in seen_slots:
            hard_failures.append(f"{day.date} {day.slot}: duplicate day/slot entry.")
        else:
            seen_slots.add(slot_key)

        if day.date in locked_dates and day.intervals_type != "Rest" and day.duration_min > 0:
            hard_failures.append(f"{day.date}: scheduled training on a locked/manual date.")

        if day.intervals_type == "Rest":
            if day.duration_min != 0:
                hard_failures.append(f"{day.date} '{day.title}': rest day must have 0 duration.")
            if day.workout_steps or day.strength_steps:
                hard_failures.append(f"{day.date} '{day.title}': rest day must not contain workout or strength steps.")
            continue

        if day.duration_min <= 0:
            hard_failures.append(f"{day.date} '{day.title}': training day must have positive duration.")

        if day.intervals_type == "WeightTraining":
            if day.workout_steps:
                hard_failures.append(f"{day.date} '{day.title}': strength session may not contain endurance workout steps.")
            if not day.strength_steps:
                warnings.append(f"{day.date} '{day.title}': strength session has no structured strength steps.")
            continue

        if day.strength_steps:
            hard_failures.append(f"{day.date} '{day.title}': endurance session may not contain strength steps.")

        if not day.workout_steps:
            hard_failures.append(f"{day.date} '{day.title}': training session is missing workout_steps.")
            continue

        total_step_duration = 0
        for step in day.workout_steps:
            total_step_duration += step.duration_min
            if _normalize_zone(step.zone) not in VALID_ZONES:
                hard_failures.append(f"{day.date} '{day.title}': unsupported zone '{step.zone}'.")

        if abs(total_step_duration - day.duration_min) > 5:
            hard_failures.append(
                f"{day.date} '{day.title}': workout step durations sum to {total_step_duration} min, "
                f"but session duration is {day.duration_min} min."
            )

    for prev, curr in zip(sorted_days, sorted_days[1:]):
        prev_date = _parse_iso_date(prev.date)
        curr_date = _parse_iso_date(curr.date)
        if prev_date is None or curr_date is None:
            continue
        gap = (curr_date - prev_date).days
        if gap > 1:
            continue
        if prev.date == curr.date and prev.slot == "AM" and prev.intervals_type == "WeightTraining":
            continue
        if _is_hard_day(prev) and _is_hard_day(curr):
            hard_failures.append(
                f"{curr.date}: consecutive hard sessions remain after post-processing "
                f"('{prev.title}' -> '{curr.title}')."
            )

    veto_changes = [change for change in postprocess_changes if _is_hard_veto_change(change)]
    for change in veto_changes:
        hard_failures.append(
            f"Post-processing had to rewrite the plan for a hard rule violation: {change}"
        )

    today_str = review_context.get("today") or date.today().isoformat()
    time_available_min = review_context.get("time_available_min")
    if isinstance(time_available_min, int) and time_available_min >= 0:
        total_today = sum(
            day.duration_min
            for day in plan.days
            if day.date == today_str and day.intervals_type != "Rest" and day.duration_min > 0
        )
        if total_today > time_available_min:
            hard_failures.append(
                f"Today's scheduled load is {total_today} min but athlete only reported {time_available_min} min available."
            )

    rtp_status = review_context.get("rtp_status") or {}
    if rtp_status.get("is_active"):
        max_duration = [30, 45, 60]
        max_zone = [2, 2, 3]
        for idx, day in enumerate(sorted_days[:3]):
            if day.duration_min > max_duration[idx]:
                hard_failures.append(
                    f"{day.date} '{day.title}': exceeds RTP day {idx + 1} duration cap ({max_duration[idx]} min)."
                )
            if _max_zone_order(day) > max_zone[idx]:
                hard_failures.append(
                    f"{day.date} '{day.title}': exceeds RTP day {idx + 1} intensity cap."
                )

    training_days = [day for day in sorted_days if day.intervals_type != "Rest" and day.duration_min > 0]
    by_date: dict[str, list[PlanDay]] = defaultdict(list)
    for day in training_days:
        by_date[day.date].append(day)

    frequency_target = review_context.get("training_frequency_target") or {}
    min_training_days = frequency_target.get("min_training_days")
    max_training_days = frequency_target.get("max_training_days")
    max_double_days = frequency_target.get("max_double_days")
    if isinstance(min_training_days, int) and len(by_date) < min_training_days:
        warnings.append(f"Plan schedules {len(by_date)} training day(s), below the target floor of {min_training_days}.")
    if isinstance(max_training_days, int) and len(by_date) > max_training_days:
        hard_failures.append(f"Plan schedules {len(by_date)} training day(s), above the target ceiling of {max_training_days}.")
    double_days = [day_str for day_str, items in by_date.items() if len(items) > 1]
    if isinstance(max_double_days, int) and len(double_days) > max_double_days:
        hard_failures.append(
            f"Plan uses {len(double_days)} double day(s), above the maximum of {max_double_days}."
        )

    hard_day_count = len({day.date for day in training_days if _is_hard_day(day)})
    allowed_hard_days = _allowed_hard_days(review_context, len(sorted_days))
    if hard_day_count > allowed_hard_days:
        hard_failures.append(
            f"Plan contains {hard_day_count} hard day(s), above the trusted limit of {allowed_hard_days} for current readiness."
        )

    required_categories = _infer_required_categories(review_context)
    existing_categories = Counter(_plan_category(day) for day in training_days)
    missing_categories = [category for category in required_categories if existing_categories.get(category, 0) == 0]
    if missing_categories:
        hard_failures.append(
            "Plan is missing required session category/categories: " + ", ".join(missing_categories) + "."
        )

    for day in training_days:
        category = _plan_category(day)
        if day.duration_min >= 40 and category in {"threshold", "vo2", "ftp_test", "endurance", "long_ride"}:
            first_zone = _canonical_zone(day.workout_steps[0].zone) if day.workout_steps else ""
            last_zone = _canonical_zone(day.workout_steps[-1].zone) if day.workout_steps else ""
            if first_zone not in {"Z1", "Z2"}:
                warnings.append(f"{day.date} '{day.title}': session starts without a warmup.")
            if last_zone != "Z1":
                warnings.append(f"{day.date} '{day.title}': session ends without a cooldown.")

    med = review_context.get("minimum_effective_dose") or {}
    med_global = med.get("mode") == "ACTIVE" and med.get("scope") == "GLOBAL"
    if len(sorted_days) >= 5 and not med_global and existing_categories.get("long_ride", 0) == 0 and existing_categories.get("endurance", 0) == 0:
        warnings.append("Multi-day plan has no clear aerobic anchor session.")

    med = review_context.get("minimum_effective_dose") or {}
    med_global = med.get("mode") == "ACTIVE" and med.get("scope") == "GLOBAL"
    if tss_budget > 0:
        total_tss = sum(base_tss_by_date.values())
        if athlete:
            total_tss += sum(estimate_tss_coggan(day, athlete) for day in plan.days)
        if not med_global and total_tss < tss_budget * 0.85:
            hard_failures.append(
                f"Planned load {round(total_tss)} TSS is below the minimum trusted floor for budget {round(tss_budget)} TSS."
            )
        elif not med_global and total_tss < tss_budget * 0.90:
            warnings.append(
                f"Planned load {round(total_tss)} TSS is materially under budget {round(tss_budget)} TSS."
            )

        # Per-week TSS floor: each calendar week should carry ≥ 80% of its proportional share.
        if athlete and not med_global and len(plan.days) >= 7:
            weekly_tss: dict[str, float] = defaultdict(float)
            for day in plan.days:
                try:
                    d = date.fromisoformat(day.date)
                    iso_week = f"{d.isocalendar()[0]}-W{d.isocalendar()[1]:02d}"
                    weekly_tss[iso_week] += estimate_tss_coggan(day, athlete)
                except (ValueError, AttributeError):
                    pass
            if weekly_tss:
                num_weeks = len(weekly_tss)
                weekly_floor = tss_budget / num_weeks * 0.80
                for week_key, week_tss in sorted(weekly_tss.items()):
                    if week_tss < weekly_floor:
                        warnings.append(
                            f"Week {week_key}: {round(week_tss)} TSS is below the 80% weekly floor ({round(weekly_floor)} TSS). Uneven load distribution reduces mesocycle effectiveness."
                        )

    passed = len(hard_failures) == 0
    if passed:
        summary = "Deterministic validation passed."
        if warnings:
            summary += f" {len(warnings)} warning(s) remain."
    else:
        summary = (
            f"Deterministic validation failed with {len(hard_failures)} hard failure(s)"
            + (f" and {len(warnings)} warning(s)." if warnings else ".")
        )

    return PlanValidationResult(
        passed=passed,
        summary=summary,
        hard_failures=hard_failures,
        warnings=warnings,
    )


def build_validation_review(validation: PlanValidationResult) -> PlanReview:
    critical = ReviewDimension(
        rating="CRITICAL",
        rationale=validation.summary,
        issues=validation.hard_failures[:5],
        recommendations=["Fix deterministic validation failures before the plan can be trusted."],
    )
    weak = ReviewDimension(
        rating="WEAK",
        rationale=validation.summary,
        issues=validation.warnings[:5],
        recommendations=["Clean up the warnings if you revise this candidate."],
    )
    review_fixes = [
        ReviewFix(
            issue=item,
            severity="CRITICAL",
            required_change=item,
            protected_elements=[],
            evidence="The postprocessed plan itself failed deterministic validation.",
        )
        for item in validation.hard_failures[:8]
    ]
    return PlanReview(
        summary=validation.summary,
        goal_alignment=critical,
        key_sessions=critical,
        efficiency=critical,
        load_and_risk=critical,
        individualization=weak,
        race_demands=critical,
        coaching_advice=validation.warnings[:5],
        review_fixes=review_fixes,
        must_fix=validation.hard_failures[:8],
        uncertainty_sources=validation.warnings[:5],
        overall_verdict="REJECT",
    )


def build_validation_scores(validation: PlanValidationResult) -> PlanScores:
    warning_penalty = min(2, len(validation.warnings))
    return PlanScores(
        effectiveness=2,
        risk=9,
        specificity=2,
        simplicity=max(1, 3 - warning_penalty),
        confidence=2,
        rationale=validation.summary,
        uncertainty_sources=validation.warnings[:5],
        action_hint="REJECT",
    )

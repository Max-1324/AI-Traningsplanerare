from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date

from training_plan.core.catalogs import VALID_ZONES, ZONE_ORDER
from training_plan.core.models import AIPlan, PlanDay, PlanValidationResult
from training_plan.engine.postprocess import estimate_tss_coggan
from training_plan.engine.validation.structure import (
    _allowed_hard_days,
    _canonical_zone,
    _day_sort_key,
    _infer_required_categories,
    _is_hard_day,
    _is_hard_veto_change,
    _max_zone_order,
    _normalize_zone,
    _parse_iso_date,
    _plan_category,
)

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



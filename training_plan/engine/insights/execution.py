from training_plan.core.common import *
from training_plan.engine.planning import classify_session_category, session_duration_min
from training_plan.engine.utils import time_available_minutes

from training_plan.engine.insights.common import (
    _activity_date,
    _avg,
    _clamp,
    _dedupe_keep_order,
    _recent_items,
    _score_bucket,
)

def build_minimum_effective_dose(ctl: float,
                                 tss_budget: float,
                                 readiness: dict | None = None,
                                 motivation: dict | None = None,
                                 compliance: dict | None = None,
                                 block_objective: dict | None = None,
                                 development_needs: dict | None = None,
                                 race_demands: dict | None = None,
                                 coach_confidence: dict | None = None,
                                 previous_state: dict | None = None) -> dict:
    readiness = readiness or {}
    motivation = motivation or {}
    compliance = compliance or {}
    block_objective = block_objective or {}
    development_needs = development_needs or {}
    race_demands = race_demands or {}
    coach_confidence = coach_confidence or {}

    # MED v2: reduce sensitivity and avoid flapping around readiness ~60.
    # - Global MED: only when readiness is genuinely low (<55) OR other strong risk triggers exist.
    # - Local MED: for borderline readiness (<58) or 58-59 with an extra fatigue flag; affects mainly today+tomorrow.
    # - Hysteresis: keep LOCAL MED until readiness >=62 to avoid day-to-day flapping.
    policy_version = 2
    previous_state = previous_state or {}
    prev_version = previous_state.get("policy_version")
    prev_mode = previous_state.get("mode", "READY")
    prev_scope = previous_state.get("scope", "NONE")
    prev_updated = previous_state.get("updated")
    prev_recent = False
    if prev_updated:
        try:
            days_since = (date.today() - date.fromisoformat(str(prev_updated)[:10])).days
            prev_recent = 0 <= days_since <= 3
        except Exception:
            prev_recent = False
    if prev_version != policy_version or not prev_recent:
        prev_mode = "READY"
        prev_scope = "NONE"

    readiness_score = readiness.get("score", 60)
    readiness_raw = readiness.get("raw_inputs") if isinstance(readiness.get("raw_inputs"), dict) else {}
    sleep_hours = readiness_raw.get("sleep_hours")
    hrv_deviation_pct = readiness_raw.get("hrv_deviation_pct")
    rhr_slope_7d = readiness_raw.get("rhr_slope_7d")
    avg_rpe_last5 = readiness_raw.get("avg_rpe_last5")
    avg_feel_last5 = readiness_raw.get("avg_feel_last5")

    extra_fatigue_flags: list[str] = []
    try:
        if sleep_hours is not None and float(sleep_hours) < 6.0:
            extra_fatigue_flags.append("sleep<6h")
    except Exception:
        pass
    try:
        if hrv_deviation_pct is not None and float(hrv_deviation_pct) <= -8.0:
            extra_fatigue_flags.append("hrv_dev<=-8%")
    except Exception:
        pass
    try:
        if rhr_slope_7d is not None and float(rhr_slope_7d) >= 0.30:
            extra_fatigue_flags.append("rhr_rising")
    except Exception:
        pass
    try:
        if avg_rpe_last5 is not None and float(avg_rpe_last5) >= 7.0:
            extra_fatigue_flags.append("rpe_high")
    except Exception:
        pass
    try:
        if avg_feel_last5 is not None and float(avg_feel_last5) >= 3.5:
            extra_fatigue_flags.append("feel_low")
    except Exception:
        pass

    low_compliance = compliance.get("weighted_completion_rate", 100) < 80
    low_confidence = coach_confidence.get("level") == "LOW"
    low_motivation = motivation.get("state") in ("FATIGUED", "BURNOUT_RISK")

    # Decide MED mode + scope
    mode = "READY"
    scope = "NONE"
    rationale: list[str] = []

    if low_compliance or low_confidence or low_motivation:
        mode = "ACTIVE"
        scope = "GLOBAL"
        if low_compliance:
            rationale.append("compliance suggests simpler structure")
        if low_confidence:
            rationale.append("coach confidence is limited")
        if low_motivation:
            rationale.append("motivation is fragile")
    elif readiness_score < 55:
        mode = "ACTIVE"
        scope = "GLOBAL"
        rationale.append("readiness is low (<55) – reduce overall load")
    else:
        # Borderline readiness: apply MED locally (today+tomorrow) and keep normal load beyond.
        prev_local = prev_mode == "ACTIVE" and prev_scope == "LOCAL"
        if prev_local and readiness_score < 62:
            mode = "ACTIVE"
            scope = "LOCAL"
            rationale.append("borderline readiness – keep MED local until readiness >= 62")
        else:
            if readiness_score < 58:
                mode = "ACTIVE"
                scope = "LOCAL"
                rationale.append("borderline readiness (<58) – reduce only today/tomorrow")
            elif readiness_score < 60 and extra_fatigue_flags:
                mode = "ACTIVE"
                scope = "LOCAL"
                rationale.append(
                    "borderline readiness (58-59) + extra fatigue signal – reduce only today/tomorrow"
                )

    med_active = mode == "ACTIVE"
    med_global = med_active and scope == "GLOBAL"

    must_hit = _dedupe_keep_order(
        list(block_objective.get("must_hit_sessions", []))
        + list(development_needs.get("must_hit_sessions", []))
        + list(race_demands.get("must_have_sessions", []))
    )
    weekly_floor = round(max(ctl * 6.4, tss_budget * 0.65))
    weekly_target = round(tss_budget * (0.80 if med_global else 0.90))
    if weekly_target < weekly_floor:
        weekly_target = weekly_floor

    mode_label = mode if not med_active else f"{mode} ({scope})"
    scope_hint = ""
    if mode == "ACTIVE" and scope == "LOCAL":
        scope_hint = " Keep today+tomorrow conservative, but plan day 3+ normally."

    summary = (
        f"Minimum effective dose {mode_label}: "
        f"protect {min(len(must_hit), 3)} key stimuli and keep total load around {weekly_floor}-{weekly_target} TSS."
        + scope_hint
    )
    return {
        "policy_version": policy_version,
        "mode": mode,
        "scope": scope,
        "weekly_tss_floor": weekly_floor,
        "weekly_tss_target": weekly_target,
        "must_hit_sessions": must_hit[:4],
        "summary": summary,
        "rationale": rationale,
        "fatigue_flags": extra_fatigue_flags,
        "previous": {
            "mode": prev_mode,
            "scope": prev_scope,
            "updated": prev_updated,
        },
    }


def build_execution_friction(constraints: list[dict] | None,
                             manual_workouts: list[dict],
                             compliance: dict | None = None,
                             learned_patterns: dict | None = None,
                             motivation: dict | None = None,
                             morning: dict | None = None,
                             minimum_effective_dose: dict | None = None) -> dict:
    constraints = constraints or []
    compliance = compliance or {}
    learned_patterns = learned_patterns or {}
    motivation = motivation or {}
    morning = morning or {}
    minimum_effective_dose = minimum_effective_dose or {}

    score = 2.0
    factors = []

    if len(manual_workouts) >= 4:
        score += 2.0
        factors.append("many locked manual sessions")
    elif len(manual_workouts) >= 2:
        score += 1.0
        factors.append("some manual sessions already fixed")

    if len(constraints) >= 3:
        score += 2.0
        factors.append("calendar constraints reduce freedom")
    elif constraints:
        score += 1.0
        factors.append("calendar constraints present")

    weighted_completion = compliance.get("weighted_completion_rate", 100)
    if weighted_completion < 75:
        score += 2.0
        factors.append("historical compliance is fragile")
    elif weighted_completion < 85:
        score += 1.0
        factors.append("compliance is only moderate")

    if motivation.get("state") in ("FATIGUED", "BURNOUT_RISK"):
        score += 1.5
        factors.append("motivation trend suggests extra friction sensitivity")

    availability = time_available_minutes(morning.get("time_available", ""))
    if availability is not None and availability < 60:
        score += 2.0
        factors.append("very limited daily time")
    elif availability is not None and availability < 90:
        score += 1.0
        factors.append("time availability is modest")

    med_mode = minimum_effective_dose.get("mode")
    med_scope = minimum_effective_dose.get("scope")
    if med_mode == "ACTIVE" and med_scope == "GLOBAL":
        score += 1.0
        factors.append("minimum effective dose mode is active")
    elif med_mode == "ACTIVE" and med_scope == "LOCAL":
        factors.append("borderline readiness: keep today+tomorrow conservative")

    for slot, data in learned_patterns.get("time_of_day", {}).items():
        total = data.get("count", 0)
        completion = data.get("completed", 0) / total if total else 1.0
        if slot == "AM" and total >= 4 and completion < 0.60:
            score += 0.5
            factors.append("AM sessions historically fail more often")
            break

    score = round(_clamp(score, 0, 10), 1)
    if score >= 7.5:
        level = "HIGH"
    elif score >= 5.0:
        level = "MEDIUM"
    else:
        level = "LOW"

    summary = (
        f"Execution friction {score}/10 ({level}). "
        + (", ".join(factors[:3]) if factors else "The schedule is relatively flexible.")
    )
    return {
        "score": score,
        "level": level,
        "risk_factors": factors,
        "summary": summary,
    }


def build_training_frequency_target(horizon_days: int,
                                    manual_workouts: list[dict] | None = None,
                                    readiness: dict | None = None,
                                    motivation: dict | None = None,
                                    compliance: dict | None = None,
                                    minimum_effective_dose: dict | None = None,
                                    execution_friction: dict | None = None,
                                    mesocycle: dict | None = None,
                                    morning: dict | None = None) -> dict:
    manual_workouts = manual_workouts or []
    readiness = readiness or {}
    motivation = motivation or {}
    compliance = compliance or {}
    minimum_effective_dose = minimum_effective_dose or {}
    execution_friction = execution_friction or {}
    mesocycle = mesocycle or {}
    morning = morning or {}

    horizon_days = max(int(horizon_days or 0), 1)
    end_date = (date.today() + timedelta(days=horizon_days - 1)).isoformat()
    locked_training_days = len({
        _activity_date(item)
        for item in manual_workouts
        if _activity_date(item) and date.today().isoformat() <= _activity_date(item) <= end_date
    })

    readiness_score = readiness.get("score", 60)
    completion = compliance.get("weighted_completion_rate", 100)
    friction_score = execution_friction.get("score", 3)
    med_active = (
        minimum_effective_dose.get("mode") == "ACTIVE"
        and minimum_effective_dose.get("scope") == "GLOBAL"
    )
    is_deload = bool(mesocycle.get("is_deload"))

    load_ratio = 0.70 if horizon_days >= 10 else 0.75
    if is_deload:
        load_ratio -= 0.10
    if med_active:
        load_ratio -= 0.08
    if readiness_score < 55:
        load_ratio -= 0.05
    elif readiness_score >= 75 and friction_score < 5 and not is_deload and not med_active:
        load_ratio += 0.05
    if motivation.get("state") == "BURNOUT_RISK":
        load_ratio -= 0.07
    elif motivation.get("state") == "FATIGUED":
        load_ratio -= 0.04
    if completion < 75:
        load_ratio -= 0.05
    elif completion >= 90 and readiness_score >= 70 and not med_active:
        load_ratio += 0.03
    if friction_score >= 7:
        load_ratio -= 0.05

    load_ratio = max(0.45, min(load_ratio, 0.90))
    target_training_days = round(horizon_days * load_ratio)
    target_training_days = max(locked_training_days, min(horizon_days, target_training_days))

    spread = 1 if horizon_days <= 10 else 2
    min_training_days = max(locked_training_days, target_training_days - spread)
    max_training_days = min(horizon_days, max(target_training_days, target_training_days + spread))
    min_rest_days = max(0, horizon_days - max_training_days)
    max_rest_days = max(0, horizon_days - min_training_days)

    if med_active or is_deload or readiness_score < 60 or friction_score >= 7:
        max_double_days = 0
    elif horizon_days <= 10:
        max_double_days = 1
    else:
        max_double_days = 2

    today_time_cap_min = time_available_minutes(morning.get("time_available", ""))
    summary = (
        f"Structure target: aim for {min_training_days}-{max_training_days} training days over "
        f"{horizon_days} plan days, with {min_rest_days}-{max_rest_days} rest days and "
        f"max {max_double_days} double day(s)."
    )
    if today_time_cap_min:
        summary += f" Today's total training must fit within {today_time_cap_min} min."

    return {
        "min_training_days": min_training_days,
        "max_training_days": max_training_days,
        "min_rest_days": min_rest_days,
        "max_rest_days": max_rest_days,
        "max_double_days": max_double_days,
        "locked_training_days": locked_training_days,
        "today_time_cap_min": today_time_cap_min,
        "summary": summary,
    }



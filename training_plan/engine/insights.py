from training_plan.core.common import *
from training_plan.engine.planning import classify_session_category, session_duration_min
from training_plan.engine.utils import time_available_minutes


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def _activity_date(item: dict) -> str:
    return (item.get("start_date_local") or item.get("date") or "")[:10]


def _recent_items(items: list[dict], days: int) -> list[dict]:
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    return [item for item in items if _activity_date(item) and _activity_date(item) >= cutoff]


def _avg(values: list[float], default: float = 0.0) -> float:
    return sum(values) / len(values) if values else default


def _score_bucket(score: float) -> str:
    if score >= 80:
        return "STRONG"
    if score >= 65:
        return "SOLID"
    if score >= 50:
        return "DEVELOPING"
    return "LIMITER"


def _dedupe_keep_order(items: list[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result




def build_capacity_map(activities: list[dict],
                       session_quality: dict | None = None,
                       race_demands: dict | None = None,
                       readiness: dict | None = None,
                       np_if_analysis: dict | None = None,
                       polarization: dict | None = None) -> dict:
    session_quality = session_quality or {}
    race_demands = race_demands or {}
    readiness = readiness or {}
    np_if_analysis = np_if_analysis or {}
    polarization = polarization or {}

    category_scores = session_quality.get("category_scores", {})
    cycling = [a for a in activities if a.get("type") in ("Ride", "VirtualRide")]
    recent_56 = _recent_items(cycling, 56)

    longest_ride = max((session_duration_min(a) for a in recent_56), default=0)
    rides_3h = sum(1 for a in recent_56 if session_duration_min(a) >= 180)
    rides_4h = sum(1 for a in recent_56 if session_duration_min(a) >= 240)
    fueling_reps = sum(1 for a in recent_56 if session_duration_min(a) >= 180)

    threshold_data = category_scores.get("threshold", {})
    vo2_data = category_scores.get("vo2", {})
    long_ride_data = category_scores.get("long_ride", {})
    endurance_data = category_scores.get("endurance", {})

    threshold_score = _clamp(
        threshold_data.get("avg_score", 52)
        + min(threshold_data.get("count", 0), 3) * 5
        - (12 if any("Tröskel-gap" in gap or "Tröskel-gap" in gap for gap in race_demands.get("gaps", [])) else 0)
    )
    vo2_score = _clamp(
        vo2_data.get("avg_score", 50)
        + min(vo2_data.get("count", 0), 2) * 6
        - (10 if any("VO2-gap" in gap for gap in race_demands.get("gaps", [])) else 0)
    )
    durability_score = _clamp(
        30
        + min(longest_ride, 360) / 4.5
        + min(rides_4h, 4) * 8
        + long_ride_data.get("avg_score", endurance_data.get("avg_score", 55)) * 0.18
        - (14 if any("Durability-gap" in gap for gap in race_demands.get("gaps", [])) else 0)
    )
    pacing_penalty = 0
    for flag in np_if_analysis.get("flags", []):
        if "IF KONSEKVENT" in flag or "FRONT-LOADING" in flag:
            pacing_penalty += 10
        elif "NP-VARIATION" in flag:
            pacing_penalty += 6
    pacing_score = _clamp(
        endurance_data.get("avg_score", 60)
        + threshold_data.get("avg_score", 60) * 0.15
        - pacing_penalty
    )
    fueling_score = _clamp(
        28
        + min(longest_ride, 360) / 6
        + min(fueling_reps, 4) * 10
        - (15 if any("Fueling-gap" in gap for gap in race_demands.get("gaps", [])) else 0)
    )
    recovery_score = _clamp(
        readiness.get("score", 55) * 0.7
        + (10 if polarization.get("mid_pct", 0) <= 20 else -8)
        + (6 if category_scores.get("recovery", {}).get("avg_score", 65) >= 70 else 0)
    )

    area_specs = [
        ("threshold", threshold_score, "sustainable power around threshold"),
        ("vo2", vo2_score, "high-end aerobic headroom"),
        ("durability", durability_score, "multi-hour resilience"),
        ("pacing", pacing_score, "power discipline and even pacing"),
        ("fueling", fueling_score, "fueling tolerance and race nutrition"),
        ("recovery", recovery_score, "ability to absorb training"),
    ]

    areas = [
        {
            "name": name,
            "score": round(score),
            "status": _score_bucket(score),
            "meaning": meaning,
        }
        for name, score, meaning in area_specs
    ]
    ranked = sorted(areas, key=lambda item: item["score"], reverse=True)
    strongest = [item["name"] for item in ranked[:2]]
    weakest = [item["name"] for item in ranked[-2:]]
    summary = (
        f"Capacity map: strongest {', '.join(strongest)} | weakest {', '.join(weakest)}. "
        f"Threshold {round(threshold_score)}/100, durability {round(durability_score)}/100, "
        f"fueling {round(fueling_score)}/100."
    )
    return {
        "areas": areas,
        "strongest": strongest,
        "weakest": weakest,
        "summary": summary,
        "longest_ride_min": longest_ride,
        "rides_over_3h": rides_3h,
        "rides_over_4h": rides_4h,
        "fueling_reps": fueling_reps,
    }


def build_individualization_profile(state: dict,
                                    learned_patterns: dict | None = None,
                                    compliance: dict | None = None,
                                    session_quality: dict | None = None,
                                    motivation: dict | None = None,
                                    outcome_tracking: dict | None = None) -> dict:
    learned_patterns = learned_patterns or state.get("learned_patterns", {})
    compliance = compliance or {}
    session_quality = session_quality or {}
    motivation = motivation or {}
    outcome_tracking = outcome_tracking or {}

    slot_data = learned_patterns.get("time_of_day", {})
    slot_scores = []
    for slot, values in slot_data.items():
        total = values.get("count", 0)
        if total <= 0:
            continue
        completion = values.get("completed", 0) / total
        slot_scores.append((slot, completion, total))
    slot_scores.sort(key=lambda item: (-item[1], -item[2], item[0]))
    preferred_slots = [slot for slot, completion, total in slot_scores if total >= 3 and completion >= 0.70]
    fragile_slots = [slot for slot, completion, total in slot_scores if total >= 3 and completion < 0.60]

    skip_patterns = []
    for key, values in learned_patterns.get("skip_by_sport_dow", {}).items():
        planned = values.get("planned", 0)
        skipped = values.get("skipped", 0)
        if planned >= 3 and skipped / planned >= 0.50:
            sport, dow = key.rsplit("_", 1)
            skip_patterns.append(f"{sport} on weekday {dow}")

    high_rpe_sports = []
    for sport, values in learned_patterns.get("high_rpe_by_type", {}).items():
        count = values.get("count", 0)
        high_rpe = values.get("high_rpe_count", 0)
        if count >= 3 and high_rpe / count >= 0.50:
            high_rpe_sports.append(sport)

    weighted_completion = compliance.get("weighted_completion_rate", 100)
    key_completion = compliance.get("key_completion_rate", 100)
    model_bias = outcome_tracking.get("summary", "")
    if weighted_completion < 75 or key_completion < 70 or "overestimate" in model_bias.lower():
        response_style = "KEEP_IT_SIMPLE"
    elif motivation.get("state") in ("FATIGUED", "BURNOUT_RISK"):
        response_style = "LOW_FRICTION"
    else:
        response_style = "CAN_HANDLE_STRUCTURE"

    positive_signals = []
    if preferred_slots:
        positive_signals.append(f"Best completion windows: {', '.join(preferred_slots)}")
    if session_quality.get("category_scores", {}).get("long_ride", {}).get("avg_score", 0) >= 70:
        positive_signals.append("Long rides usually translate well.")
    if session_quality.get("category_scores", {}).get("threshold", {}).get("avg_score", 0) >= 70:
        positive_signals.append("Threshold work is tolerated reasonably well.")

    caution_signals = []
    if fragile_slots:
        caution_signals.append(f"Low completion windows: {', '.join(fragile_slots)}")
    if skip_patterns:
        caution_signals.append("Repeated skip patterns: " + ", ".join(skip_patterns[:3]))
    if high_rpe_sports:
        caution_signals.append("Sports that often drive high RPE: " + ", ".join(high_rpe_sports[:3]))

    summary = (
        f"Individualization: {response_style}. "
        + (f"Prefer {', '.join(preferred_slots)}. " if preferred_slots else "")
        + (f"Caution: {caution_signals[0]}" if caution_signals else "No major recurring historical traps found.")
    ).strip()
    profile = {
        "response_style": response_style,
        "preferred_slots": preferred_slots,
        "fragile_slots": fragile_slots,
        "positive_signals": positive_signals,
        "caution_signals": caution_signals,
        "summary": summary,
    }
    state["response_profile"] = {
        "updated": date.today().isoformat(),
        "response_style": response_style,
        "preferred_slots": preferred_slots,
        "fragile_slots": fragile_slots,
        "positive_signals": positive_signals[:3],
        "caution_signals": caution_signals[:3],
    }
    return profile


def build_nutrition_readiness(activities: list[dict],
                              race_demands: dict | None = None,
                              athlete: dict | None = None,
                              phase: dict | None = None) -> dict:
    race_demands = race_demands or {}
    phase = phase or {}
    cycling = [a for a in activities if a.get("type") in ("Ride", "VirtualRide")]
    recent_70 = _recent_items(cycling, 70)
    longest_ride = max((session_duration_min(a) for a in recent_70), default=0)
    rides_2h = sum(1 for a in recent_70 if session_duration_min(a) >= 120)
    rides_3h = sum(1 for a in recent_70 if session_duration_min(a) >= 180)
    rides_4h = sum(1 for a in recent_70 if session_duration_min(a) >= 240)

    score = _clamp(
        20
        + min(longest_ride, 360) / 6
        + min(rides_2h, 4) * 6
        + min(rides_3h, 4) * 8
        + min(rides_4h, 3) * 10
        - (12 if any("Fueling-gap" in gap for gap in race_demands.get("gaps", [])) else 0)
    )
    next_steps = []
    if rides_3h < 2:
        next_steps.append("Schedule one long fueling rehearsal over 3h.")
    if longest_ride < 240:
        next_steps.append("Extend one ride toward 4h to test intake under fatigue.")
    if phase.get("phase") in ("Build", "Taper"):
        next_steps.append("Keep nutrition prescriptions race-specific, not generic.")

    summary = (
        f"Nutrition readiness {round(score)}/100. "
        f"Longest recent ride {round(longest_ride / 60, 1) if longest_ride else 0}h, "
        f"{rides_3h} rides over 3h, {rides_4h} rides over 4h."
    )
    return {
        "score": round(score),
        "status": _score_bucket(score),
        "rides_over_2h": rides_2h,
        "rides_over_3h": rides_3h,
        "rides_over_4h": rides_4h,
        "longest_ride_min": longest_ride,
        "next_steps": next_steps,
        "summary": summary,
    }


def build_minimum_effective_dose(ctl: float,
                                 tss_budget: float,
                                 readiness: dict | None = None,
                                 motivation: dict | None = None,
                                 compliance: dict | None = None,
                                 block_objective: dict | None = None,
                                 development_needs: dict | None = None,
                                 race_demands: dict | None = None,
                                 coach_confidence: dict | None = None) -> dict:
    readiness = readiness or {}
    motivation = motivation or {}
    compliance = compliance or {}
    block_objective = block_objective or {}
    development_needs = development_needs or {}
    race_demands = race_demands or {}
    coach_confidence = coach_confidence or {}

    low_readiness = readiness.get("score", 60) < 60
    low_compliance = compliance.get("weighted_completion_rate", 100) < 80
    low_confidence = coach_confidence.get("level") == "LOW"
    low_motivation = motivation.get("state") in ("FATIGUED", "BURNOUT_RISK")
    med_active = low_readiness or low_compliance or low_confidence or low_motivation

    must_hit = _dedupe_keep_order(
        list(block_objective.get("must_hit_sessions", []))
        + list(development_needs.get("must_hit_sessions", []))
        + list(race_demands.get("must_have_sessions", []))
    )
    weekly_floor = round(max(ctl * 6.4, tss_budget * 0.65))
    weekly_target = round(tss_budget * (0.80 if med_active else 0.90))
    if weekly_target < weekly_floor:
        weekly_target = weekly_floor

    summary = (
        f"Minimum effective dose {'ACTIVE' if med_active else 'READY'}: "
        f"protect {min(len(must_hit), 3)} key stimuli and keep total load around {weekly_floor}-{weekly_target} TSS."
    )
    return {
        "mode": "ACTIVE" if med_active else "READY",
        "weekly_tss_floor": weekly_floor,
        "weekly_tss_target": weekly_target,
        "must_hit_sessions": must_hit[:4],
        "summary": summary,
        "rationale": [
            reason
            for reason, active in [
                ("readiness is not high enough for full volume", low_readiness),
                ("compliance suggests simpler structure", low_compliance),
                ("coach confidence is limited", low_confidence),
                ("motivation is fragile", low_motivation),
            ]
            if active
        ],
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

    if minimum_effective_dose.get("mode") == "ACTIVE":
        score += 1.0
        factors.append("minimum effective dose mode is active")

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
    med_active = minimum_effective_dose.get("mode") == "ACTIVE"
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


def build_benchmark_system(activities: list[dict],
                           planned_events: list[dict],
                           athlete: dict | None = None,
                           phase: dict | None = None,
                           ftp_check: dict | None = None,
                           race_demands: dict | None = None,
                           capacity_map: dict | None = None,
                           nutrition_readiness: dict | None = None,
                           readiness: dict | None = None,
                           np_if_analysis: dict | None = None) -> dict:

    phase = phase or {}
    ftp_check = ftp_check or {}
    race_demands = race_demands or {}
    capacity_map = capacity_map or {}
    nutrition_readiness = nutrition_readiness or {}
    readiness = readiness or {}
    np_if_analysis = np_if_analysis or {}

    future_names = " ".join((e.get("name") or "").lower() for e in planned_events if _activity_date(e) >= date.today().isoformat())
    area_scores = {area["name"]: area["score"] for area in capacity_map.get("areas", [])}
    benchmarks = []

    d2r = race_demands.get("days_to_race")
    days_to_race = d2r if d2r is not None else 999

    if ftp_check.get("needs_test") and "ramp test" not in future_names and "ftp test" not in future_names:
        benchmarks.append({
            "name": "FTP / threshold benchmark",
            "priority": "HIGH",
            "due_in_days": 5 if readiness.get("score", 60) >= 55 else 10,
            "purpose": "recalibrate bike zones and sustainable power",
            "session": "Ramp test or 20 minute threshold test",
            "trigger": ftp_check.get("recommendation", ""),
        })

    if area_scores.get("durability", 50) < 68:
        benchmarks.append({
            "name": "Durability checkpoint",
            "priority": "HIGH" if days_to_race <= 56 else "MEDIUM",
            "due_in_days": 10,
            "purpose": "check how well long steady riding is progressing",
            "session": "Progressive long ride with controlled fueling and final hour discipline",
            "trigger": "Durability remains a limiter in the capacity map.",
        })

    if nutrition_readiness.get("score", 50) < 70:
        benchmarks.append({
            "name": "Fueling benchmark",
            "priority": "HIGH" if days_to_race <= 42 else "MEDIUM",
            "due_in_days": 14,
            "purpose": "test race fueling tolerance under real duration",
            "session": "3-4h endurance ride with explicit CHO target and notes",
            "trigger": nutrition_readiness.get("summary", ""),
        })

    if area_scores.get("pacing", 60) < 65 or np_if_analysis.get("flags"):
        benchmarks.append({
            "name": "Pacing benchmark",
            "priority": "MEDIUM",
            "due_in_days": 12,
            "purpose": "verify smoother power distribution and no early overpacing",
            "session": "Negative split endurance session or steady threshold control set",
            "trigger": "Recent NP/IF patterns suggest pacing drift.",
        })

    if not benchmarks:
        benchmarks.append({
            "name": "Confirmation benchmark",
            "priority": "LOW",
            "due_in_days": 14,
            "purpose": "confirm that the current block is moving the right marker",
            "session": "Repeat one key workout and compare feel, duration and control",
            "trigger": "No urgent calibration gaps found.",
        })

    priority_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    benchmarks.sort(key=lambda item: (priority_order.get(item["priority"], 9), item["due_in_days"]))
    summary = (
        f"Benchmark system: next up is {benchmarks[0]['name']} in about {benchmarks[0]['due_in_days']} days. "
        f"{len(benchmarks)} checkpoints are defined."
    )
    return {
        "benchmarks": benchmarks,
        "next_benchmark": benchmarks[0],
        "summary": summary,
    }


def build_block_learning(state: dict,
                         compliance: dict | None = None,
                         session_quality: dict | None = None,
                         outcome_tracking: dict | None = None,
                         development_needs: dict | None = None,
                         individualization_profile: dict | None = None) -> dict:
    compliance = compliance or {}
    session_quality = session_quality or {}
    outcome_tracking = outcome_tracking or {}
    development_needs = development_needs or {}
    individualization_profile = individualization_profile or {}

    history = state.get("plan_pipeline", {}).get("history", [])
    evaluated = [entry for entry in history if entry.get("outcome")]
    recent = evaluated[-4:]

    worked = []
    did_not_work = []
    next_bias = []

    if recent:
        key_completion = _avg([
            (entry.get("outcome") or {}).get("key_session_completion_rate", 0.0)
            for entry in recent
        ])
        simple_hits = [
            (entry.get("outcome") or {}).get("key_session_completion_rate", 0.0)
            for entry in recent
            if (entry.get("scores") or {}).get("simplicity", 0) >= 7
        ]
        complex_hits = [
            (entry.get("outcome") or {}).get("key_session_completion_rate", 0.0)
            for entry in recent
            if (entry.get("scores") or {}).get("simplicity", 0) <= 5
        ]
        if key_completion >= 0.75:
            worked.append("Recent blocks converted key sessions into real training reasonably well.")
        else:
            did_not_work.append("Recent blocks did not land enough key sessions in practice.")
        if simple_hits and complex_hits and _avg(simple_hits) > _avg(complex_hits) + 0.15:
            worked.append("Simpler plans have translated better than more complex ones.")
            next_bias.append("Prefer cleaner structure over extra filler volume.")

    if compliance.get("weighted_completion_rate", 100) < 80:
        did_not_work.append("Weighted compliance is too low for a dense block design.")
        next_bias.append("Protect two or three must-hit sessions and shorten the rest.")

    for alert in session_quality.get("priority_alerts", [])[:2]:
        did_not_work.append(alert)

    primary_focus = development_needs.get("primary_focus")
    if primary_focus:
        next_bias.append(f"Bias the next block toward {primary_focus}.")

    style = individualization_profile.get("response_style")
    if style == "KEEP_IT_SIMPLE":
        next_bias.append("Use a lower-friction block with fewer moving parts.")
    elif style == "LOW_FRICTION":
        next_bias.append("Use fun, short formats when possible to preserve momentum.")

    if not worked:
        worked.append("No strong positive pattern is established yet; keep learning explicit.")
    if not did_not_work:
        did_not_work.append("No major repeated failure pattern stands out yet.")

    next_bias = _dedupe_keep_order(next_bias)[:4]
    summary = (
        f"Block learning: worked -> {worked[0]} "
        f"Did not work -> {did_not_work[0]} "
        f"Next bias -> {next_bias[0] if next_bias else 'keep observing.'}"
    )
    learning = {
        "worked": worked[:4],
        "did_not_work": did_not_work[:4],
        "next_bias": next_bias,
        "summary": summary,
    }
    state["block_learning"] = {
        "updated": date.today().isoformat(),
        **learning,
    }
    return learning


def build_performance_forecast(fitness: list[dict],
                               readiness: dict | None = None,
                               compliance: dict | None = None,
                               trajectory: dict | None = None,
                               capacity_map: dict | None = None,
                               coach_confidence: dict | None = None,
                               nutrition_readiness: dict | None = None,
                               block_learning: dict | None = None) -> dict:
    readiness = readiness or {}
    compliance = compliance or {}
    trajectory = trajectory or {}
    capacity_map = capacity_map or {}
    coach_confidence = coach_confidence or {}
    nutrition_readiness = nutrition_readiness or {}
    block_learning = block_learning or {}

    ctl_now = fitness[-1].get("ctl", 0) if fitness else 0
    ctl_28d_ago = fitness[-29].get("ctl", ctl_now) if len(fitness) >= 29 else ctl_now
    ctl_change_4w = round(ctl_now - ctl_28d_ago, 1)
    readiness_score = readiness.get("score", 55)
    completion_score = compliance.get("weighted_completion_rate", 80)
    coach_conf = coach_confidence.get("score", 70)
    area_scores = {area["name"]: area["score"] for area in capacity_map.get("areas", [])}

    absorption = _clamp(
        0.45
        + (readiness_score - 50) / 120
        + (completion_score - 75) / 160
        + (coach_conf - 70) / 250,
        0.20,
        0.95,
    )
    if any("simpler plans" in line.lower() for line in block_learning.get("worked", [])):
        absorption += 0.04
    absorption = _clamp(absorption, 0.20, 0.95)

    threshold_now = area_scores.get("threshold", 55)
    durability_now = area_scores.get("durability", 55)
    fueling_now = nutrition_readiness.get("score", area_scores.get("fueling", 50))

    threshold_delta = round(_clamp((78 - threshold_now) * 0.12 + absorption * 6 - 1.5, -2, 8))
    durability_delta = round(_clamp((82 - durability_now) * 0.14 + absorption * 7 - 1.0, -2, 9))
    fueling_delta = round(_clamp((78 - fueling_now) * 0.10 + absorption * 5 - 1.0, -2, 7))

    projected_threshold = round(_clamp(threshold_now + threshold_delta))
    projected_durability = round(_clamp(durability_now + durability_delta))
    projected_fueling = round(_clamp(fueling_now + fueling_delta))
    projected_readiness = round(_clamp(
        projected_threshold * 0.30
        + projected_durability * 0.35
        + projected_fueling * 0.10
        + readiness_score * 0.15
        + completion_score * 0.10
    ))

    confidence = round(_clamp(coach_conf * 0.10, 1, 10))
    assumptions = [
        "must-hit sessions are completed consistently",
        "load stays close to target without excessive fatigue",
        "fueling practice is treated as a trainable skill",
    ]
    risks = []
    if completion_score < 75:
        risks.append("forecast is fragile if compliance stays low")
    if readiness_score < 55:
        risks.append("fatigue may blunt adaptation in the short term")
    if trajectory.get("has_target") and not trajectory.get("is_achievable", True):
        risks.append("trajectory to the target race is aggressive")

    summary = (
        f"4 week forecast: threshold {projected_threshold}/100, durability {projected_durability}/100, "
        f"race readiness trajectory {projected_readiness}/100. Confidence {confidence}/10."
    )
    return {
        "horizon_days": min(28, max(14, trajectory.get("days_to_race", 28) or 28)),
        "ctl_now": round(ctl_now, 1),
        "ctl_change_4w": ctl_change_4w,
        "absorption_score": round(absorption * 100),
        "threshold_now": round(threshold_now),
        "threshold_projection": projected_threshold,
        "durability_now": round(durability_now),
        "durability_projection": projected_durability,
        "fueling_now": round(fueling_now),
        "fueling_projection": projected_fueling,
        "race_readiness_projection": projected_readiness,
        "confidence": confidence,
        "assumptions": assumptions,
        "risks": risks,
        "summary": summary,
    }


def build_race_readiness_score(readiness: dict | None = None,
                               race_demands: dict | None = None,
                               session_quality: dict | None = None,
                               compliance: dict | None = None,
                               taper_score: dict | None = None,
                               coach_confidence: dict | None = None,
                               performance_forecast: dict | None = None,
                               capacity_map: dict | None = None,
                               nutrition_readiness: dict | None = None) -> dict:
    readiness = readiness or {}
    race_demands = race_demands or {}
    session_quality = session_quality or {}
    compliance = compliance or {}
    taper_score = taper_score or {}
    coach_confidence = coach_confidence or {}
    performance_forecast = performance_forecast or {}
    capacity_map = capacity_map or {}
    nutrition_readiness = nutrition_readiness or {}

    area_scores = {area["name"]: area["score"] for area in capacity_map.get("areas", [])}
    
    sq_score = session_quality.get("overall_score")
    sq_val = sq_score if sq_score is not None else 60

    base = (
        readiness.get("score", 55) * 0.18
        + compliance.get("key_completion_rate", 80) * 0.22
        + sq_val * 0.15
        + area_scores.get("durability", 55) * 0.20
        + area_scores.get("threshold", 55) * 0.10
        + nutrition_readiness.get("score", 55) * 0.10
        + performance_forecast.get("race_readiness_projection", 60) * 0.05
    )
    if taper_score.get("is_in_taper"):
        base = base * 0.80 + taper_score.get("score", 60) * 0.20

    gap_penalty = min(len(race_demands.get("gaps", [])), 4) * 4
    confidence_penalty = 8 if coach_confidence.get("level") == "LOW" else 0
    score = round(_clamp(base - gap_penalty - confidence_penalty))

    if score >= 80:
        label = "READY"
    elif score >= 68:
        label = "BUILDING"
    elif score >= 55:
        label = "PARTIAL"
    else:
        label = "NOT_READY"

    limiters = list(capacity_map.get("weakest", []))
    if race_demands.get("gaps"):
        limiters.extend(race_demands["gaps"][:2])

    if limiters:
        limiter_text = ", ".join(str(item) for item in limiters[:2])
    else:
        limiter_text = "no single limiter dominates"
    summary = f"Race readiness {score}/100 ({label}). Main limiters: {limiter_text}."
    return {
        "score": score,
        "label": label,
        "limiters": limiters[:4],
        "summary": summary,
    }


def build_season_plan(phase: dict | None = None,
                      races: list[dict] | None = None,
                      mesocycle: dict | None = None,
                      trajectory: dict | None = None,
                      development_needs: dict | None = None,
                      block_objective: dict | None = None,
                      benchmark_system: dict | None = None,
                      performance_forecast: dict | None = None,
                      capacity_map: dict | None = None,
                      race_readiness: dict | None = None) -> dict:
    phase = phase or {}
    races = races or []
    mesocycle = mesocycle or {}
    trajectory = trajectory or {}
    development_needs = development_needs or {}
    block_objective = block_objective or {}
    benchmark_system = benchmark_system or {}
    performance_forecast = performance_forecast or {}
    capacity_map = capacity_map or {}
    race_readiness = race_readiness or {}

    today = date.today()
    future_races = sorted(
        [race for race in races if _activity_date(race) and _activity_date(race) >= today.isoformat()],
        key=lambda race: _activity_date(race),
    )
    target_event = future_races[0] if future_races else {}
    target_date = _activity_date(target_event)
    days_to_target = trajectory.get("days_to_race")
    if days_to_target is None and target_date:
        days_to_target = (datetime.strptime(target_date, "%Y-%m-%d").date() - today).days

    if days_to_target and days_to_target > 0:
        total_weeks = max(4, min(16, math.ceil(days_to_target / 7)))
    else:
        total_weeks = 12

    primary_focus = development_needs.get("primary_focus", block_objective.get("primary_focus", "durability"))
    secondary_focus = development_needs.get("secondary_focus", block_objective.get("secondary_focus"))
    weakest = capacity_map.get("weakest", [primary_focus])
    benchmark_names = [item["name"] for item in benchmark_system.get("benchmarks", [])[:3]]

    if total_weeks <= 5:
        block_blueprint = [
            ("Stabilize and sharpen", min(2, total_weeks - 1)),
            ("Race specific taper", max(1, total_weeks - min(2, total_weeks - 1))),
        ]
    elif total_weeks <= 9:
        block_blueprint = [
            ("Rebuild main limiter", 3),
            ("Specificity and benchmarks", max(2, total_weeks - 5)),
            ("Taper and race execution", 2),
        ]
    else:
        block_blueprint = [
            ("Stabilize and calibrate", 3),
            ("Primary build block", 4),
            ("Specificity block", max(2, total_weeks - 9)),
            ("Taper and race execution", 2),
        ]

    blocks = []
    cursor = today
    remaining_weeks = total_weeks
    for label, requested_weeks in block_blueprint:
        if remaining_weeks <= 0:
            break
        weeks = min(requested_weeks, remaining_weeks)
        block_end = cursor + timedelta(days=weeks * 7 - 1)
        focus = primary_focus
        if "Specificity" in label:
            focus = secondary_focus or "race_specificity"
        elif "Taper" in label:
            focus = "freshness_and_execution"
        elif "Stabilize" in label and weakest:
            focus = weakest[0]

        milestones = []
        if label == "Stabilize and calibrate" and benchmark_names:
            milestones.append(benchmark_names[0])
        if "Specificity" in label and len(benchmark_names) > 1:
            milestones.append(benchmark_names[1])
        if "Taper" in label:
            milestones.append(f"Race readiness target: {race_readiness.get('score', '?')}/100 -> higher with freshness")

        blocks.append({
            "label": label,
            "start": cursor.isoformat(),
            "end": block_end.isoformat(),
            "weeks": weeks,
            "focus": focus,
            "must_hit": development_needs.get("must_hit_sessions", [])[:3],
            "milestones": milestones,
        })
        cursor = block_end + timedelta(days=1)
        remaining_weeks -= weeks

    summary = (
        f"Season plan: {total_weeks} week map toward {target_event.get('name', 'next target') or 'next target'}. "
        f"Current focus {primary_focus}, next milestone {benchmark_names[0] if benchmark_names else 'key session execution'}."
    )
    return {
        "total_weeks": total_weeks,
        "target_event": target_event.get("name", ""),
        "target_date": target_date,
        "blocks": blocks,
        "summary": summary,
        "forecast_anchor": performance_forecast.get("summary", ""),
    }

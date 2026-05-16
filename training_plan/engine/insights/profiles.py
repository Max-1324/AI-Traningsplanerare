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
        - (12 if any("Threshold gap" in gap or "Tröskel-gap" in gap for gap in race_demands.get("gaps", [])) else 0)
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



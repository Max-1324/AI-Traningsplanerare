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
        # Calculate key_completion safely
        key_completion_rates = []
        for entry in recent:
            outcome = entry.get("outcome")
            if outcome:
                rate = outcome.get("key_session_completion_rate")
                # Ensure rate is treated as float, defaulting to 0.0 if None or not a number
                key_completion_rates.append(float(rate) if isinstance(rate, (float, int)) else 0.0)
        
        key_completion = _avg(key_completion_rates)

        simple_hits = []
        complex_hits = []
        for entry in recent:
            scores = entry.get("scores", {})
            outcome = entry.get("outcome")
            
            # Capture completion rate for hits tracking
            completion_rate = 0.0
            if outcome and isinstance(outcome.get("key_session_completion_rate"), (float, int)):
                 completion_rate = float(outcome["key_session_completion_rate"])

            # Determine simplicity score, default to a neutral value if missing
            simplicity = scores.get("simplicity")
            if not isinstance(simplicity, (float, int)):
                simplicity = 5 # Defaulting to middle ground if data is missing

            if simplicity >= 7:
                simple_hits.append(completion_rate)
            elif simplicity <= 5:
                complex_hits.append(completion_rate)
        
        avg_simple_hits = _avg(simple_hits)
        avg_complex_hits = _avg(complex_hits)

        if key_completion >= 0.75:
            worked.append("Recent blocks converted key sessions into real training reasonably well.")
        else:
            did_not_work.append("Recent blocks did not land enough key sessions in practice.")
        if simple_hits and complex_hits and avg_simple_hits > avg_complex_hits + 0.15:
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

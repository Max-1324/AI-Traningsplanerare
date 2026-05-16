from __future__ import annotations

from collections import defaultdict
from typing import Optional

from training_plan.core.common import *
from training_plan.core.models import AIPlan, PlanDay, PlanDecisionTrace
from training_plan.engine.pipeline.core import _KEY_PLAN_CATEGORIES, classify_plan_day
from training_plan.engine.planning import classify_session_category

def _parse_iso_date(value: str) -> Optional[date]:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except Exception:
        return None


def _plan_session_record(day: PlanDay) -> dict:
    category = classify_plan_day(day)
    return {
        "date": day.date,
        "title": day.title,
        "type": day.intervals_type,
        "duration_min": day.duration_min,
        "slot": day.slot,
        "category": category,
        "is_key": category in _KEY_PLAN_CATEGORIES,
    }


def record_plan_decision(state: dict, plan: AIPlan, trace: PlanDecisionTrace,
                         planned_total_tss: float, block_objective: dict | None = None,
                         race_demands: dict | None = None):
    bucket = state.setdefault("plan_pipeline", {})
    history = bucket.setdefault("history", [])
    sessions = [_plan_session_record(day) for day in plan.days]
    entry = {
        "created_on": date.today().isoformat(),
        "action": trace.action,
        "used_with_override": trace.used_with_override,
        "iterations_run": trace.iterations_run,
        "plan_dates": [session["date"] for session in sessions],
        "planned_sessions": sessions,
        "key_sessions": [session for session in sessions if session["is_key"]],
        "planned_total_tss": round(planned_total_tss, 1),
        "objective": (block_objective or {}).get("objective", ""),
        "primary_focus": (block_objective or {}).get("primary_focus", ""),
        "target_event": (race_demands or {}).get("target_name", ""),
        "review": trace.review.model_dump(exclude_none=True) if trace.review else {},
        "scores": trace.scores.model_dump(exclude_none=True) if trace.scores else {},
        "validator_summary": trace.validator_summary,
        "validator_failures": list(trace.validator_failures),
        "validator_warnings": list(trace.validator_warnings),
        "rationale": trace.rationale,
        "summary": plan.summary,
    }

    history = [
        item for item in history
        if not (
            item.get("created_on") == entry["created_on"]
            and item.get("plan_dates") == entry["plan_dates"]
        )
    ]
    history.append(entry)
    bucket["history"] = history[-40:]


def _match_planned_session(planned_session: dict, actuals: list[dict]) -> Optional[dict]:
    target_category = planned_session.get("category", "general")
    target_type = planned_session.get("type", "")
    target_duration = planned_session.get("duration_min", 0) or 0

    best_match = None
    best_score = -1
    for actual in actuals:
        score = 0
        actual_category = classify_session_category(actual)
        actual_type = actual.get("type", "")
        actual_duration = round(((actual.get("moving_time") or actual.get("elapsed_time") or 0) / 60))

        if actual_category == target_category:
            score += 3
        if actual_type == target_type:
            score += 2
        if target_duration and actual_duration:
            duration_gap = abs(actual_duration - target_duration) / max(target_duration, 1)
            if duration_gap <= 0.25:
                score += 2
            elif duration_gap <= 0.50:
                score += 1
        if target_type == "Rest" and not actual_duration:
            score += 1

        if score > best_score:
            best_match = actual
            best_score = score

    return best_match if best_score >= 3 else None


def update_plan_outcome_tracking(state: dict, activities: list[dict]) -> tuple[dict, dict]:
    bucket = state.setdefault("plan_pipeline", {})
    history = bucket.setdefault("history", [])
    today = date.today()

    activities_by_date: dict[str, list[dict]] = defaultdict(list)
    for activity in activities:
        d = activity.get("start_date_local", "")[:10]
        if d:
            activities_by_date[d].append(activity)

    for entry in history:
        if entry.get("outcome"):
            continue
        plan_dates = entry.get("plan_dates", [])
        plan_end = max((_parse_iso_date(d) for d in plan_dates), default=None)
        if not plan_end or plan_end >= today:
            continue

        planned_sessions = entry.get("planned_sessions", [])
        completed = 0
        key_total = sum(1 for session in planned_sessions if session.get("is_key"))
        key_completed = 0
        realized_load = 0.0

        for session in planned_sessions:
            match = _match_planned_session(session, activities_by_date.get(session["date"], []))
            if match:
                completed += 1
                realized_load += match.get("icu_training_load", 0) or 0
                if session.get("is_key"):
                    key_completed += 1

        planned_total = len(planned_sessions)
        planned_tss = entry.get("planned_total_tss", 0) or 0
        completion_rate = round(completed / planned_total, 2) if planned_total else None
        key_completion_rate = round(key_completed / key_total, 2) if key_total else None
        realized_load_pct = round(realized_load / planned_tss, 2) if planned_tss else None

        if key_completion_rate is not None and key_completion_rate >= 0.8:
            verdict = "The plan was executed strongly in practice."
        elif completion_rate is not None and completion_rate < 0.5:
            verdict = "The plan had low actual compliance."
        else:
            verdict = "The plan yielded mixed results."

        entry["outcome"] = {
            "completion_rate": completion_rate,
            "key_session_completion_rate": key_completion_rate,
            "realized_load_pct": realized_load_pct,
            "verdict": verdict,
        }

    evaluated = [entry for entry in history if entry.get("outcome")]
    recent = evaluated[-6:]
    if not recent:
        historical_validation = {
            "evaluated_plans": 0,
            "summary": "Historical validation: no previous plans with final outcomes to evaluate yet.",
        }
        outcome_tracking = {
            "evaluated_plans": 0,
            "summary": "Outcome tracking: waiting for previous plan windows to finish before calibration can be done.",
        }
        bucket["historical_validation"] = historical_validation
        bucket["outcome_tracking"] = outcome_tracking
        return historical_validation, outcome_tracking

    avg_completion = round(sum((entry["outcome"].get("completion_rate") or 0) for entry in recent) / len(recent), 2)
    avg_key_completion = round(sum((entry["outcome"].get("key_session_completion_rate") or 0) for entry in recent) / len(recent), 2)
    avg_effectiveness = round(sum((entry.get("scores", {}).get("effectiveness") or 0) for entry in recent) / len(recent), 1)
    avg_confidence = round(sum((entry.get("scores", {}).get("confidence") or 0) for entry in recent) / len(recent), 1)

    simplicity_strong = [
        entry["outcome"].get("key_session_completion_rate")
        for entry in recent
        if (entry.get("scores", {}).get("simplicity") or 0) >= 7
        and entry["outcome"].get("key_session_completion_rate") is not None
    ]
    simplicity_weak = [
        entry["outcome"].get("key_session_completion_rate")
        for entry in recent
        if (entry.get("scores", {}).get("simplicity") or 0) <= 5
        and entry["outcome"].get("key_session_completion_rate") is not None
    ]

    bias_note = "Calibration looks relatively neutral."
    if avg_effectiveness >= 8 and avg_key_completion < 0.6:
        bias_note = "The model tends to overestimate effectiveness when key sessions are not completed."
    elif avg_effectiveness <= 5 and avg_key_completion >= 0.75:
        bias_note = "The model sometimes underestimates what the athlete can actually absorb."

    simplicity_note = ""
    if simplicity_strong and simplicity_weak:
        strong_avg = sum(simplicity_strong) / len(simplicity_strong)
        weak_avg = sum(simplicity_weak) / len(simplicity_weak)
        if strong_avg > weak_avg + 0.15:
            simplicity_note = " Simpler plans have historically given better key session compliance."

    historical_validation = {
        "evaluated_plans": len(recent),
        "avg_completion_rate": avg_completion,
        "avg_key_session_completion_rate": avg_key_completion,
        "summary": (
            f"Historical validation (proxy): {len(recent)} previous plan windows evaluated. "
            f"Avg compliance {round(avg_completion * 100)}% and key sessions {round(avg_key_completion * 100)}%."
            f"{simplicity_note}"
        ),
    }
    outcome_tracking = {
        "evaluated_plans": len(recent),
        "avg_effectiveness": avg_effectiveness,
        "avg_confidence": avg_confidence,
        "summary": (
            f"Outcome tracking: predicted effectiveness {avg_effectiveness}/10 och confidence {avg_confidence}/10 "
            f"har hittills gett {round(avg_key_completion * 100)}% key-session completion. {bias_note}"
        ),
    }

    bucket["historical_validation"] = historical_validation
    bucket["outcome_tracking"] = outcome_tracking
    return historical_validation, outcome_tracking

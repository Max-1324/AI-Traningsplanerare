from __future__ import annotations

from training_plan.core.common import *
from training_plan.core.models import AIPlan, PlanDay, PlanReview, PlanScores
from training_plan.engine.pipeline.core import _KEY_PLAN_CATEGORIES, classify_plan_day

_VETO_TRIGGERS = [
    "HARD-EASY", "TAK V", "VOLYMSPÄRR", "VOLYMSPARR", "STYRKEGRÄNS",
    "RULLSKIDSGRÄNS", "ACWR-VETO", "HRV-VETO", "TIDSBUDGET",
    "TIME BUDGET", "TSS-UNDERSKOTT VETO", "TSS-DEFICIT VETO",
]

def _parse_plan_date(value: str):
    try:
        return date.fromisoformat(value)
    except Exception:
        return None


def _hard_plan_offsets(plan: AIPlan | None, today_str: str) -> list[int]:
    if not plan:
        return []
    today = _parse_plan_date(today_str) or date.today()
    offsets: list[int] = []
    for day in plan.days:
        day_date = _parse_plan_date(day.date)
        if not day_date:
            continue
        if classify_plan_day(day) in _KEY_PLAN_CATEGORIES:
            offsets.append((day_date - today).days)
    return sorted(offsets)


def _acute_fatigue_signal(review_context: dict | None) -> dict:
    review_context = review_context or {}
    readiness = review_context.get("readiness") or {}
    raw = readiness.get("raw_inputs") if isinstance(readiness.get("raw_inputs"), dict) else {}
    fatigue_flags: list[str] = []

    score = readiness.get("score")
    try:
        if score is not None and float(score) < 55:
            fatigue_flags.append("readiness<55")
    except (TypeError, ValueError):
        pass

    checks = (
        ("sleep<6h", raw.get("sleep_hours"), lambda v: float(v) < 6.0),
        ("hrv_dev<=-8%", raw.get("hrv_deviation_pct"), lambda v: float(v) <= -8.0),
        ("rhr_rising", raw.get("rhr_slope_7d"), lambda v: float(v) > 0.3),
        ("rpe_high", raw.get("avg_rpe_last5"), lambda v: float(v) >= 7.5),
        ("feel_low", raw.get("avg_feel_last5"), lambda v: float(v) >= 3.8),
    )
    for label, value, predicate in checks:
        if value is None:
            continue
        try:
            if predicate(value):
                fatigue_flags.append(label)
        except (TypeError, ValueError):
            continue

    return {
        "active": bool(fatigue_flags),
        "flags": fatigue_flags,
    }


def _fitness_trend_signal(review_context: dict | None) -> dict:
    review_context = review_context or {}
    trajectory = review_context.get("trajectory") or {}
    performance_forecast = review_context.get("performance_forecast") or {}
    minimum_effective_dose = review_context.get("minimum_effective_dose") or {}
    signals: list[str] = []

    required_weekly_tss = trajectory.get("required_weekly_tss")
    try:
        if required_weekly_tss is not None and float(required_weekly_tss) > 0:
            signals.append("positive_load_target")
    except (TypeError, ValueError):
        pass

    forecast_summary = str(performance_forecast.get("summary") or "").lower()
    if any(token in forecast_summary for token in ("improve", "build", "trajectory")):
        signals.append("positive_forecast")

    med_global = (
        minimum_effective_dose.get("mode") == "ACTIVE"
        and minimum_effective_dose.get("scope") == "GLOBAL"
    )
    return {
        "supports_progression": bool(signals) and not med_global,
        "signals": signals,
    }


def _apply_fatigue_fitness_score_adjustments(
    *,
    plan: AIPlan | None,
    review_context: dict | None,
    effectiveness: int,
    risk: int,
    specificity: int,
    confidence: int,
    uncertainty_sources: list[str],
) -> tuple[int, int, int, int, list[str], list[str]]:
    """Separate acute readiness risk from longer-term fitness progression.

    Acute fatigue should punish hard work only in the next 48h. A positive
    fitness trend should preserve credit for future key sessions instead of
    letting today's low readiness make the whole horizon look risky.
    """
    notes: list[str] = []
    fatigue = _acute_fatigue_signal(review_context)
    fitness = _fitness_trend_signal(review_context)
    today_str = (review_context or {}).get("today") or date.today().isoformat()
    hard_offsets = _hard_plan_offsets(plan, today_str)
    hard_today_tomorrow = [offset for offset in hard_offsets if 0 <= offset <= 1]
    future_hard = [offset for offset in hard_offsets if offset >= 2]

    if fatigue["active"] and hard_today_tomorrow:
        risk = min(10, risk + (3 if 0 in hard_today_tomorrow else 2))
        confidence = max(1, confidence - 1)
        uncertainty_sources.append("acute fatigue conflicts with hard work in the next 48h")
        notes.append(
            "acute fatigue raised risk because a hard/key session is scheduled today or tomorrow"
        )
    elif fatigue["active"] and future_hard and fitness["supports_progression"]:
        effectiveness = min(10, effectiveness + 1)
        specificity = min(10, specificity + 1)
        notes.append(
            "acute fatigue did not penalize future key sessions because they are outside the 48h fatigue window"
        )

    if fitness["supports_progression"] and not fatigue["active"] and future_hard:
        confidence = min(10, confidence + 1)
        notes.append("positive fitness trend supports keeping progression/key sessions later in the horizon")

    return effectiveness, risk, specificity, confidence, uncertainty_sources, notes


def compute_scores_from_review(
    review: PlanReview,
    plan: AIPlan | None = None,
    review_context: dict | None = None,
) -> PlanScores:
    """
    Deterministic scoring based on review dimensions instead of AI scoring.
    Eliminates redundant AI call while maintaining decision quality.
    """
    # Map review rating levels to score ranges
    rating_to_score = {
        "CRITICAL": 2,      # Critical problems = low score
        "WEAK": 4,          # Weak = below acceptable
        "ADEQUATE": 6,      # Adequate = acceptable baseline
        "STRONG": 8,        # Strong = good
    }

    # Score effectiveness from goal_alignment + key_sessions
    goal_score = rating_to_score.get(review.goal_alignment.rating, 5)
    sessions_score = rating_to_score.get(review.key_sessions.rating, 5)
    effectiveness = min(10, round((goal_score + sessions_score) / 2))

    # Score risk from load_and_risk (inverted: higher rating = lower risk score)
    risk_base = rating_to_score.get(review.load_and_risk.rating, 5)
    # Invert: CRITICAL load → HIGH risk (8), EXCELLENT → LOW risk (2)
    risk = max(1, min(10, 10 - risk_base + 2))

    # Score specificity from race_demands + efficiency
    demands_score = rating_to_score.get(review.race_demands.rating, 5)
    efficiency_score = rating_to_score.get(review.efficiency.rating, 5)
    specificity = min(10, round((demands_score + efficiency_score) / 2))

    # Score simplicity from individualization (simple = individualized for athlete)
    simplicity_base = rating_to_score.get(review.individualization.rating, 5)
    # Also penalize if there are many must-fix items
    must_fix_penalty = min(3, len(review.must_fix or []))
    simplicity = max(1, simplicity_base - must_fix_penalty)

    # Score confidence based on uncertainty sources
    uncertainty_count = len(review.uncertainty_sources or [])
    confidence_base = 8
    confidence = max(2, confidence_base - uncertainty_count)

    uncertainty_sources = list(review.uncertainty_sources or [])
    (
        effectiveness,
        risk,
        specificity,
        confidence,
        uncertainty_sources,
        context_notes,
    ) = _apply_fatigue_fitness_score_adjustments(
        plan=plan,
        review_context=review_context,
        effectiveness=effectiveness,
        risk=risk,
        specificity=specificity,
        confidence=confidence,
        uncertainty_sources=uncertainty_sources,
    )

    # Compute action hint based on verdict + dimensions
    if review.overall_verdict == "REJECT":
        action_hint = "REJECT"
    elif review.overall_verdict == "PASS":
        # Can ACCEPT only if all scores are good
        if (effectiveness >= 7 and risk <= 5 and specificity >= 7 and
            simplicity >= 6 and confidence >= 4 and not review.must_fix):
            action_hint = "ACCEPT"
        else:
            action_hint = "REVISE"
    else:
        action_hint = "REVISE"

    return PlanScores(
        effectiveness=effectiveness,
        risk=risk,
        specificity=specificity,
        simplicity=simplicity,
        confidence=confidence,
        rationale=(
            f"Computed from review: {review.overall_verdict} "
            f"({', '.join(d.rating for d in [review.goal_alignment, review.key_sessions, review.load_and_risk, review.race_demands])})"
            + (f"; context adjustments: {' | '.join(context_notes)}" if context_notes else "")
        ),
        uncertainty_sources=uncertainty_sources,
        action_hint=action_hint,
    )


def decide_plan(review: PlanReview, scores: PlanScores, postprocess_changes: list[str] = None) -> tuple[str, str]:
    postprocess_changes = postprocess_changes or []
    vetos_found = [c for c in postprocess_changes if any(t in c.upper() for t in _VETO_TRIGGERS)]

    dimensions = [
        review.goal_alignment,
        review.key_sessions,
        review.efficiency,
        review.load_and_risk,
        review.individualization,
        review.race_demands,
    ]
    critical_count = sum(1 for dim in dimensions if dim.rating == "CRITICAL")
    weak_count = sum(1 for dim in dimensions if dim.rating == "WEAK")

    reasons = []
    if vetos_found:
        reasons.append(f"Python veto triggered ({len(vetos_found)} rule violations)")
    if review.must_fix:
        reasons.append(f"{len(review.must_fix)} must-fix")
    if critical_count:
        reasons.append(f"{critical_count} critical areas")
    if scores.risk >= 8:
        reasons.append(f"risk {scores.risk}/10")
    if scores.effectiveness <= 4:
        reasons.append(f"effectiveness {scores.effectiveness}/10")
    if scores.specificity <= 4:
        reasons.append(f"specificity {scores.specificity}/10")

    if (
        review.overall_verdict == "REJECT"
        or scores.action_hint == "REJECT"
        or scores.risk >= 8
        or scores.effectiveness <= 4
        or scores.specificity <= 4
        or critical_count >= 2
    ):
        return "REJECT", ", ".join(reasons) or "The plan is rejected by review/scoring."

    if (
        not vetos_found
        and review.overall_verdict == "PASS"
        and scores.action_hint == "ACCEPT"
        and scores.effectiveness >= 7
        and scores.specificity >= 7
        and scores.risk <= 5
        and scores.simplicity >= 5
        and scores.confidence >= 3
        and not review.must_fix
        and critical_count == 0
        and weak_count <= 1
    ):
        return "ACCEPT", "The plan is aligned with goals, sufficiently safe and needs no mandatory changes."

    reasons.extend([
        f"effectiveness {scores.effectiveness}/10",
        f"risk {scores.risk}/10",
        f"specificity {scores.specificity}/10",
        f"simplicity {scores.simplicity}/10",
        f"confidence {scores.confidence}/10",
    ])
    return "REVISE", ", ".join(dict.fromkeys(reasons))



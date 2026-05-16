from __future__ import annotations

from training_plan.core.common import *
from training_plan.core.models import AIPlan, PairwiseDecision, PlanDecisionTrace, PlanReview, PlanScores
from training_plan.engine.pipeline.core import _is_invalid_review_fallback, generate_plan, summarize_plan_candidate
from training_plan.engine.pipeline.prompts import build_tss_gap_revision_prompt
from training_plan.engine.postprocess import estimate_tss_coggan
from training_plan.engine.pipeline.scoring import _VETO_TRIGGERS

_PAIRWISE_SCORE_MARGIN = int(os.getenv("PLAN_PAIRWISE_SCORE_MARGIN", "1"))
_INVALID_REVIEW_RANK_PENALTY = float(os.getenv("PLAN_INVALID_REVIEW_RANK_PENALTY", "4.0"))
_INVALID_REVIEW_COMPETITIVE_MARGIN = float(os.getenv("PLAN_INVALID_REVIEW_COMPETITIVE_MARGIN", "2.0"))
_TSS_GAP_REVISION_MIN_MISSING = int(os.getenv("PLAN_TSS_GAP_REVISION_MIN_MISSING", "120"))
_TSS_GAP_REVISION_MIN_PCT = float(os.getenv("PLAN_TSS_GAP_REVISION_MIN_PCT", "0.90"))
_TSS_DEFICIT_VETO_PCT = float(os.getenv("PLAN_TSS_DEFICIT_VETO_PCT", "0.85"))

def _candidate_rank(review: PlanReview, scores: PlanScores, postprocess_changes: list[str] = None) -> float:
    postprocess_changes = postprocess_changes or []
    vetos_found = sum(1 for c in postprocess_changes if any(t in c.upper() for t in _VETO_TRIGGERS))
    invalid_review_penalty = _INVALID_REVIEW_RANK_PENALTY if _is_invalid_review_fallback(review) else 0.0

    verdict_bonus = {"PASS": 2.0, "REVISE": 0.5, "REJECT": -2.0}
    return (
        scores.effectiveness * 2.0
        + scores.specificity * 1.6
        + scores.simplicity * 1.2
        + scores.confidence * 0.8
        - scores.risk * 1.8
        - len(review.must_fix) * 0.7
        - vetos_found * 3.0
        - invalid_review_penalty
        + verdict_bonus.get(review.overall_verdict, 0.0)
    )


def _candidate_round_line(label: str, action: str, scores: PlanScores, review: PlanReview,
                          focus: str = "") -> str:
    must_fix = review.must_fix[0] if review.must_fix else "no major must-fix"
    return (
        f"{label}: {action} | Focus {focus or 'balanced'} | Effect {scores.effectiveness}/10 | "
        f"Risk {scores.risk}/10 | Spec {scores.specificity}/10 | Simplicity {scores.simplicity}/10 | "
        f"Confidence {scores.confidence}/10 | Must-fix {must_fix}"
    )


def _pick_round_winner(results: list[dict]) -> dict:
    accepted = [result for result in results if result["action"] == "ACCEPT"]
    pool = accepted if accepted else results
    valid_pool = [result for result in pool if not _is_invalid_review_fallback(result["review"])]
    invalid_pool = [result for result in pool if _is_invalid_review_fallback(result["review"])]

    if valid_pool:
        best_valid = max(valid_pool, key=lambda result: result["rank"])
        if invalid_pool:
            best_invalid = max(invalid_pool, key=lambda result: result["rank"])
            if best_invalid["rank"] > best_valid["rank"] + _INVALID_REVIEW_COMPETITIVE_MARGIN:
                return best_invalid
        return best_valid

    return max(pool, key=lambda result: result["rank"])


def _score_delta_text(prev: PlanScores | None, curr: PlanScores) -> str:
    if prev is None:
        return (
            f"Baseline scores -> Effect {curr.effectiveness}/10, Risk {curr.risk}/10, "
            f"Spec {curr.specificity}/10, Simplicity {curr.simplicity}/10, Confidence {curr.confidence}/10"
        )

    def fmt(label: str, old: int, new: int, invert_good: bool = False) -> str:
        delta = new - old
        if invert_good:
            direction = "better" if delta < 0 else "worse" if delta > 0 else "unchanged"
        else:
            direction = "better" if delta > 0 else "worse" if delta < 0 else "unchanged"
        sign = f"{delta:+d}"
        return f"{label} {old}->{new} ({sign}, {direction})"

    return " | ".join([
        fmt("Effect", prev.effectiveness, curr.effectiveness),
        fmt("Risk", prev.risk, curr.risk, invert_good=True),
        fmt("Spec", prev.specificity, curr.specificity),
        fmt("Simplicity", prev.simplicity, curr.simplicity),
        fmt("Confidence", prev.confidence, curr.confidence),
    ])


def _candidate_change_reason(prev_trace: PlanDecisionTrace | None, result: dict) -> str:
    reasons = []
    prev_review = prev_trace.review if prev_trace else None
    prev_scores = prev_trace.scores if prev_trace else None
    review: PlanReview = result["review"]
    scores: PlanScores = result["scores"]
    changes: list[str] = result["changes"]

    if prev_scores:
        if scores.effectiveness > prev_scores.effectiveness:
            reasons.append("higher effectiveness")
        if scores.specificity > prev_scores.specificity:
            reasons.append("better race/block specificity")
        if scores.simplicity > prev_scores.simplicity:
            reasons.append("simpler structure")
        if scores.confidence > prev_scores.confidence:
            reasons.append("less uncertainty")
        if scores.risk < prev_scores.risk:
            reasons.append("lower risk")

    if prev_review:
        prev_must_fix = set(prev_review.must_fix or [])
        curr_must_fix = set(review.must_fix or [])
        removed = [item for item in prev_review.must_fix if item not in curr_must_fix]
        added = [item for item in review.must_fix if item not in prev_must_fix]
        if removed:
            reasons.append(f"resolved must-fix: {removed[0]}")
        if added:
            reasons.append(f"new must-fix: {added[0]}")

    veto_items = [c for c in changes if "VETO" in c.upper()]
    if veto_items:
        reasons.append(f"postprocess veto: {veto_items[0]}")
    if _is_invalid_review_fallback(review):
        reasons.append("review parse fallback used")

    if not reasons and review.strengths:
        reasons.append(f"strength highlighted: {review.strengths[0]}")
    if not reasons:
        reasons.append(result["rationale"])

    return "; ".join(reasons[:3])


def _pairwise_rank_adjustment(pairwise: PairwiseDecision | None) -> float:
    if pairwise is None:
        return 0.0
    confidence_bonus = min(1.5, pairwise.confidence / 10)
    if pairwise.better_plan == "CANDIDATE":
        return 2.5 + confidence_bonus
    if pairwise.better_plan == "CURRENT":
        return -(2.5 + confidence_bonus)
    return 0.0


def _pairwise_reason_text(pairwise: PairwiseDecision | None) -> str:
    if pairwise is None:
        return "no pairwise comparison"
    parts = [f"Pairwise {pairwise.better_plan.lower()} ({pairwise.confidence}/10)"]
    if pairwise.must_fix_resolved:
        parts.append(f"resolved: {pairwise.must_fix_resolved[0]}")
    if pairwise.must_fix_added:
        parts.append(f"added: {pairwise.must_fix_added[0]}")
    if pairwise.regressions:
        parts.append(f"regression: {pairwise.regressions[0]}")
    elif pairwise.improved_areas:
        parts.append(f"improved: {pairwise.improved_areas[0]}")
    elif pairwise.summary:
        parts.append(pairwise.summary)
    return "; ".join(parts[:4])


def _should_run_pairwise(previous_trace: PlanDecisionTrace | None, candidate_scores: PlanScores,
                         candidate_review: PlanReview, candidate_changes: list[str]) -> bool:
    if not previous_trace or not previous_trace.scores or not previous_trace.review:
        return False

    prev_scores = previous_trace.scores
    prev_review = previous_trace.review
    candidate_must_fix = len(candidate_review.must_fix or [])
    previous_must_fix = len(prev_review.must_fix or [])
    veto_count = sum(1 for c in candidate_changes if "VETO" in c.upper())

    if veto_count and not any("VETO" in item.upper() for item in (prev_review.must_fix or [])):
        return False
    if candidate_must_fix > previous_must_fix + 1:
        return False
    if candidate_scores.effectiveness + _PAIRWISE_SCORE_MARGIN < prev_scores.effectiveness:
        return False
    if candidate_scores.specificity + _PAIRWISE_SCORE_MARGIN < prev_scores.specificity:
        return False
    if candidate_scores.risk > prev_scores.risk + _PAIRWISE_SCORE_MARGIN:
        return False
    return True


def _is_meaningful_improvement(previous_trace: PlanDecisionTrace | None, winner: dict) -> bool:
    if not previous_trace or not previous_trace.scores or not previous_trace.review:
        return True

    prev_scores = previous_trace.scores
    prev_review = previous_trace.review
    scores: PlanScores = winner["scores"]
    review: PlanReview = winner["review"]
    pairwise: PairwiseDecision | None = winner.get("pairwise")

    resolved = len([item for item in (prev_review.must_fix or []) if item not in (review.must_fix or [])])
    added = len([item for item in (review.must_fix or []) if item not in (prev_review.must_fix or [])])

    if pairwise and pairwise.better_plan == "CANDIDATE":
        return True
    if scores.effectiveness > prev_scores.effectiveness:
        return True
    if scores.specificity > prev_scores.specificity:
        return True
    if scores.risk < prev_scores.risk:
        return True
    if resolved > added:
        return True
    return False


def _count_weekly_tss_cap_rewrites(changes: list[str]) -> int:
    markers = ("TAK V", "VOLYMSPARR", "VOLYMSPAR", "VOLYMSPÄRR")
    return sum(
        1
        for change in (changes or [])
        if any(marker in str(change or "").upper() for marker in markers)
    )


def _apply_tss_gap_revision(
    candidate_plan: AIPlan,
    candidate_changes: list[str],
    gen_provider: str,
    generation_prompt: str,
    postprocess_candidate: Callable[[AIPlan], tuple[AIPlan, list[str]]],
    athlete: dict | None,
    base_tss_by_date: dict[str, float],
    tss_budget: float,
    review_context: dict,
    attempt: int,
) -> tuple[AIPlan, list[str]]:
    """Check whether the plan is significantly under the TSS budget and, if so,
    issue a single focused AI revision to close the gap.

    This is a named pipeline phase rather than an inlined inner loop so that:
    - The extra AI call is visible in the call stack and logs.
    - The phase can be disabled or tuned independently.
    - The candidate generation loop stays readable.

    Returns the (possibly revised) plan and updated changes list.
    """
    med = review_context.get("minimum_effective_dose") or {}
    med_global = med.get("mode", "READY") == "ACTIVE" and med.get("scope", "NONE") == "GLOBAL"
    gap_fill_trigger_pct = min(_TSS_GAP_REVISION_MIN_PCT, _TSS_DEFICIT_VETO_PCT)

    planned_tss = sum(estimate_tss_coggan(d, athlete) for d in candidate_plan.days) if athlete else 0
    total_tss = planned_tss + sum(base_tss_by_date.values())
    original_plan = candidate_plan
    original_changes = list(candidate_changes)
    original_planned_tss = planned_tss
    original_total_tss = total_tss
    original_tss_cap_rewrites = _count_weekly_tss_cap_rewrites(original_changes)

    # Phase 1 — AI-driven gap fill (only when gap is large enough to matter)
    if (
        not med_global
        and tss_budget > 0
        and total_tss < tss_budget * gap_fill_trigger_pct
    ):
        missing = round(tss_budget - total_tss)
        if missing >= _TSS_GAP_REVISION_MIN_MISSING:
            log.info(
                "   [TSS-GAP] Candidate is %s TSS under budget (%s/%s) — "
                "issuing load-balancing revision...",
                missing,
                round(total_tss),
                round(tss_budget),
            )
            tss_gap_prompt = build_tss_gap_revision_prompt(
                generation_prompt,
                candidate_plan,
                missing,
                total_tss,
                tss_budget,
                candidate_changes,
                attempt,
            )
            candidate_plan = generate_plan(
                gen_provider,
                tss_gap_prompt,
                temperature=_REVISION_GENERATION_TEMPERATURE,
            )
            candidate_plan, candidate_changes = postprocess_candidate(candidate_plan)
            revised_tss_cap_rewrites = _count_weekly_tss_cap_rewrites(candidate_changes)
            if revised_tss_cap_rewrites > original_tss_cap_rewrites:
                log.info(
                    "   [TSS-GAP] Revision triggered weekly-cap rewrites (TAK). "
                    "Keeping the original candidate instead."
                )
                candidate_plan = original_plan
                candidate_changes = original_changes
                planned_tss = original_planned_tss
                total_tss = original_total_tss
            else:
                planned_tss = sum(estimate_tss_coggan(d, athlete) for d in candidate_plan.days) if athlete else 0
                total_tss = planned_tss + sum(base_tss_by_date.values())

    # Phase 2 — annotate remaining deficit so the reviewer can see it
    if tss_budget > 0 and total_tss < tss_budget * _TSS_DEFICIT_VETO_PCT:
        missing = round(tss_budget - total_tss)
        if med_global:
            candidate_changes.append(
                f"TSS-INFO: Plan gives {round(total_tss)} TSS (budget {round(tss_budget)}). "
                f"Approved due to low form (MED=ACTIVE), but do not reduce further."
            )
        else:
            candidate_changes.append(
                f"TSS-DEFICIT VETO: Plan only reaches {round(total_tss)} TSS "
                f"(budget {round(tss_budget)}). You are missing {missing} TSS. "
                f"Extend endurance sessions or add aerobic volume!"
            )

    return candidate_plan, candidate_changes



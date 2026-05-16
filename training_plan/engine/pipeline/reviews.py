from __future__ import annotations

from training_plan.core.common import *
from training_plan.core.models import AIPlan, PairwiseDecision, PlanDecisionTrace, PlanReview, PlanScores
from training_plan.engine.pipeline.core import _debug_ai_call, _parse_structured_response
from training_plan.engine.pipeline.prompts import build_pairwise_prompt, build_review_prompt, filter_review_context

_REVIEW_TEMPERATURE = float(os.getenv("PLAN_REVIEW_TEMPERATURE", "0.05"))
_PAIRWISE_TEMPERATURE = float(os.getenv("PLAN_PAIRWISE_TEMPERATURE", "0.05"))

def review_plan(provider: str, plan: AIPlan, athlete: dict | None,
                base_tss_by_date: dict[str, float], review_context: dict,
                postprocess_changes: list[str]) -> PlanReview:
    fallback = PlanReview(
        summary="Review step could not be parsed safely. The plan should be simplified and reviewed again.",
        must_fix=["Review response was invalid; run a safer revision round."],
        uncertainty_sources=["Reviewer response could not be parsed."],
        overall_verdict="REVISE",
    )
    # Use filtered context to reduce token waste
    filtered_context = filter_review_context(review_context)
    review_prompt = build_review_prompt(plan, athlete, base_tss_by_date, filtered_context, postprocess_changes)
    raw = call_ai(provider, review_prompt, temperature=_REVIEW_TEMPERATURE)
    _debug_ai_call("REVIEW", review_prompt, raw or "")
    return _parse_structured_response(raw, PlanReview, fallback, "Plan-review")


def compare_plans(provider: str, current_plan: AIPlan, current_trace: PlanDecisionTrace,
                  candidate_plan: AIPlan, candidate_review: PlanReview, candidate_scores: PlanScores,
                  athlete: dict | None, base_tss_by_date: dict[str, float], review_context: dict,
                  candidate_changes: list[str]) -> PairwiseDecision:
    fallback = PairwiseDecision(
        better_plan="TIE",
        confidence=3,
        summary="Pairwise comparison could not be parsed safely, so no extra promotion was given.",
    )
    if not current_trace.review or not current_trace.scores:
        return fallback

    pairwise_prompt = build_pairwise_prompt(
        current_plan,
        current_trace.review,
        current_trace.scores,
        candidate_plan,
        candidate_review,
        candidate_scores,
        athlete,
        base_tss_by_date,
        review_context,
        candidate_changes,
    )
    raw = call_ai(provider, pairwise_prompt, temperature=_PAIRWISE_TEMPERATURE)
    _debug_ai_call("PAIRWISE", pairwise_prompt, raw or "")
    return _parse_structured_response(raw, PairwiseDecision, fallback, "Plan-pairwise")





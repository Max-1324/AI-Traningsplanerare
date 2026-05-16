from __future__ import annotations

from training_plan.core.models import PlanReview, PlanScores, PlanValidationResult, ReviewDimension, ReviewFix

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

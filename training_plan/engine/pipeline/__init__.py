from __future__ import annotations

from typing import Callable

from training_plan.core.common import *
from training_plan.core.models import AIPlan, PlanDecisionTrace, PlanReview, PlanScores
from training_plan.engine.pipeline.core import (
    classify_injury,
    classify_plan_day,
    generate_plan,
    summarize_plan_candidate,
)
from training_plan.engine.pipeline.prompts import (
    _candidate_specs,
    build_candidate_prompt,
    build_pairwise_prompt,
    build_review_prompt,
    build_revision_prompt,
    build_tss_gap_revision_prompt,
    filter_review_context,
)
from training_plan.engine.pipeline.reviews import compare_plans, review_plan
from training_plan.engine.pipeline.scoring import compute_scores_from_review, decide_plan
from training_plan.engine.pipeline.candidates import (
    _apply_tss_gap_revision as _apply_tss_gap_revision_impl,
    _candidate_change_reason,
    _candidate_rank,
    _candidate_round_line,
    _is_meaningful_improvement,
    _pairwise_rank_adjustment,
    _pairwise_reason_text,
    _pick_round_winner,
    _score_delta_text,
    _should_run_pairwise,
)
from training_plan.engine.pipeline.outcomes import record_plan_decision, update_plan_outcome_tracking
from training_plan.engine.validation import (
    build_validation_review,
    build_validation_scores,
    repair_postprocessed_plan,
    validate_postprocessed_plan,
)

_FIRST_ROUND_GENERATION_TEMPERATURE = float(os.getenv("PLAN_FIRST_ROUND_TEMPERATURE", "0.35"))
_REVISION_GENERATION_TEMPERATURE = float(os.getenv("PLAN_REVISION_TEMPERATURE", "0.15"))
_EARLY_STOP_PATIENCE = int(os.getenv("PLAN_EARLY_STOP_PATIENCE", "2"))


def _apply_tss_gap_revision(*args, **kwargs):
    """Compatibility wrapper so tests patching pipeline.generate_plan still work."""
    import training_plan.engine.pipeline.candidates as candidate_module

    original_generate_plan = candidate_module.generate_plan
    candidate_module.generate_plan = generate_plan
    try:
        return _apply_tss_gap_revision_impl(*args, **kwargs)
    finally:
        candidate_module.generate_plan = original_generate_plan

def run_plan_pipeline(gen_provider: str, review_provider: str, generation_prompt: str,
                      postprocess_candidate: Callable[[AIPlan], tuple[AIPlan, list[str]]],
                      athlete: dict | None, base_tss_by_date: dict[str, float],
                      tss_budget: float,
                      review_context: dict, max_iterations: int = 5,
                      candidate_count: int = 2) -> tuple[AIPlan, list[str], PlanDecisionTrace]:
    max_iterations = max(1, max_iterations)
    candidate_count = max(1, candidate_count)
    best_candidate: tuple[AIPlan, list[str], PlanDecisionTrace, float] | None = None
    current_plan: AIPlan | None = None
    current_changes: list[str] = []
    revision_history: list[str] = []
    last_trace: PlanDecisionTrace | None = None
    candidate_specs = _candidate_specs(candidate_count)
    stagnant_rounds = 0

    for attempt in range(1, max_iterations + 1):
        previous_best_trace = best_candidate[2] if best_candidate else None
        if attempt == 1:
            log.info("🧠 Creating original plan (Attempt %s/%s). Generating %s candidates...", attempt, max_iterations, candidate_count)
            round_base_prompt = generation_prompt
            generation_temperature = _FIRST_ROUND_GENERATION_TEMPERATURE
            round_candidate_specs = candidate_specs
        else:
            review = last_trace.review if last_trace and last_trace.review else PlanReview()
            scores = last_trace.scores if last_trace and last_trace.scores else PlanScores(
                effectiveness=5,
                risk=5,
                specificity=5,
                simplicity=5,
                confidence=3,
                rationale="Fallback before revision.",
                action_hint="REVISE",
            )
            action = last_trace.action if last_trace else "REVISE"
            log.info(f"🔁 Revision round {attempt}/{max_iterations} (Decision: {action}) - AI is rebuilding the plan...")
            round_base_prompt = build_revision_prompt(
                generation_prompt,
                current_plan or best_candidate[0],
                review,
                scores,
                action,
                attempt,
                current_changes,
            )
            generation_temperature = _REVISION_GENERATION_TEMPERATURE
            round_candidate_specs = candidate_specs[:1]

        round_results = []
        round_best_plan: AIPlan | None = None
        round_best_trace: PlanDecisionTrace | None = None
        round_best_rank: float = float("-inf")
        for candidate_spec in round_candidate_specs:
            candidate_prompt = build_candidate_prompt(
                round_base_prompt,
                candidate_spec,
                attempt,
                len(round_candidate_specs),
            )
            try:
                candidate_plan = generate_plan(
                    gen_provider,
                    candidate_prompt,
                    temperature=generation_temperature,
                )
                candidate_plan, candidate_changes = postprocess_candidate(candidate_plan)
                candidate_plan, candidate_changes = _apply_tss_gap_revision(
                    candidate_plan,
                    candidate_changes,
                    gen_provider,
                    generation_prompt,
                    postprocess_candidate,
                    athlete,
                    base_tss_by_date,
                    tss_budget,
                    review_context,
                    attempt,
                )

                validation = validate_postprocessed_plan(
                    candidate_plan,
                    athlete=athlete,
                    base_tss_by_date=base_tss_by_date,
                    tss_budget=tss_budget,
                    review_context=review_context,
                    postprocess_changes=candidate_changes,
                )
                if validation.hard_failures or validation.warnings:
                    repaired_plan, repair_actions = repair_postprocessed_plan(
                        candidate_plan,
                        review_context=review_context,
                        validation=validation,
                    )
                    if repair_actions:
                        candidate_plan = repaired_plan
                        candidate_changes.extend(repair_actions)
                        validation = validate_postprocessed_plan(
                            candidate_plan,
                            athlete=athlete,
                            base_tss_by_date=base_tss_by_date,
                            tss_budget=tss_budget,
                            review_context=review_context,
                            postprocess_changes=candidate_changes,
                        )
                if validation.passed:
                    review = review_plan(
                        review_provider,
                        candidate_plan,
                        athlete,
                        base_tss_by_date,
                        review_context,
                        candidate_changes,
                    )
                    # Use deterministic scoring instead of AI call (saves 33% of API calls)
                    scores = compute_scores_from_review(
                        review,
                        plan=candidate_plan,
                        review_context=review_context,
                    )
                    action, rationale = decide_plan(review, scores, candidate_changes)
                else:
                    review = build_validation_review(validation)
                    scores = build_validation_scores(validation)
                    action = "REJECT"
                    rationale = validation.summary

                rank = _candidate_rank(review, scores, candidate_changes)
                pairwise = None
                # Pairwise reference: within round 1 compare against the best candidate so far in this
                # round; in revision rounds compare against the carried-over best from previous rounds.
                pairwise_ref_plan = round_best_plan or current_plan
                pairwise_ref_trace = round_best_trace or previous_best_trace
                if (
                    validation.passed
                    and pairwise_ref_plan
                    and _should_run_pairwise(pairwise_ref_trace, scores, review, candidate_changes)
                ):
                    pairwise = compare_plans(
                        review_provider,
                        pairwise_ref_plan,
                        pairwise_ref_trace,
                        candidate_plan,
                        review,
                        scores,
                        athlete,
                        base_tss_by_date,
                        review_context,
                        candidate_changes,
                    )
                    rank += _pairwise_rank_adjustment(pairwise)
                if rank > round_best_rank:
                    round_best_rank = rank
                    round_best_plan = candidate_plan
                    round_best_trace = PlanDecisionTrace(
                        action=action,
                        rationale=rationale,
                        iterations_run=attempt,
                        used_with_override=False,
                        selected_candidate=candidate_spec["label"],
                        validator_summary=validation.summary,
                        validator_failures=list(validation.hard_failures),
                        validator_warnings=list(validation.warnings),
                        review=review,
                        scores=scores,
                    )
                round_results.append({
                    "label": candidate_spec["label"],
                    "focus": candidate_spec["focus"],
                    "plan": candidate_plan,
                    "changes": list(candidate_changes),
                    "review": review,
                    "scores": scores,
                    "action": action,
                    "rationale": rationale,
                    "rank": rank,
                    "pairwise": pairwise,
                    "validation": validation,
                })
                candidate_reason = _candidate_change_reason(previous_best_trace, round_results[-1])
                log.info(
                    "🧪 %s -> %s | Effect %s/10 | Risk %s/10 | Spec %s/10 | Simplicity %s/10 | Confidence %s/10",
                    candidate_spec["label"],
                    action,
                    scores.effectiveness,
                    scores.risk,
                    scores.specificity,
                    scores.simplicity,
                    scores.confidence,
                )
                log.info("   Δ %s", _score_delta_text(previous_best_trace.scores if previous_best_trace else None, scores))
                log.info("   Why: %s", candidate_reason)
                if validation.hard_failures:
                    log.info("   Validator: %s", validation.summary)
                    for item in validation.hard_failures[:3]:
                        log.info("   Validation fail: %s", item)
                elif validation.warnings:
                    log.info("   Validator: %s", validation.summary)
                    for item in validation.warnings[:2]:
                        log.info("   Validation warning: %s", item)
                repair_lines = [item for item in candidate_changes if item.startswith("AUTO-REPAIR:")]
                if repair_lines:
                    for item in repair_lines[:4]:
                        log.info("   %s", item)
                if pairwise:
                    log.info("   Pairwise: %s", _pairwise_reason_text(pairwise))
            except Exception as exc:
                log.warning("   %s skipped after generation/review failure: %s", candidate_spec["label"], exc)
                continue

        if not round_results:
            if best_candidate is not None:
                log.warning("⚠️ Round %s produced no usable candidates. Keeping previous best candidate.", attempt)
                break
            raise RuntimeError("No valid plan candidates could be generated or parsed.")

        round_summary = [
            _candidate_round_line(
                result["label"], result["action"], result["scores"], result["review"], result["focus"]
            ) + (
                f" | Validator FAIL ({len(result['validation'].hard_failures)})"
                if result["validation"].hard_failures else
                (
                    f" | Validator WARN ({len(result['validation'].warnings)})"
                    if result["validation"].warnings else ""
                )
            ) + (
                f" | {_pairwise_reason_text(result['pairwise'])}"
                if result.get("pairwise") else ""
            )
            for result in round_results
        ]
        winner = _pick_round_winner(round_results)
        winner_reason = _candidate_change_reason(previous_best_trace, winner)
        score_delta = _score_delta_text(previous_best_trace.scores if previous_best_trace else None, winner["scores"])
        pairwise_text = _pairwise_reason_text(winner.get("pairwise")) if winner.get("pairwise") else ""
        meaningful_improvement = _is_meaningful_improvement(previous_best_trace, winner)
        current_plan = winner["plan"]
        current_changes = winner["changes"]
        revision_history.append(
            f"Round {attempt}: chose {winner['label']} ({winner['focus']}) -> {winner['action']} | "
            f"Effect {winner['scores'].effectiveness}/10 | Risk {winner['scores'].risk}/10 | "
            f"Spec {winner['scores'].specificity}/10 | Simplicity {winner['scores'].simplicity}/10 | "
            f"Confidence {winner['scores'].confidence}/10 | {score_delta} | Why: {winner_reason}"
            + (f" | {pairwise_text}" if pairwise_text else "")
        )

        trace = PlanDecisionTrace(
            action=winner["action"],
            rationale=(
                f"{winner['rationale']} | {score_delta} | Why: {winner_reason}"
                + (f" | {pairwise_text}" if pairwise_text else "")
            ),
            iterations_run=attempt,
            used_with_override=False,
            selected_candidate=winner["label"],
            historical_validation_summary=review_context.get("historical_validation_summary", ""),
            outcome_tracking_summary=review_context.get("outcome_tracking_summary", ""),
            validator_summary=winner["validation"].summary,
            validator_failures=list(winner["validation"].hard_failures),
            validator_warnings=list(winner["validation"].warnings),
            review=winner["review"],
            scores=winner["scores"],
            candidate_pool_summary=round_summary,
            revision_history=list(revision_history),
        )
        winner_rank = winner["rank"]

        if best_candidate is None or winner_rank > best_candidate[3]:
            best_candidate = (current_plan, list(current_changes), trace, winner_rank)

        # Keep plan, changes, and trace aligned from the same historical winner.
        current_plan = best_candidate[0]
        current_changes = list(best_candidate[1])
        last_trace = best_candidate[2]

        log.info("🏁 Round %s winner: %s", attempt, _candidate_round_line(
            winner["label"], winner["action"], winner["scores"], winner["review"], winner["focus"]
        ))
        log.info("   Round delta: %s", score_delta)
        log.info("   Round why: %s", winner_reason)
        if pairwise_text:
            log.info("   Round pairwise: %s", pairwise_text)

        if winner["action"] == "ACCEPT":
            accepted_plan = winner["plan"].model_copy(update={"decision_trace": trace})
            return accepted_plan, winner["changes"], trace

        if meaningful_improvement:
            stagnant_rounds = 0
        else:
            stagnant_rounds += 1
            log.info(
                "   Early-stop watch: no meaningful improvement this round (%s/%s)",
                stagnant_rounds,
                _EARLY_STOP_PATIENCE,
            )
            if attempt >= 2 and stagnant_rounds >= _EARLY_STOP_PATIENCE:
                log.info("⏹️ Early stopping: revisions have plateaued.")
                break

    assert best_candidate is not None
    best_plan, best_changes, best_trace, _ = best_candidate
    if best_trace.validator_failures:
        log.error("❌ No candidate passed deterministic validation. Refusing override write path.")
        raise RuntimeError("No candidate passed deterministic validation.")
    best_trace = best_trace.model_copy(update={
        "used_with_override": True,
        "revision_history": list(revision_history) + [
            "No candidate reached ACCEPT within max iterations; best revised version used with caution."
        ],
    })
    log.warning("⚠️ No plan reached ACCEPT. Using best revised candidate with override.")
    return best_plan.model_copy(update={"decision_trace": best_trace}), best_changes, best_trace



__all__ = [
    "_apply_tss_gap_revision",
    "build_candidate_prompt",
    "build_pairwise_prompt",
    "build_review_prompt",
    "build_revision_prompt",
    "build_tss_gap_revision_prompt",
    "classify_injury",
    "classify_plan_day",
    "compare_plans",
    "compute_scores_from_review",
    "decide_plan",
    "filter_review_context",
    "generate_plan",
    "record_plan_decision",
    "review_plan",
    "run_plan_pipeline",
    "summarize_plan_candidate",
    "update_plan_outcome_tracking",
]

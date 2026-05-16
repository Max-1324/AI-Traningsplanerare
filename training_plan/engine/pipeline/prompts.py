from __future__ import annotations

from training_plan.core.common import *
from training_plan.core.models import AIPlan, PlanReview, PlanScores
from training_plan.engine.pipeline.core import summarize_plan_candidate

_CANDIDATE_VARIATIONS = [
    {
        "label": "Candidate A",
        "focus": "Balanced with protected key sessions",
        "instructions": [
            "Build a balanced plan that protects 3-4 key sessions per week.",
            "Use 80/20 polarization: 80% easy endurance, 20% structured intensity.",
            "Include test-and-adjust feedback loops; skip non-critical filler.",
        ],
    },
    {
        "label": "Candidate B",
        "focus": "Conservative recovery-first approach",
        "instructions": [
            "Minimize load: reduce weekly TSS by 15% from budget while hitting must-hit sessions.",
            "Maximize recovery window between hard sessions (2+ days easy minimum).",
            "Emphasize sleep/HRV feedback over aggressive periodization.",
        ],
    },
    {
        "label": "Candidate C",
        "focus": "Race-specific preparation",
        "instructions": [
            "Weight race demands more heavily than the block objective when they conflict.",
            "Use race-specific intensity distribution while respecting all safety rules and vetoes.",
            "Keep risk acceptable; do not trade safety for TSS or specificity.",
        ],
    },
]

def _plan_for_prompt(plan: AIPlan) -> str:
    return json.dumps(plan.model_dump(exclude={"decision_trace"}, exclude_none=True), ensure_ascii=False, indent=2)


def filter_review_context(review_context: dict, include_fields: list[str] | None = None) -> dict:
    """
    Filter review_context to only essential fields for a specific prompt stage.
    Reduces token waste from redundant context.

    Args:
        review_context: Full context from main.py
        include_fields: List of fields to include. If None, returns all.

    Returns:
        Filtered context dict with only relevant fields.
    """
    default_review_fields = [
        "phase", "mesocycle", "trajectory", "block_objective",
        "development_needs", "race_demands", "readiness", "motivation",
        "compliance", "coach_confidence", "session_quality", "capacity_map",
        "performance_forecast", "race_readiness", "failure_memory_summary"
    ]

    if include_fields is None:
        include_fields = default_review_fields

    filtered = {}
    for field in include_fields:
        if field in review_context:
            filtered[field] = review_context[field]

    return filtered


def _compact_context(review_context: dict) -> str:
    return json.dumps(review_context, ensure_ascii=False, indent=2, default=str)


def _candidate_specs(candidate_count: int) -> list[dict]:
    specs = []
    for idx in range(candidate_count):
        base = _CANDIDATE_VARIATIONS[idx % len(_CANDIDATE_VARIATIONS)]
        specs.append({
            "label": f"Candidate {chr(65 + idx)}",
            "focus": base["focus"],
            "instructions": list(base["instructions"]),
        })
    return specs


def build_candidate_prompt(base_prompt: str, candidate_spec: dict, attempt: int, total_candidates: int) -> str:
    instructions = "\n".join(f"- {line}" for line in candidate_spec["instructions"])
    round_label = "initial generation" if attempt == 1 else f"revision round {attempt}"
    return f"""
ROLE: You are generating {candidate_spec['label']} of {total_candidates} competing candidates ({round_label}).

The candidates will be reviewed and scored independently. The best one wins.
Therefore your plan must be genuinely distinct — not a cosmetic copy of another candidate.

FOCUS FOR THIS CANDIDATE:
{candidate_spec['focus']}
{instructions}

REQUIREMENTS:
- Keep exactly the same JSON schema as the brief requires.
- The difference from other candidates must be visible in session prioritization, volume distribution, intensity profile, or risk appetite.
- Do not pad sessions or add filler just to meet TSS — extend real sessions instead.

BRIEF:
{base_prompt}
""".strip()


def build_review_prompt(plan: AIPlan, athlete: dict | None, base_tss_by_date: dict[str, float],
                        review_context: dict, postprocess_changes: list[str]) -> str:
    plan_summary = summarize_plan_candidate(plan, athlete, base_tss_by_date)
    changes_text = "\n".join(f"- {c}" for c in postprocess_changes) if postprocess_changes else "- No postprocess changes"
    return f"""
ROLE: You are an independent and skeptical review coach for endurance planning.
You did NOT create the plan below. Your task is to find errors, blind spots, unnecessary complexity, and overconfidence.
Be especially vigilant of plans that optimize the wrong thing, hide filler sessions, or appear overconfident despite weak data.

EVALUATE THE PLAN BASED ON:
A. Goal alignment
B. Key sessions
C. Efficiency
D. Load & risk
E. Individualization
F. Race demands

COUNTERFACTUAL THINKING:
- Is there a simpler plan with similar effect?
- What happens if the volume is reduced but the quality is maintained?
- What happens if the focus shifts from the current primary focus to the best alternative?

IMPORTANT ABOUT SAFETY RULES AND POSTPROCESSING:
The above plan has already run through Python's strict safety rules. If you see in "POSTPROCESSING ALREADY APPLIED" that the code was forced to overwrite sessions (e.g. converted to Z1 due to "HARD-EASY", reduced time due to "CAP", or changed sport due to "STRENGTH_LIMIT" / "ACWR-VETO"), it means the original plan violated physiological laws.
If such rule violations have occurred, you MUST fail the plan (set overall_verdict to REVISE or REJECT) and add the rule violation as a "must_fix". Never accept a plan that Python had to "cut to pieces", but force a revision where the AI builds the puzzle neatly and legally from the start! (Exceptions: "Illness", "LOCKED DATE" and "RETURN TO PLAY" are OK).

IMPORTANT:
- Do not just reward plans that "sound coach-like".
- If the data foundation is uncertain, it should be visible in the review.
- Filler sessions should be penalized.
- Must-hit sessions must be clearly protected.
- For every meaningful must-fix, explain the required change and what must be preserved while fixing it.
- DO NOT penalize sessions several days into the future based on today's low readiness/HRV. You may only require changes (must_fix) for high intensity if they are TODAY or TOMORROW.
- Even if the "primary_focus" for the block happens to be 'recovery', this ONLY applies short-term. You may NOT fail an FTP test or key session 4+ days into the future citing a 'recovery phase'.
- Avoid conditional must-fixes (e.g. "change this IF form does not improve"). Either the session is a direct error today, or you approve it.
- NEVER use `must_fix` to warn about behaviors (e.g. "make sure this doesn't become a habit") or future concerns. A `must_fix` may ONLY point to a concrete, physiological error in the plan.
- NEVER use `must_fix` for nutrition advice or vague power target personalization ("personalize based on physiology"). Power targets are only a `must_fix` if the provided context contains enough FTP/zone data to verify a specific error (e.g. "4×8min set to 280W but FTP is 230W"). Vague personalization advice belongs in `coaching_advice`.
- If you have philosophical advice, warnings about the future, or minor feedback, put them in `coaching_advice` instead of `must_fix`.

CALIBRATION — read this before you assign ratings:
- CRITICAL is reserved for true structural violations: physiological impossibility, injury risk, direct contradiction of race goal, or a session that is concretely wrong TODAY. If you are tempted to use CRITICAL for a future session, a minor imbalance, or a preference, use WEAK instead.
- A plan that is reasonable and mostly sound deserves overall_verdict PASS even if it is not perfect. REVISE is for plans with at least one genuine must-fix. REJECT is for plans that are fundamentally broken and must be discarded.
- An empty must_fix list is a valid and encouraged outcome for a good plan.

KONTEXT:
{_compact_context(review_context)}

PLAN METRICS:
{json.dumps(plan_summary, ensure_ascii=False, indent=2)}

POSTPROCESSING ALREADY APPLIED:
{changes_text}

CANDIDATE PLAN:
{_plan_for_prompt(plan)}

Return ONLY JSON with exactly this schema:
{{
  "summary": "2-4 sentences with a clear main verdict",
  "goal_alignment": {{"rating": "STRONG|ADEQUATE|WEAK|CRITICAL", "rationale": "", "issues": [""], "recommendations": [""]}},
  "key_sessions": {{"rating": "STRONG|ADEQUATE|WEAK|CRITICAL", "rationale": "", "issues": [""], "recommendations": [""]}},
  "efficiency": {{"rating": "STRONG|ADEQUATE|WEAK|CRITICAL", "rationale": "", "issues": [""], "recommendations": [""]}},
  "load_and_risk": {{"rating": "STRONG|ADEQUATE|WEAK|CRITICAL", "rationale": "", "issues": [""], "recommendations": [""]}},
  "individualization": {{"rating": "STRONG|ADEQUATE|WEAK|CRITICAL", "rationale": "", "issues": [""], "recommendations": [""]}},
  "race_demands": {{"rating": "STRONG|ADEQUATE|WEAK|CRITICAL", "rationale": "", "issues": [""], "recommendations": [""]}},
  "strengths": ["max 4 concrete strengths"],
  "protected_elements": ["what the revision must preserve because these parts are already right or strategically important"],
  "coaching_advice": ["minor feedback, tips and future warnings that DO NOT require immediate rebuilding"],
  "review_fixes": [
    {{
      "issue": "concrete problem",
      "severity": "MEDIUM|HIGH|CRITICAL",
      "required_change": "what must change in the revised plan",
      "protected_elements": ["what must not be lost while fixing this"],
      "evidence": "brief physiological or structural reason"
    }}
  ],
  "must_fix": ["the most important thing that must be changed before the plan can be trusted"],
  "uncertainty_sources": ["what makes you uncertain"],
  "counterfactuals": [
    {{"question": "Is there a simpler plan with similar effect?", "answer": "", "tradeoffs": "", "recommendation": ""}},
    {{"question": "What happens if volume is reduced but quality is maintained?", "answer": "", "tradeoffs": "", "recommendation": ""}},
    {{"question": "What happens if focus shifts to the best alternative?", "answer": "", "tradeoffs": "", "recommendation": ""}}
  ],
  "overall_verdict": "PASS|REVISE|REJECT"
}}
""".strip()


def build_pairwise_prompt(current_plan: AIPlan, current_review: PlanReview, current_scores: PlanScores,
                          candidate_plan: AIPlan, candidate_review: PlanReview, candidate_scores: PlanScores,
                          athlete: dict | None, base_tss_by_date: dict[str, float],
                          review_context: dict, candidate_changes: list[str]) -> str:
    current_summary = summarize_plan_candidate(current_plan, athlete, base_tss_by_date)
    candidate_summary = summarize_plan_candidate(candidate_plan, athlete, base_tss_by_date)
    filtered_context = filter_review_context(review_context)
    changes_text = "\n".join(f"- {c}" for c in candidate_changes) if candidate_changes else "- No postprocess changes"
    return f"""
ROLE: You are a strict pairwise planning judge.
Your job is NOT to do a fresh open-ended review. Your job is to decide if the CANDIDATE is ACTUALLY BETTER than CURRENT.

IMPORTANT:
- Prefer the plan that solves must-fix items without introducing new regressions.
- Protect key sessions and goal alignment over cosmetic differences.
- Penalize candidates that add new must-fix problems, lose specificity, increase risk, or trigger postprocess vetoes.
- Only pick CANDIDATE if there is a real net improvement, not just a different writing style.
- If the improvement is unclear or mixed, return TIE.

DECISION PRIORITY:
1. Must-fix resolved vs must-fix added
2. Goal alignment and key sessions
3. Risk and veto avoidance
4. Specificity
5. Simplicity and confidence

CONTEXT:
{_compact_context(filtered_context)}

CURRENT PLAN METRICS:
{json.dumps(current_summary, ensure_ascii=False, indent=2)}

CURRENT REVIEW:
{json.dumps(current_review.model_dump(exclude_none=True), ensure_ascii=False, indent=2)}

CURRENT SCORES:
{json.dumps(current_scores.model_dump(exclude_none=True), ensure_ascii=False, indent=2)}

CANDIDATE PLAN METRICS:
{json.dumps(candidate_summary, ensure_ascii=False, indent=2)}

CANDIDATE REVIEW:
{json.dumps(candidate_review.model_dump(exclude_none=True), ensure_ascii=False, indent=2)}

CANDIDATE SCORES:
{json.dumps(candidate_scores.model_dump(exclude_none=True), ensure_ascii=False, indent=2)}

CANDIDATE POSTPROCESSING:
{changes_text}

Return ONLY JSON with exactly this schema:
{{
  "better_plan": "CURRENT|CANDIDATE|TIE",
  "confidence": 0,
  "summary": "1-3 sentences on why one plan is better or why it is a tie",
  "improved_areas": ["what candidate improved"],
  "regressions": ["what candidate made worse"],
  "must_fix_resolved": ["previous must-fix items that candidate resolved"],
  "must_fix_added": ["new must-fix items candidate introduced"]
}}
""".strip()


def build_revision_prompt(base_generation_prompt: str, plan: AIPlan, review: PlanReview,
                          scores: PlanScores, action: str, attempt: int,
                          postprocess_changes: list[str]) -> str:
    hard_reset = action == "REJECT"
    surgical = action == "REVISE" and scores.effectiveness >= 7 and scores.specificity >= 7
    changes_text = "\n".join(f"- {c}" for c in postprocess_changes) if postprocess_changes else "- No postprocess changes"
    
    if hard_reset:
        revision_mode = "DISCARD the previous structure and rebuild the plan from scratch."
    elif surgical:
        revision_mode = "SURGICAL REVISION: This plan is almost perfect. You may ONLY change exactly what is mentioned in must-fix. Do absolutely not touch anything else in the weekly structure. DO NOT lower the total load (TSS)."
    else:
        revision_mode = "Keep only the parts that are still clearly defensible. Actively revise the rest."
    return f"""
ROLE: You are the revision planner. You MUST improve the plan based on independent review and scoring.
Do not try to defend the old plan. If the review says something is weak or wrong, fix it.

REVISION ROUND: {attempt}
REQUIREMENTS:
- Address must-fix first
- Follow each review_fixes.required_change explicitly
- Protect review.protected_elements and each fix item's protected_elements unless a physiological veto forces a change
- Protect the right must-hit sessions
- Remove filler sessions
- Simplify if the same effect can be achieved with less friction
- Show uncertainty in summary when data foundation is uncertain
- If action is REJECT, rethink from scratch

REVISION MODE:
{revision_mode}

CURRENT PLAN:
{_plan_for_prompt(plan)}

REVIEW:
{json.dumps(review.model_dump(exclude_none=True), ensure_ascii=False, indent=2)}

SCORES:
{json.dumps(scores.model_dump(exclude_none=True), ensure_ascii=False, indent=2)}

POSTPROCESSING ALREADY APPLIED:
{changes_text}

ORIGINAL PLANNING BRIEF:
{base_generation_prompt}

Return ONLY the exact same AIPlan JSON schema that the original prompt requires.
""".strip()


def build_tss_gap_revision_prompt(base_generation_prompt: str, plan: AIPlan,
                                  missing_tss: int, total_tss: float, target_tss: float,
                                  postprocess_changes: list[str], attempt: int) -> str:
    changes_text = "\n".join(f"- {c}" for c in postprocess_changes) if postprocess_changes else "- No postprocess changes"
    plan_summary = summarize_plan_candidate(plan)
    return f"""
ROLE: You are the load-balancing revision planner.
Your only job is to revise the current plan so it closes a large TSS gap in a coach-like way.

TSS GAP ALERT:
- Current total TSS including locked/manual sessions: {round(total_tss)}
- Target budget: {round(target_tss)}
- Missing TSS: {missing_tss}
- Revision round: {attempt}

NON-NEGOTIABLE RULES:
- Preserve the key sessions and overall block intent unless there is a physiological conflict.
- Increase load mainly by extending existing Z2/endurance sessions and long rides.
- Prefer fewer, longer, more coherent endurance sessions over adding many small filler sessions.
- Do NOT add "repair" steps, "extension" filler blocks, or artificial padding language.
- If volume must be added, fold it into the main session structure so the final plan reads naturally.
- Do not solve the gap by adding extra intensity unless absolutely necessary.
- Keep recovery logic intact around hard sessions and tests.

CURRENT PLAN:
{_plan_for_prompt(plan)}

CURRENT PLAN SUMMARY:
{json.dumps(plan_summary, ensure_ascii=False, indent=2)}

POSTPROCESS SIGNALS:
{changes_text}

ORIGINAL PLANNING BRIEF:
{base_generation_prompt}

Return ONLY the exact same AIPlan JSON schema that the original prompt requires.
""".strip()


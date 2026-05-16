import training_plan.core.common as common
from training_plan.core.common import *
from training_plan.engine.context import PromptContext
from training_plan.engine.libraries import *
from training_plan.engine.planning import *
from training_plan.engine.analysis import *
from training_plan.engine.skeleton import format_skeleton_for_prompt
from training_plan.engine.utils import strip_planner_comment_block, read_wellness_score

def _build_key_session_directive(
    block_objective: dict,
    development_needs: dict,
    minimum_effective_dose: dict,
    mesocycle: dict | None,
    planning_day_count: int,
) -> str:
    """
    Produce a KEY / SUPPORT / RECOVERY priority table for the AI prompt.

    KEY sessions are the 2-3 quality workouts that must be protected.
    SUPPORT sessions fill the volume around them.
    RECOVERY sessions are mandatory low-intensity or rest slots.
    """
    block_objective = block_objective or {}
    development_needs = development_needs or {}
    minimum_effective_dose = minimum_effective_dose or {}
    mesocycle = mesocycle or {}

    is_deload = mesocycle.get("is_deload", False)
    weeks = max(1, round(planning_day_count / 7))

    # Gather candidate key sessions from multiple sources, in priority order
    key_candidates: list[str] = []
    for src in (
        block_objective.get("must_hit_sessions", []),
        minimum_effective_dose.get("must_hit_sessions", []),
        development_needs.get("must_hit_sessions", []),
    ):
        for s in src:
            if s and s not in key_candidates:
                key_candidates.append(s)

    flex_sessions: list[str] = block_objective.get("flex_sessions", [])

    # During deload: cap key sessions at 1 per week, no high-intensity
    max_key = weeks * 1 if is_deload else weeks * 2
    key_sessions = key_candidates[:max_key] or ([] if is_deload else ["Zone 2 endurance"])
    support_sessions = flex_sessions[:3] or ["Zone 2 endurance fill"]

    primary_focus = (
        block_objective.get("primary_focus")
        or development_needs.get("primary_focus")
        or "durability"
    )

    deload_note = "\n  ⚠️ DELOAD WEEK: No Z4+ in KEY slots. Reduce volume 35-40%." if is_deload else ""

    lines = [
        "SESSION PRIORITY FOR THIS PLAN:",
        f"  Primary coaching focus: {primary_focus}{deload_note}",
        "  KEY sessions (protect these — schedule first, never skip, never stack back-to-back):",
    ]
    for ks in key_sessions:
        lines.append(f"    • {ks}")
    lines.append("  SUPPORT sessions (volume fillers around KEY sessions — can be shortened if fatigued):")
    for ss in support_sessions:
        lines.append(f"    • {ss}")
    lines.append("  RECOVERY slots (mandatory — never replace with a key session):")
    lines.append(f"    • {max(1, weeks)} rest or Z1 day(s) per week, placed the day after each KEY session.")
    lines.append("  Scheduling rule: KEY → RECOVERY → SUPPORT → repeat. Never place two KEY sessions on consecutive days.")

    return "\n".join(lines)


def _build_planner_insights_section(planner_insights: dict | None) -> str:
    """Format higher-level planner signals as one compact prompt section."""
    planner_insights = planner_insights or {}
    if not planner_insights:
        return ""

    capacity_map = planner_insights.get("capacity_map", {})
    nutrition_readiness = planner_insights.get("nutrition_readiness", {})
    individualization_profile = planner_insights.get("individualization_profile", {})
    minimum_effective_dose = planner_insights.get("minimum_effective_dose", {})
    execution_friction = planner_insights.get("execution_friction", {})
    training_frequency_target = planner_insights.get("training_frequency_target", {})
    benchmark_system = planner_insights.get("benchmark_system", {})
    block_learning = planner_insights.get("block_learning", {})
    performance_forecast = planner_insights.get("performance_forecast", {})
    race_readiness = planner_insights.get("race_readiness", {})
    season_plan = planner_insights.get("season_plan", {})

    lines = ["PLANNER INSIGHTS:"]

    if capacity_map:
        lines.append("  CAPACITY MAP:")
        for area in capacity_map.get("areas", [])[:6]:
            lines.append(
                f"    - {area.get('name')}: {area.get('score')}/100 [{area.get('status')}] - {area.get('meaning')}"
            )
        lines.append(
            f"    Strongest: {', '.join(capacity_map.get('strongest', [])) or 'Unknown'} | "
            f"Weakest: {', '.join(capacity_map.get('weakest', [])) or 'Unknown'}"
        )

    if performance_forecast:
        lines.extend([
            "  PERFORMANCE FORECAST:",
            f"    {performance_forecast.get('summary', '')}",
            f"    Assumptions: {' | '.join(performance_forecast.get('assumptions', [])[:3]) or 'No explicit assumptions'}",
            f"    Risks: {' | '.join(performance_forecast.get('risks', [])[:3]) or 'No major forecast risks'}",
        ])

    if race_readiness:
        lines.extend([
            "  RACE READINESS:",
            f"    {race_readiness.get('summary', '')}",
        ])

    if benchmark_system:
        lines.extend([
            "  BENCHMARK SYSTEM:",
            f"    {benchmark_system.get('summary', '')}",
        ])
        for item in benchmark_system.get("benchmarks", [])[:3]:
            lines.append(
                f"    - {item.get('name')} ({item.get('priority')} in ~{item.get('due_in_days')}d): "
                f"{item.get('session')} | Why: {item.get('purpose')}"
            )

    if minimum_effective_dose:
        lines.extend([
            "  MINIMUM EFFECTIVE DOSE:",
            f"    {minimum_effective_dose.get('summary', '')}",
            f"    Must-protect: {' | '.join(minimum_effective_dose.get('must_hit_sessions', [])) or 'No explicit must-hit sessions'}",
        ])

    if execution_friction:
        lines.extend([
            "  EXECUTION FRICTION:",
            f"    {execution_friction.get('summary', '')}",
            f"    Friction factors: {' | '.join(execution_friction.get('risk_factors', [])[:4]) or 'Low baseline friction'}",
        ])

    if training_frequency_target:
        lines.extend([
            "  TRAINING STRUCTURE TARGET:",
            f"    {training_frequency_target.get('summary', '')}",
            f"    Training days: {training_frequency_target.get('min_training_days', '?')}-{training_frequency_target.get('max_training_days', '?')}",
            f"    Rest days: {training_frequency_target.get('min_rest_days', '?')}-{training_frequency_target.get('max_rest_days', '?')}",
            f"    Double days max: {training_frequency_target.get('max_double_days', '?')}",
        ])

    if individualization_profile:
        lines.extend([
            "  INDIVIDUALIZATION:",
            f"    {individualization_profile.get('summary', '')}",
        ])
        if individualization_profile.get("positive_signals"):
            lines.append("    Positive signals:")
            lines.extend(f"      - {item}" for item in individualization_profile["positive_signals"][:3])
        if individualization_profile.get("caution_signals"):
            lines.append("    Caution:")
            lines.extend(f"      - {item}" for item in individualization_profile["caution_signals"][:3])

    if nutrition_readiness:
        lines.extend([
            "  NUTRITION READINESS:",
            f"    {nutrition_readiness.get('summary', '')}",
            f"    Next steps: {' | '.join(nutrition_readiness.get('next_steps', [])[:3]) or 'Maintain current race-fueling practice'}",
        ])

    if block_learning:
        lines.extend([
            "  BLOCK LEARNING:",
            f"    {block_learning.get('summary', '')}",
            f"    Worked: {' | '.join(block_learning.get('worked', [])[:3])}",
            f"    Did not work: {' | '.join(block_learning.get('did_not_work', [])[:3])}",
            f"    Next bias: {' | '.join(block_learning.get('next_bias', [])[:3]) or 'No explicit bias'}",
        ])

    if season_plan:
        lines.extend([
            "  SEASON PLAN:",
            f"    {season_plan.get('summary', '')}",
        ])
        for block in season_plan.get("blocks", [])[:4]:
            lines.append(
                f"    - {block.get('label')} ({block.get('start')} -> {block.get('end')} | {block.get('weeks')}w): "
                f"focus {block.get('focus')} | milestones: {' | '.join(block.get('milestones', [])) or 'execution'}"
            )

    return "\n" + "\n".join(lines) + "\n"


def _build_yesterday_feedback_section(yesterday_analysis: str, feedback_date: str) -> str:
    if not yesterday_analysis:
        return ""
    return f"""
YESTERDAY'S ANALYSIS (provide feedback in "yesterday_feedback"):
This refers to the session on {feedback_date}, the day before the first planned date.
{yesterday_analysis}

INSTRUCTION: Give 3-5 sentences feedback in the "yesterday_feedback" field:
  - Was the plan followed? Right sport, duration, intensity?
  - If zones/HR deviated: what can the athlete do differently?
  - Concrete tips for the next similar session.
  - If session was missed: acknowledge the reason, no guilt, look forward.
  - IMPORTANT: Do not confuse this date with travel plans or constraints that apply today or forward!
  - LANGUAGE RULE: Do NOT use the word "yesterday" in the feedback. Since the text is saved on the session's own date in the calendar, write "the session" or "today's session".
"""


def _build_double_session_rules() -> str:
    return """
DOUBLE SESSIONS & TIME OF DAY (AM/PM):
  You can choose what time of day the athlete should train ("slot": "AM", "PM" or "MAIN").
  - Adapt to the WEATHER! Raining in the afternoon but sunny in the morning? Choose "AM".
  - Double sessions are allowed only if TSB >= 0 and the athlete has not reported injuries.
  - Represent each sport as a separate JSON object with the same date and different slot.
    Never combine two sports in a single session object.
    Correct format for double session run + bike on 2026-04-05:
      {{"date":"2026-04-05","title":"Run","intervals_type":"Run","slot":"AM","duration_min":40,...}}
      {{"date":"2026-04-05","title":"Indoor bike","intervals_type":"VirtualRide","slot":"PM","duration_min":60,...}}
  - AM=lighter session (30-45min). PM=main session.
  - NEVER Z4+ on both sessions the same day.
  - Use double sessions only when they clearly improve adaptation, logistics, or recovery distribution.
"""


def _build_sports_section() -> str:
    active_types = {s["intervals_type"] for s in SPORTS}
    catalog_types = {
        s["intervals_type"] for s in ALL_SPORTS_CATALOG
        if s["intervals_type"] not in ("WeightTraining",)
    }
    unavailable = catalog_types - active_types
    unavailable_text = (
        "⚠️ NOT available: " + ", ".join(sorted(unavailable)) + "."
        if unavailable else ""
    )
    rollski_note = (
        "🎿 ROLLER SKIING: The athlete does double poling. Mention this in the description "
        "and adapt technique focus accordingly (shoulder rotation, core activation, rhythm in the pole plant)."
        if "RollerSki" in active_types else ""
    )
    sport_lines = "\n".join(
        f"  {s['name']} ({s['intervals_type']}): {s.get('comment','')}" for s in SPORTS
    )
    return f"""SPORTS:
{unavailable_text}
{rollski_note}
{sport_lines}"""


def _build_json_schema(tsb_budget: float, locked_str: str, valid_types: set[str]) -> str:
    return f"""RETURN FORMAT:
Return ONLY valid JSON. No markdown. No text outside the JSON object.
{{
  "stress_audit": "Day1=X TSS, Day2=Y TSS, ... Total=Z vs budget {tsb_budget}",
  "summary": "3-5 sentences. Focus on key decisions.",
  "yesterday_feedback": "3-5 sentences feedback ONLY if YESTERDAY'S ANALYSIS above contains actual activity data. Set '' otherwise. Do NOT use the word 'yesterday'.",
  "weekly_feedback": "3-5 sentences coach analysis of last week. Leave as '' if it is not Monday.",
  "manual_workout_nutrition": [{{"date":"YYYY-MM-DD","nutrition":"Row (based on ACTUAL duration)"}}],
  "days": [
    {{
      "date":"YYYY-MM-DD","title":"Session name",
      "intervals_type":"One of: {' | '.join(sorted(valid_types))}",
      "duration_min":60,"distance_km":0,
      "description":"2-3 sentences. For double sessions: explain why AM/PM split.",
      "nutrition":"",
      "workout_steps":[{{"duration_min":15,"zone":"Z1","description":"Warmup"}}],
      "strength_steps":[{{"exercise":"Name","sets":3,"reps":"10-12","rest_sec":60,"notes":"Technique tips"}}],
      "slot":"MAIN"
    }}
  ]
}}
CONSTRAINTS:
  - slot = "AM", "PM", or "MAIN" (default). The same date can have max 2 entries (one AM + one PM).
  - Do NOT include locked dates ({locked_str}) in "days".
  - For WeightTraining: strength_steps MUST have at least 4-6 exercises with exercise/sets/reps/rest_sec/notes.
  - workout_steps MUST be included for ALL training sessions (not WeightTraining/Rest). At least: warmup (Z1/Z2), main block (correct zone), cooldown (Z1). Interval sessions: each interval and rest as its own step.
"""



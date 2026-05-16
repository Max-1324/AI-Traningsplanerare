import training_plan.core.common as common
from training_plan.core.common import *
from training_plan.engine.planning import is_ai_generated

# ══════════════════════════════════════════════════════════════════════════════
# VISNING
# ══════════════════════════════════════════════════════════════════════════════

def print_plan(plan, changes, mesocycle=None, trajectory=None,
               acwr_trend=None, taper_score=None, race_week=None, rtp_status=None,
               planner_insights=None):
    planner_insights = planner_insights or {}
    if common.args is not None:
        gen_provider = (common.args.provider_gen or common.args.provider).upper()
        review_provider = (common.args.provider_review or common.args.provider).upper()
    else:
        gen_provider = os.getenv("AI_PROVIDER_gen_revision", os.getenv("AI_PROVIDER", "gemini")).upper()
        review_provider = os.getenv("AI_PROVIDER_review", os.getenv("AI_PROVIDER", "gemini")).upper()
    provider_label = gen_provider if gen_provider == review_provider else f"{gen_provider} gen / {review_provider} review"
    print("\n" + "="*65)
    print(f"  TRAINING PLAN v2  ({provider_label})")
    if mesocycle:
        print(f"  Block {mesocycle['block_number']}, Week {mesocycle['week_in_block']}/4"
              + (" [🟡 DELOAD]" if mesocycle['is_deload'] else ""))
    print("="*65)
    if trajectory and trajectory.get("has_target"):
        print(f"\n🎯 {trajectory['message']}")

    # ACWR-trend
    if acwr_trend and acwr_trend.get("current_zone") not in ("UNKNOWN", None):
        print(f"\n📊 {acwr_trend['summary']}")

    # Taper quality
    if taper_score and taper_score.get("is_in_taper"):
        print(f"\n📉 {taper_score['summary']}")

    # Race week
    if race_week and race_week.get("is_active"):
        print(f"\n🏁 RACE WEEK: {race_week['race_name']} in {race_week['days_to_race']}d")
        for p in race_week["protocol"]:
            steps = " → ".join(f"{s['d']}m {s['z']}" for s in p.get("steps", []))
            print(f"    {p['date']} (-{p['days_before']}d): {p['title']}")
            if steps:
                print(f"      {steps}")

    # RTP
    if rtp_status and rtp_status.get("is_active"):
        print(f"\n🚑 RETURN TO PLAY ACTIVATED: {rtp_status['days_off']} rest days in a row")

    print(f"\nStress Audit: {plan.stress_audit}\n")
    print(f"{plan.summary}\n")

    if planner_insights:
        capacity_map = planner_insights.get("capacity_map", {})
        performance_forecast = planner_insights.get("performance_forecast", {})
        race_readiness = planner_insights.get("race_readiness", {})
        minimum_effective_dose = planner_insights.get("minimum_effective_dose", {})
        execution_friction = planner_insights.get("execution_friction", {})
        benchmark_system = planner_insights.get("benchmark_system", {})
        season_plan = planner_insights.get("season_plan", {})

        if race_readiness or performance_forecast or minimum_effective_dose:
            print("PLANNER INSIGHTS:")
            if race_readiness:
                print(f"  {race_readiness.get('summary', '')}")
            if performance_forecast:
                print(f"  {performance_forecast.get('summary', '')}")
            if minimum_effective_dose:
                print(f"  {minimum_effective_dose.get('summary', '')}")
            if execution_friction:
                print(f"  {execution_friction.get('summary', '')}")
            if capacity_map:
                print(
                    "  Capacity strongest: "
                    + (", ".join(capacity_map.get("strongest", [])) or "unknown")
                    + " | weakest: "
                    + (", ".join(capacity_map.get("weakest", [])) or "unknown")
                )
            if benchmark_system.get("next_benchmark"):
                next_benchmark = benchmark_system["next_benchmark"]
                print(
                    f"  Next benchmark: {next_benchmark.get('name')} "
                    f"(~{next_benchmark.get('due_in_days')}d)"
                )
            if season_plan.get("blocks"):
                first_block = season_plan["blocks"][0]
                print(
                    f"  Season map: {season_plan.get('total_weeks', '?')}w | "
                    f"current block {first_block.get('label')} -> {first_block.get('focus')}"
                )
            print()

    if plan.decision_trace and plan.decision_trace.scores:
        trace = plan.decision_trace
        scores = trace.scores
        override = " [OVERRIDE]" if trace.used_with_override else ""
        print(f"REVIEW GATE: {trace.action}{override}")
        if trace.selected_candidate:
            print(f"  Selected candidate: {trace.selected_candidate}")
        print(
            f"  Effect {scores.effectiveness}/10 | Risk {scores.risk}/10 | "
            f"Specificity {scores.specificity}/10 | Simplicity {scores.simplicity}/10 | "
            f"Confidence {scores.confidence}/10"
        )
        if trace.review and trace.review.summary:
            print(f"  {trace.review.summary}")
        if trace.validator_summary:
            print(f"  Validator: {trace.validator_summary}")
        if trace.validator_failures:
            print(f"  Validation fails: {' | '.join(trace.validator_failures[:3])}")
        if trace.rationale:
            print(f"  Why selected: {trace.rationale}")
        if trace.review and trace.review.must_fix:
            print(f"  Must-fix: {' | '.join(trace.review.must_fix[:3])}")
        if trace.candidate_pool_summary:
            print("  Candidate pool:")
            for line in trace.candidate_pool_summary[:5]:
                print(f"    - {line}")
        print()

    # FIX #4: Visa yesterday feedback
    if plan.yesterday_feedback:
        print("📝 COACH FEEDBACK:")
        print(f"  {plan.yesterday_feedback}\n")

    if changes:
        print("POST-PROCESSING:")
        for c in changes: print(f"  {c}")
        print()
    for day in plan.days:
        emoji = EMOJIS.get(day.intervals_type, "❓")
        slot_label = f" [{day.slot}]" if day.slot != "MAIN" else ""
        print(f"{emoji} {day.date}{slot_label} - {day.title} [{day.intervals_type}]")
        print(f"    {day.duration_min}min" + (f" | {day.distance_km}km" if day.distance_km else ""))
        print(f"    {day.description}")
        for s in day.workout_steps: print(f"      * {s.duration_min}min {s.zone} - {s.description}")
        for s in day.strength_steps:
            r = f", rest {s.rest_sec}s" if s.rest_sec else ""
            n = f" - {s.notes}" if s.notes else ""
            print(f"      * {s.exercise}: {s.sets}x{s.reps}{r}{n}")
        if day.nutrition: print(f"    🍌 Nutrition: {day.nutrition}")
        print()
    print("="*65)

# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def format_existing_plan(ai_workouts: list) -> str:
    if not ai_workouts:
        return "  No existing plan – create a new one from scratch."
    lines = ["  Existing plan (AI generated):"]
    for w in sorted(ai_workouts, key=lambda x: x.get("start_date_local","")):
        d    = w.get("start_date_local","")[:10]
        name = w.get("name") or "?"
        wtype = w.get("type") or "Note"
        dur  = round((w.get("moving_time") or 0) / 60)
        # Visa inte beskrivning/AI_TAG i prompt – undviker att AI kopierar taggen
        lines.append(f"    {d} | {wtype:12} | {dur}min | {name}")
    return "\n".join(lines)

def plan_update_mode(ai_workouts, yesterday_actuals, yesterday_planned, hrv, wellness, activities, horizon) -> tuple[str, str]:
    lw = wellness[-1] if wellness else {}
    sleep_h = lw.get("sleepSecs", 0) / 3600 if lw.get("sleepSecs") else None
    if not ai_workouts:
        return "full", "No existing plan – creating new."
    if yesterday_planned and is_ai_generated(yesterday_planned):
        if not yesterday_actuals:
            return "full", "Yesterday's planned session was missed – regenerating plan."
    if hrv["state"] == "LOW":
            return "full", f"HRV = LOW ({hrv.get('today', 'N/A')} ms, {hrv['deviation_pct']}% under average) – regenerating plan."
    if sleep_h is not None and sleep_h < 5.5:
        return "full", f"Very short sleep ({sleep_h:.1f}h) – regenerating plan."
    last_act = next((a for a in reversed(activities) if a.get("perceived_exertion")), None)
    if last_act:
        rpe = last_act.get("perceived_exertion", 0)
        if rpe >= 9 and sleep_h is not None and sleep_h < 6.5:
            return "full", f"High RPE ({rpe}/10) + short sleep ({sleep_h:.1f}h) – regenerating."
    try:
        planned_dates = {
            datetime.strptime(w.get("start_date_local","")[:10], "%Y-%m-%d").date()
            for w in ai_workouts if w.get("start_date_local","")[:10]
        }
        target_end = date.today() + timedelta(days=horizon)
        missing = [
            date.today() + timedelta(days=i)
            for i in range(1, horizon + 1)
            if (date.today() + timedelta(days=i)) not in planned_dates
        ]
        if missing:
            return "extend", f"Adding {len(missing)} new day(s)."
    except Exception:
        pass
    return "none", "Plan complete and recovery normal – no changes."

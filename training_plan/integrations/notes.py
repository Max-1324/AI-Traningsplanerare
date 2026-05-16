from collections import Counter

from training_plan.core.common import *
from training_plan.engine.utils import read_wellness_score, safe_date_str, strip_planner_comment_block
from training_plan.engine.prompt_builders import sanitize
from training_plan.integrations.intervals_client import icu_get

def _build_planner_comment_block(morning):
    lines = []
    time_available = sanitize(morning.get("time_available", ""), 20)
    injury_today = sanitize(morning.get("injury_today", ""), 150)
    athlete_note = sanitize(morning.get("athlete_note", ""), 200)
    if time_available:
        lines.append(f"time_available={time_available}")
    if injury_today:
        lines.append(f"injury={injury_today}")
    if athlete_note:
        lines.append(f"athlete_note={athlete_note}")
    if not lines:
        return ""
    return (
        f"{PLANNER_COMMENT_START}\n"
        + "\n".join(lines)
        + f"\n{PLANNER_COMMENT_END}"
    )


def _merge_planner_comments(existing_comments, morning):
    base_comments = strip_planner_comment_block(existing_comments or "")
    planner_block = _build_planner_comment_block(morning)
    if base_comments and planner_block:
        return f"{base_comments}\n\n{planner_block}"
    return base_comments or planner_block




def save_morning_wellness(morning, today_wellness=None):
    today_wellness = today_wellness or {}
    payload = {}

    stress_value = read_wellness_score(
        {"stress": morning.get("life_stress", 1)},
        ("stress",),
        default=1,
    )
    current_stress = read_wellness_score(today_wellness, ("stress", "Stress"), default=None)
    if current_stress != stress_value:
        payload["stress"] = stress_value

    merged_comments = _merge_planner_comments(today_wellness.get("comments", ""), morning)
    if merged_comments != (today_wellness.get("comments", "") or ""):
        payload["comments"] = merged_comments

    if not payload:
        return

    try:
        requests.put(
            f"{BASE}/athlete/{ATHLETE_ID}/wellness/{date.today().isoformat()}",
            auth=AUTH,
            timeout=15,
            json=payload,
        ).raise_for_status()
        log.info("Wellness updated with morning responses.")
    except Exception as e:
        log.warning(f"Could not save morning wellness: {e}")

def save_daily_note_to_icu(plan, changes, planner_insights=None):
    """
    Sparar dagens sammanfattning som en NOTE idag, och gårdagens 
    feedback som en separat NOTE igår.
    """
    planner_insights = planner_insights or {}
    today_date = date.today()
    today_str = today_date.isoformat()
    yesterday_str = (today_date - timedelta(days=1)).isoformat()
    
    # --- Bygg innehåll för IDAG ---
    lines_today = ["🤖 DAILY SUMMARY:"]
    lines_today.append(plan.summary)

    if planner_insights:
        race_readiness = planner_insights.get("race_readiness", {})
        performance_forecast = planner_insights.get("performance_forecast", {})
        minimum_effective_dose = planner_insights.get("minimum_effective_dose", {})
        execution_friction = planner_insights.get("execution_friction", {})
        benchmark_system = planner_insights.get("benchmark_system", {})
        season_plan = planner_insights.get("season_plan", {})
        lines_today.append("")
        lines_today.append("Planner insights:")
        if race_readiness:
            lines_today.append(f"Race readiness: {race_readiness.get('summary', '')}")
        if performance_forecast:
            lines_today.append(f"Forecast: {performance_forecast.get('summary', '')}")
        if minimum_effective_dose:
            lines_today.append(f"MED: {minimum_effective_dose.get('summary', '')}")
            if minimum_effective_dose.get("rationale"):
                lines_today.append("MED reasons: " + " | ".join(minimum_effective_dose.get("rationale", [])[:4]))
        if execution_friction:
            lines_today.append(f"Friction: {execution_friction.get('summary', '')}")
        if benchmark_system.get("next_benchmark"):
            nb = benchmark_system["next_benchmark"]
            lines_today.append(
                f"Next benchmark: {nb.get('name')} (~{nb.get('due_in_days')}d) - {nb.get('session')}"
            )
        if season_plan.get("blocks"):
            block = season_plan["blocks"][0]
            lines_today.append(
                f"Season block: {block.get('label')} ({block.get('start')} -> {block.get('end')}) focus {block.get('focus')}"
            )

    if getattr(plan, "decision_trace", None) and plan.decision_trace and plan.decision_trace.scores:
        trace = plan.decision_trace
        scores = trace.scores
        lines_today.append("")
        lines_today.append("🧪 PLAN REVIEW:")
        lines_today.append(
            f"Decision: {trace.action}"
            + (" (override after max iterations)" if trace.used_with_override else "")
        )
        if trace.selected_candidate:
            lines_today.append(f"Selected candidate: {trace.selected_candidate}")
        lines_today.append(
            f"Scores: Effect {scores.effectiveness}/10 | Risk {scores.risk}/10 | "
            f"Specificitet {scores.specificity}/10 | Enkelhet {scores.simplicity}/10 | "
            f"Confidence {scores.confidence}/10"
        )
        if trace.review and trace.review.summary:
            lines_today.append(f"Review: {trace.review.summary}")
        if trace.validator_summary:
            lines_today.append(f"Validator: {trace.validator_summary}")
        if trace.validator_failures:
            lines_today.append("Validation fails: " + " | ".join(trace.validator_failures[:3]))
        if trace.rationale:
            lines_today.append(f"Why selected: {trace.rationale}")
        if trace.review and trace.review.must_fix:
            lines_today.append("Must-fix: " + " | ".join(trace.review.must_fix[:3]))
        if trace.candidate_pool_summary:
            lines_today.append("Candidate pool:")
            for line in trace.candidate_pool_summary[:5]:
                lines_today.append(f"  - {line}")
        if trace.outcome_tracking_summary:
            lines_today.append(f"Outcome: {trace.outcome_tracking_summary}")
        if trace.historical_validation_summary:
            lines_today.append(f"History: {trace.historical_validation_summary}")
        
    if changes:
        lines_today.append("")
        lines_today.append("🔧 ADJUSTMENTS (Post-processing):")
        for c in changes:
            lines_today.append(f"  • {c}")
    note_today = "\n".join(lines_today)

    # --- Bygg innehåll för IGÅR ---
    note_yesterday = None
    if plan.yesterday_feedback:
            note_yesterday = f"📝 COACH FEEDBACK:\n{plan.yesterday_feedback}"
    
    try:
        # Rensa tidigare skapade loggar från idag OCH igår (för att undvika dubbletter)
        existing = icu_get(f"/athlete/{ATHLETE_ID}/events", {
            "oldest": yesterday_str,
            "newest": (today_date + timedelta(days=1)).isoformat(),
        })
        
        for e in existing:
            if e.get("category") == "NOTE":
                date_local = e.get("start_date_local", "")[:10]
                
                if e.get("name") == "🤖 AI Coach Log" and date_local == today_str:
                    requests.put(
                        f"{BASE}/athlete/{ATHLETE_ID}/events/bulk-delete",
                        auth=AUTH, timeout=15, json=[{"id": e["id"]}],
                    ).raise_for_status()
                
                if e.get("name") == "📝 Coach Feedback" and date_local == yesterday_str:
                    requests.put(
                        f"{BASE}/athlete/{ATHLETE_ID}/events/bulk-delete",
                        auth=AUTH, timeout=15, json=[{"id": e["id"]}],
                    ).raise_for_status()

        # 1. Spara Dagens Logg (På dagens datum kl 05:00)
        requests.post(f"{BASE}/athlete/{ATHLETE_ID}/events", auth=AUTH, timeout=10, json={
            "category": "NOTE",
            "start_date_local": today_str + "T05:00:00",
            "name": "🤖 AI Coach Log",
            "description": note_today + f"\n\n{AI_TAG}",
            "color": "#8E44AD"  # Lila färg
        }).raise_for_status()

        # 2. Spara Gårdagens Feedback (På gårdagens datum kl 18:00)
        if note_yesterday:
            requests.post(f"{BASE}/athlete/{ATHLETE_ID}/events", auth=AUTH, timeout=10, json={
                "category": "NOTE",
                "start_date_local": yesterday_str + "T18:00:00",
                "name": "📝 Coach Feedback",
                "description": note_yesterday + f"\n\n{AI_TAG}",
                "color": "#8E44AD"  # Lila färg
            }).raise_for_status()
            
        log.info("📝 Daily coach log and feedback saved separately in intervals.icu")
    except Exception as e:
        log.warning(f"Could not save daily coach log: {e}")

def generate_weekly_report(activities: list, wellness: list, fitness: list,
                           mesocycle: dict, trajectory: dict,
                           compliance: dict, ftp_check: dict,
                           acwr_trend: dict, taper_score: dict,
                           ai_feedback: str = "",
                           motivation: dict = None,
                           development_needs: dict = None,
                           block_objective: dict = None,
                           race_demands: dict = None,
                           session_quality: dict = None,
                           coach_confidence: dict = None,
                           polarization: dict = None,
                           planner_insights: dict = None) -> str:
    planner_insights = planner_insights or {}
    today = date.today()
    week_start = today - timedelta(days=today.weekday() + 7)
    week_end   = week_start + timedelta(days=7)
    week_end_incl = week_start + timedelta(days=6)
    week_acts = [
        a for a in activities
        if safe_date_str(a) and week_start.isoformat() <= safe_date_str(a) < week_end.isoformat()
    ]
    total_min   = sum((a.get("moving_time", 0) or 0) / 60 for a in week_acts)
    total_tss   = sum((a.get("icu_training_load", 0) or 0) for a in week_acts)
    total_dist  = sum((a.get("distance", 0) or 0) / 1000 for a in week_acts)
    zone_mins = [0.0] * 7
    for a in week_acts:
        hr_zones = a.get("icu_hr_zone_times") or a.get("icu_zone_times") or []
        for i, z in enumerate(hr_zones):
            if isinstance(z, dict):
                secs = z.get("secs", 0) or z.get("seconds", 0)
            elif isinstance(z, (int, float)):
                secs = z
            else:
                continue
            if i < 7:
                zone_mins[i] += secs / 60
    total_zone_min = sum(zone_mins) or 1
    zone_pct = [round(z / total_zone_min * 100) for z in zone_mins]
    low_pct  = zone_pct[0] + zone_pct[1] if len(zone_pct) > 1 else 0
    mid_pct  = zone_pct[2] if len(zone_pct) > 2 else 0
    high_pct = sum(zone_pct[3:]) if len(zone_pct) > 3 else 0
    if low_pct >= 75 and mid_pct <= 15:
        polar_verdict = "✅ Good polarized distribution"
    elif mid_pct > 20:
        polar_verdict = "⚠️ Too much Z3 (gray zone) – more pure Z2 or pure Z4+"
    else:
        polar_verdict = "Neutral distribution"
    ctl_values = [f.get("ctl", 0) for f in fitness[-14:] if f.get("ctl") is not None]
    ctl_delta = round(ctl_values[-1] - ctl_values[-8], 1) if len(ctl_values) >= 8 else 0
    sport_min = {}
    for a in week_acts:
        t = a.get("type", "Other")
        sport_min[t] = sport_min.get(t, 0) + (a.get("moving_time", 0) or 0) / 60
    sport_lines = " | ".join(f"{k}: {round(v)}min" for k, v in sorted(sport_min.items(), key=lambda x: -x[1]))
    week_wellness = [
        w for w in wellness
        if w.get("id", "")[:10] >= week_start.isoformat()
        and w.get("id", "")[:10] < week_end.isoformat()
    ]
    sleep_vals = [w.get("sleepSecs", 0) / 3600 for w in week_wellness if w.get("sleepSecs")]
    avg_sleep = round(sum(sleep_vals) / len(sleep_vals), 1) if sleep_vals else "N/A"
    hrv_vals = [w.get("hrv") for w in week_wellness if w.get("hrv")]
    avg_hrv = round(sum(hrv_vals) / len(hrv_vals)) if hrv_vals else "N/A"
    report = f"""━━━ WEEKLY REPORT {week_start.isoformat()} → {week_end_incl.isoformat()} ━━━

📊 SUMMARY
  Time:     {round(total_min)}min ({round(total_min/60, 1)}h)
  TSS:      {round(total_tss)}
  Distance: {round(total_dist, 1)}km
  Sessions: {len(week_acts)}st
  CTL:      {round(ctl_values[-1]) if ctl_values else 'N/A'} (Δ{ctl_delta:+.1f} past week)"""

    if ai_feedback:
        report += f"\n\n🤖 COACH FEEDBACK\n  {ai_feedback}"

    report += f"""

🏋️ SPORT DISTRIBUTION
  {sport_lines or 'No data'}

📈 ZONE DISTRIBUTION
  Z1-Z2 (low): {low_pct}% | Z3 (mid): {mid_pct}% | Z4+ (high): {high_pct}%
  {polar_verdict}

💤 RECOVERY
  Sleep avg: {avg_sleep}h | HRV avg: {avg_hrv}ms

🔄 MESOCYCLE
  Block {mesocycle['block_number']}, Week {mesocycle['week_in_block']}/4
  {'🟡 DELOAD WEEK' if mesocycle['is_deload'] else f'Loading week ({mesocycle["load_factor"]:.0%})'}
  {mesocycle['deload_reason'] if mesocycle['deload_reason'] else ''}

🎯 CTL TRAJECTORY
  {trajectory['message'] if trajectory.get('has_target') else 'No A-race scheduled.'}
"""
    if trajectory.get("milestones"):
        report += "  Milestones:\n"
        for m in trajectory["milestones"]:
            report += f"    +{m['weeks']}v: CTL {m['projected_ctl']}\n"

    if acwr_trend and acwr_trend.get("summary"):
        report += f"""
📈 ACWR TREND
  {acwr_trend['summary']}
"""

    if taper_score and taper_score.get("is_in_taper"):
        score = int(taper_score.get('score', 0))
        length = 20
        filled_length = int(length * score / 100)
        bar = '█' * filled_length + '░' * (length - filled_length)
        report += f"""
📉 TAPER QUALITY (Day {taper_score['taper_day']}/{taper_score['taper_days']})
  Score: {score}/100 {bar}
  {taper_score.get('verdict', '')}
  Details: CTL {taper_score.get('ctl_drop_pct'):+.1f}%, ATL {taper_score.get('atl_drop_pct'):+.1f}%, TSB Δ{taper_score.get('tsb_rise'):+.1f}
  Adjustments: {' '.join(taper_score.get('adjustments', [])) or 'None, everything looks good.'}
"""

    report += f"""
📋 COMPLIANCE
  {compliance['summary']}
"""
    if motivation:
        report += f"""
🧠 MOTIVATION & PSYKOLOGI
  {motivation['summary']}
"""
        if motivation["state"] in ("BURNOUT_RISK", "FATIGUED"):
            report += f"  ⚠️ Prioritize recovery and variation next week.\n"

    if block_objective:
        report += f"""
🎯 BLOCK OBJECTIVE
  Primary focus: {block_objective.get('primary_focus', '?')}
  Secondary focus: {block_objective.get('secondary_focus') or 'No secondary focus'}
  Objective: {block_objective.get('objective', '')}
  Must-hit: {' | '.join(block_objective.get('must_hit_sessions', [])) or 'None defined'}
"""

    if development_needs:
        prio_lines = []
        for p in development_needs.get("priorities", [])[:3]:
            prio_lines.append(f"  - {p['area']} ({p['score']}): {p['why']}")
        report += "\n📌 DEVELOPMENT NEEDS\n" + ("\n".join(prio_lines) if prio_lines else "  No clear development needs identified.")

    if race_demands:
        report += f"""

🏁 RACE DEMANDS
  {race_demands.get('summary', '')}
  {' | '.join(race_demands.get('markers', [])[:4]) if race_demands.get('markers') else 'No markers'}
"""

    if session_quality:
        report += f"""
🛠️ SESSION QUALITY
  {session_quality.get('summary', '')}
"""
        if session_quality.get("recent_sessions"):
            report += "\n" + "\n".join(session_quality["recent_sessions"][:4]) + "\n"

    if polarization:
        report += f"""
⚖️ POLARISATION
  {polarization.get('summary', '')}
"""

    if coach_confidence:
        report += f"""
🧭 COACH CONFIDENCE
  {coach_confidence.get('summary', '')}
"""

    report += f"""
🔬 FTP-STATUS
  {ftp_check['recommendation']}
  {ftp_check.get('suggested_protocol', '')}
"""
    if planner_insights:
        capacity_map = planner_insights.get("capacity_map", {})
        performance_forecast = planner_insights.get("performance_forecast", {})
        race_readiness = planner_insights.get("race_readiness", {})
        benchmark_system = planner_insights.get("benchmark_system", {})
        minimum_effective_dose = planner_insights.get("minimum_effective_dose", {})
        execution_friction = planner_insights.get("execution_friction", {})
        block_learning = planner_insights.get("block_learning", {})
        season_plan = planner_insights.get("season_plan", {})
        nutrition_readiness = planner_insights.get("nutrition_readiness", {})
        individualization_profile = planner_insights.get("individualization_profile", {})
        report += "\n\nPLANNER INSIGHTS\n"
        if race_readiness:
            report += f"  {race_readiness.get('summary', '')}\n"
        if performance_forecast:
            report += f"  {performance_forecast.get('summary', '')}\n"
        if minimum_effective_dose:
            report += f"  {minimum_effective_dose.get('summary', '')}\n"
            if minimum_effective_dose.get("rationale"):
                report += f"  MED reasons: {' | '.join(minimum_effective_dose.get('rationale', [])[:4])}\n"
        if execution_friction:
            report += f"  {execution_friction.get('summary', '')}\n"
        if capacity_map:
            report += (
                "  Capacity strongest: "
                + (", ".join(capacity_map.get("strongest", [])) or "unknown")
                + " | weakest: "
                + (", ".join(capacity_map.get("weakest", [])) or "unknown")
                + "\n"
            )
        if nutrition_readiness:
            report += f"  Nutrition: {nutrition_readiness.get('summary', '')}\n"
        if individualization_profile:
            report += f"  Individualization: {individualization_profile.get('summary', '')}\n"
        if block_learning:
            report += f"  Block learning: {block_learning.get('summary', '')}\n"
        if benchmark_system.get("benchmarks"):
            report += "  Benchmarks:\n"
            for item in benchmark_system["benchmarks"][:3]:
                report += (
                    f"    - {item.get('name')} ({item.get('priority')} in ~{item.get('due_in_days')}d): "
                    f"{item.get('session')}\n"
                )
        if season_plan.get("blocks"):
            report += "  Season map:\n"
            for block in season_plan["blocks"][:4]:
                report += (
                    f"    - {block.get('label')} ({block.get('start')} -> {block.get('end')}): "
                    f"{block.get('focus')}\n"
                )
    return report.strip()


def save_weekly_report_to_icu(report: str):
    today = date.today()
    last_monday = today - timedelta(days=today.weekday() + 7)
    last_sunday = last_monday + timedelta(days=6)
    week_num = last_monday.isocalendar()[1]
    try:
        existing = icu_get(f"/athlete/{ATHLETE_ID}/events", {
            "oldest": last_monday.isoformat(),
            "newest": (today + timedelta(days=1)).isoformat(),
        })
        for e in existing:
            if REPORT_TAG in (e.get("description") or ""):
                log.info("📊 Weekly report already exists for this week, skipping.")
                return
        requests.post(f"{BASE}/athlete/{ATHLETE_ID}/events", auth=AUTH, timeout=10, json={
            "category": "NOTE",
            "start_date_local": last_sunday.isoformat() + "T23:50:00",
            "name": f"📊 Weekly report w{week_num}",
            "description": report + f"\n\n{REPORT_TAG}",
            "color": "#4A90D9",
        }).raise_for_status()
        log.info(f"📊 Weekly report saved in intervals.icu ({last_sunday.isoformat()})")
    except Exception as e:
        log.warning(f"Could not save weekly report: {e}")



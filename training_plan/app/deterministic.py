"""Glue between the analysis in app/main.py and the deterministic planner.

`build_planner_inputs` turns main's analysis results into `PlannerInputs`;
`build_ai_context` collects the facts the AI enrichment step may use;
`finalize_deterministic_plan` runs enrichment, safety rules and validation.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from functools import partial

from training_plan.core.models import AIPlan, PlanDecisionTrace
from training_plan.engine.calendar_context import (
    atp_weeks,
    availability_by_date,
    injuries_by_date,
    tss_per_hour,
)
from training_plan.engine.intensity import (
    HARD_CATEGORIES,
    IntensitySignals,
    assign_emphasis,
    hard_sessions_by_week,
    hard_sessions_for_week,
)
from training_plan.engine.periodization import (
    apply_calendar_targets,
    build_week_targets,
    done_tss_this_week,
    format_week_targets,
    monday_of,
)
from training_plan.engine.pipeline.enrich import COACH_LANGUAGE, enrich_and_validate, request_enrichment
from training_plan.engine.planner import PlannerInputs, PlannerResult
from training_plan.engine.planning import classify_session_category
from training_plan.engine.postprocess import apply_safety_rules
from training_plan.engine.postprocess.injury import injury_restrictions
from training_plan.engine.utils import race_priority, time_available_minutes
from training_plan.engine.validation import validate_postprocessed_plan

def _day(item: dict) -> str:
    return (item.get("start_date_local") or "")[:10]


def _recently_sick(events: list, today: date, days: int = 7) -> bool:
    """A SICK event that ended within the last `days` days (or is still going on)."""
    cutoff = (today - timedelta(days=days)).isoformat()
    for e in events:
        if (e.get("category") or "").upper() != "SICK":
            continue
        start = _day(e)
        end = (e.get("end_date_local") or e.get("start_date_local") or "")[:10]
        if start and start <= today.isoformat() and end >= cutoff:
            return True
    return False


def _adapt_week_targets(targets: list, *, today: date, signals: IntensitySignals, unavailable: set,
                        tph: float, race_phase: str | None, has_race: bool) -> list:
    """Hard sessions per week from the athlete's response, and the session format per week."""
    adapted = []
    for target in targets:
        week = [(date.fromisoformat(target.week_start) + timedelta(days=i)).isoformat() for i in range(7)]
        trainable = sum(1 for d in week if d not in unavailable)
        count, reason = hard_sessions_for_week(target, signals, trainable_days=trainable,
                                               hours=target.tss_target / tph if tph else None)
        note = "; ".join(x for x in (target.note, reason) if x)
        emphasis = assign_emphasis(target.week_start, atp_phase=target.phase or None,
                                   race_phase=race_phase, has_race=has_race)
        adapted.append(replace(target, max_key_sessions=count, note=note, emphasis=emphasis))
    return adapted


def _latest_sleep_hours(wellness: list, today: date) -> float | None:
    for w in reversed(wellness or []):
        if (w.get("id") or "")[:10] in (today.isoformat(), (today - timedelta(days=1)).isoformat()):
            if w.get("sleepSecs"):
                return w["sleepSecs"] / 3600
    return None


def build_planner_inputs(
    *,
    today: date,
    horizon: int,
    ctl: float,
    tsb: float,
    mesocycle: dict,
    phase: dict,
    trajectory: dict,
    races: list,
    race_week: dict,
    rtp_status: dict,
    state: dict,
    dominant_sport: str,
    weather: list,
    constraints: list,
    locked_dates: set,
    base_tss_by_date: dict,
    budgets: dict,
    sport_acwr: dict,
    hrv: dict,
    readiness: dict,
    wellness: list,
    activities: list,
    morning: dict,
    injury_profile: dict | None,
    development_needs: dict,
    ftp_check: dict,
    motivation: dict,
    calendar_events: list | None = None,
    compliance: dict | None = None,
) -> PlannerInputs:
    horizon_dates = [(today + timedelta(days=i)).isoformat() for i in range(horizon + 1)]
    calendar_events = calendar_events or []
    today_s, tomorrow_s = today.isoformat(), (today + timedelta(days=1)).isoformat()

    # Daily readiness only affects today and tomorrow; the week targets stay stable.
    reasons = []
    if (hrv or {}).get("state") == "LOW":
        reasons.append(f"HRV low ({hrv.get('deviation_pct', '?')}% vs baseline)")
    sleep_h = _latest_sleep_hours(wellness, today)
    if sleep_h is not None and sleep_h < 5.5:
        reasons.append(f"short sleep ({sleep_h:.1f} h)")
    if (readiness or {}).get("score", 100) < 45:
        reasons.append(f"readiness {readiness['score']}/100")
    restricted = {today_s, tomorrow_s} if reasons else set()
    time_today = time_available_minutes(morning.get("time_available", "") or "")
    if time_today is not None and time_today < 60:
        restricted.add(today_s)
    if (rtp_status or {}).get("is_active"):
        restricted |= set(horizon_dates[:7])
        reasons.append(f"return to play after {rtp_status.get('days_off')} rest days")

    # SICK / INJURED / HOLIDAY events in the calendar (the planner also plans the rest of
    # the last week, so look a week past the horizon).
    lookahead = [(today + timedelta(days=i)).isoformat() for i in range(horizon + 8)]
    availability = availability_by_date(calendar_events, lookahead)
    unavailable = {d for d, (level, _) in availability.items() if level == "UNAVAILABLE"}
    limited = {d for d, (level, _) in availability.items() if level == "LIMITED"}
    restricted |= limited
    for level, reason in dict.fromkeys(v for d, v in sorted(availability.items()) if d in horizon_dates):
        reasons.append(f"{reason} ({'no training' if level == 'UNAVAILABLE' else 'limited'})")

    # INJURED events: block only the sports the injury affects ("knee" → no running), on its dates.
    constraints = list(constraints or [])
    for d, text in sorted(injuries_by_date(calendar_events, lookahead).items()):
        blocked = injury_restrictions(text, None)
        if blocked and blocked["avoid_sports"]:
            sports = sorted(blocked["avoid_sports"])
            constraints.append({"date": d, "blocked_types": sports, "reason": f"injured: {text}"})
            if d in horizon_dates:
                reason = f"injured: {text} (no {', '.join(sports)})"
                if reason not in reasons:
                    reasons.append(reason)

    injury_note = morning.get("injury_today") or ""
    injury = injury_restrictions(injury_note, injury_profile) if injury_note else None
    avoid = set(injury["avoid_sports"]) if injury else set()
    if injury and injury.get("severity") in ("MODERATE", "SEVERE"):
        restricted |= set(horizon_dates)
        reasons.append(f"injury ({injury.get('severity', '').lower()})")
    avoid |= {sport for sport, d in (sport_acwr or {}).items() if d.get("zone") == "DANGER"}

    monday = monday_of(today).isoformat()
    this_week = [a for a in activities if monday <= _day(a) <= today_s]
    kinds_done = [classify_session_category(a) for a in this_week]
    intensity_done = sum(1 for k in kinds_done if k in HARD_CATEGORIES)
    yesterday = (today - timedelta(days=1)).isoformat()
    yesterday_hard = any(_day(a) == yesterday and classify_session_category(a) in HARD_CATEGORIES for a in activities)

    targets = build_week_targets(
        ctl, mesocycle, today,
        races=races, trajectory=trajectory, tsb=tsb,
        done_tss=done_tss_this_week(activities, today),
    )
    # The athlete's annual training plan in intervals.icu, when there is one, sets the weekly load.
    tph = tss_per_hour(activities)
    atp = atp_weeks(calendar_events)
    if atp:
        targets = apply_calendar_targets(targets, atp, tph)
    # How many hard sessions each week gets follows the athlete's own recovery and history.
    compliance = compliance or {}
    signals = IntensitySignals(
        hrv_state=(hrv or {}).get("state"), ctl=ctl, tsb=tsb,
        burnout=(motivation or {}).get("state") == "BURNOUT_RISK",
        rtp_active=bool((rtp_status or {}).get("is_active")),
        recently_sick=_recently_sick(calendar_events, today),
        key_planned=int(compliance.get("intensity_planned") or 0),
        key_missed=int(compliance.get("intensity_missed") or 0),
        hard_by_week=hard_sessions_by_week(activities, today, classify_session_category),
    )
    targets = _adapt_week_targets(
        targets, today=today, signals=signals, unavailable=unavailable, tph=tph,
        race_phase=(phase or {}).get("phase"), has_race=any(_day(r) >= today_s for r in races or []),
    )
    return PlannerInputs(
        today=today,
        horizon_dates=horizon_dates,
        week_targets=targets,
        phase=(phase or {}).get("phase", "Base"),
        mesocycle=mesocycle,
        workout_levels=dict(state.get("workout_levels", {})),
        primary_sport=dominant_sport,
        weather=weather,
        constraints=constraints,
        locked_dates=set(locked_dates),
        unavailable_dates=unavailable,
        base_tss_by_date=dict(base_tss_by_date),
        sport_budgets=budgets,
        avoid_sports=avoid,
        restricted_dates=restricted,
        restriction_reason=", ".join(reasons),
        time_available_today=time_today,
        race_week=race_week,
        rtp_status=rtp_status,
        focus_areas=[p.get("area") for p in (development_needs or {}).get("priorities", [])],
        ftp_test_due=bool((ftp_check or {}).get("needs_test")) and (phase or {}).get("phase") not in ("Taper", "Race Week"),
        burnout=(motivation or {}).get("state") == "BURNOUT_RISK",
        done_today=any(_day(a) == today_s for a in activities),
        intensity_done_this_week=intensity_done,
        key_kinds_done_this_week=[k for k in kinds_done if k in HARD_CATEGORIES],
        yesterday_was_hard=yesterday_hard,
        injury=injury,
        injury_note=injury_note,
    )


def build_ai_context(
    *,
    inputs: PlannerInputs,
    result: PlannerResult,
    ctl: float,
    atl: float,
    tsb: float,
    phase: dict,
    readiness: dict,
    hrv: dict,
    motivation: dict,
    development_needs: dict,
    races: list,
    weather: list,
    manual_workouts: list,
    morning: dict,
    yesterday_analysis: str,
) -> dict:
    today = inputs.today
    next_race = next(
        (r for r in sorted(races or [], key=_day) if _day(r) >= today.isoformat()), None)
    facts = [
        f"Today: {today.isoformat()} ({today.strftime('%A')})",
        f"Phase: {(phase or {}).get('phase', '?')} – {(phase or {}).get('rule', '')}",
        f"Fitness: CTL {ctl:.0f} | ATL {atl:.0f} | TSB {tsb:+.0f}",
        (readiness or {}).get("summary", ""),
        f"HRV state: {(hrv or {}).get('state', '?')}",
        (motivation or {}).get("summary", ""),
        (development_needs or {}).get("summary", ""),
        f"Time available today: {morning.get('time_available') or 'no limit given'}",
        f"Pain/injury: {morning.get('injury_today') or 'none'}",
    ]
    if inputs.restriction_reason:
        facts.append(f"Sessions adjusted (easier, shorter or none) because of: {inputs.restriction_reason}")
    if next_race:
        facts.append(f"Next race: {next_race.get('name', 'Race')} on {_day(next_race)} "
                     f"(priority {race_priority(next_race)})")
    horizon = set(inputs.horizon_dates)
    weather_lines = [
        f"{w['date']}: AM {w.get('desc_morning', '?')} {w.get('temp_morning', '?')}°C {w.get('rain_morning_mm', 0)}mm | "
        f"PM {w.get('desc', '?')} {w.get('temp_afternoon', '?')}°C {w.get('rain_afternoon_mm', 0)}mm"
        for w in weather or [] if w.get("date") in horizon
    ]
    manual_lines = [
        f"{_day(w)}: {w.get('name', '?')} ({w.get('type') or '?'}, {round((w.get('moving_time') or 0) / 60)} min)"
        for w in manual_workouts if _day(w) in horizon
    ]
    weeks = [t for t in result.week_targets if any(t.contains(d) for d in inputs.horizon_dates)]
    return {
        "language": COACH_LANGUAGE,
        "athlete_note": morning.get("athlete_note", ""),
        "facts": [f for f in facts if f],
        "week_targets": format_week_targets(weeks),
        "weather": weather_lines,
        "manual_sessions": manual_lines,
        "yesterday_analysis": yesterday_analysis or "",
        "yesterday_date": (today - timedelta(days=1)).isoformat(),
        "weekly_feedback_requested": today.weekday() == 0,
    }


def enrich_keys_for(result: PlannerResult, mode: str, ai_workouts: list) -> set[str]:
    """Sessions that will actually be saved, so the AI only writes for those."""
    if mode == "full":
        return set(result.options)
    existing = {_day(w) for w in ai_workouts}
    return {key for key in result.options if key.split("|")[0] not in existing}


def finalize_deterministic_plan(
    result: PlannerResult,
    *,
    mode: str,
    ai_workouts: list,
    provider: str,
    use_ai: bool,
    ai_context: dict,
    safety_kwargs: dict,
    athlete: dict,
    base_tss_by_date: dict,
    validation_context: dict,
    validation_budget: float,
) -> tuple[AIPlan, list[str], PlanDecisionTrace]:
    keys = enrich_keys_for(result, mode, ai_workouts)
    enrichment = request_enrichment(provider, result, keys, ai_context) if use_ai else None
    safety = partial(apply_safety_rules, **safety_kwargs)

    def validate(plan, changes):
        return validate_postprocessed_plan(
            plan, athlete=athlete, base_tss_by_date=base_tss_by_date, tss_budget=validation_budget,
            review_context=validation_context, postprocess_changes=changes,
        )

    return enrich_and_validate(result, enrichment=enrichment, enrich_keys=keys, safety=safety, validate=validate)

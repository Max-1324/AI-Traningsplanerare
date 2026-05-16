from training_plan.core.common import *
from training_plan.engine.libraries import *
from training_plan.engine.planning import *
from training_plan.engine.utils import safe_date_str, safe_date

def development_needs_analysis(phase: dict, readiness: dict, motivation: dict,
                               compliance: dict, ftp_check: dict,
                               np_if_analysis: dict, session_quality: dict,
                               race_demands: dict, polarization: dict) -> dict:
    priorities = []

    def add(area: str, score: int, why: str, sessions: list[str]):
        priorities.append({
            "area": area,
            "score": score,
            "why": why,
            "sessions": sessions,
        })

    readiness_score = (readiness or {}).get("score", 60)
    motivation_state = (motivation or {}).get("state", "NEUTRAL")
    weighted_compliance = (compliance or {}).get("weighted_completion_rate", 100)
    key_completion = (compliance or {}).get("key_completion_rate", 100)
    phase_name = (phase or {}).get("phase", "Base")
    session_scores = (session_quality or {}).get("category_scores", {})

    if readiness_score < 45 or motivation_state == "BURNOUT_RISK":
        add(
            "recovery",
            100,
            f"Readiness {readiness_score}/100 and motivation {motivation_state} require more recovery to absorb the training.",
            ["1-2 extra easy days", "shorter main sessions", "keep only most valuable key sessions"],
        )
    elif motivation_state == "FATIGUED":
        add(
            "recovery",
            82,
            "Psychological/mental fatigue visible in the feel trend - slightly lower friction gives better long-term development.",
            ["fun quality in shorter format", "high feasibility", "avoid unnecessary filler"],
        )

    if weighted_compliance < 75 or key_completion < 70:
        add(
            "consistency",
            92,
            f"Weighted compliance {weighted_compliance}% and key sessions {key_completion}% is too low for maximum development.",
            ["2-3 must-hit sessions", "shorter flex sessions", "less plan friction on weekdays"],
        )

    if ftp_check and ftp_check.get("needs_test") and phase_name not in ("Race Week",):
        add(
            "calibration",
            86,
            ftp_check["recommendation"],
            ["schedule FTP test", "keep 1-2 days easier before test", "adjust future zones after the outcome"],
        )

    if race_demands and race_demands.get("gaps"):
        if any("durability gap" in g.lower() for g in race_demands["gaps"]):
            add(
                "durability",
                84,
                "Race demands show that long durability is still a clear bottleneck.",
                ["1 long Z2 session", "progressive long ride", "train nutrition during long sessions"],
            )
        if any("fueling gap" in g.lower() for g in race_demands["gaps"]):
            add(
                "fueling",
                74,
                "Long nutrition repetitions are missing for the race target.",
                ["CHO plan on long sessions", "practice 80-100g CHO/h", "log stomach tolerance"],
            )

    threshold_count = session_scores.get("threshold", {}).get("count", 0)
    threshold_score = session_scores.get("threshold", {}).get("avg_score", 0)
    if phase_name in ("Base", "Build") and (threshold_count < 2 or threshold_score < 68):
        add(
            "threshold",
            76 if phase_name == "Build" else 68,
            f"Threshold stimuli are {'few' if threshold_count < 2 else 'too weak'} for the current phase.",
            ["1 threshold session", "keep RPE 6-7", "even quality through all intervals"],
        )

    vo2_count = session_scores.get("vo2", {}).get("count", 0)
    vo2_score = session_scores.get("vo2", {}).get("avg_score", 0)
    if phase_name == "Build" and readiness_score >= 60 and (vo2_count < 1 or vo2_score < 65):
        add(
            "vo2",
            70,
            "Build phase without clear oxygen stimuli loses top speed and headroom.",
            ["1 short VO2 session", "full recovery before/after", "avoid double hard days"],
        )

    np_flags = (np_if_analysis or {}).get("flags", [])
    if np_flags:
        if any("IF CONSISTENTLY HIGH" in f or "IF KONSEKVENT HÖG" in f or "FRONT-LOADING" in f for f in np_flags):
            add(
                "pacing",
                72,
                "The pacing/IF pattern indicates that the sessions are harder than intended or losing consistency.",
                ["one strict Z2 session", "one pacing-focused long session", "clearer nutrition and watt discipline"],
            )

    if polarization and polarization.get("mid_pct", 0) > 20:
        add(
            "polarization",
            66,
            "Too much Z3 reduces the quality in both aerobic base and hard key sessions.",
            ["cleaner Z2 days", "cleaner Z4+/VO2 days", "less gray zone"],
        )

    if not priorities:
        add(
            "durability",
            60,
            "No acute weaknesses stand out - continue building robust aerobic durability.",
            ["1 long Z2 session", "1 quality session", "other supporting volume"],
        )

    deduped = {}
    for item in sorted(priorities, key=lambda x: (-x["score"], x["area"])):
        deduped.setdefault(item["area"], item)
    top = list(deduped.values())[:3]

    must_hit = []
    for item in top[:2]:
        for sess in item["sessions"]:
            if sess not in must_hit:
                must_hit.append(sess)

    primary = top[0]["area"]
    secondary = top[1]["area"] if len(top) > 1 else None
    summary = " | ".join(f"{p['area']} ({p['score']})" for p in top)
    return {
        "priorities": top,
        "primary_focus": primary,
        "secondary_focus": secondary,
        "must_hit_sessions": must_hit[:4],
        "flex_sessions": [
            "other sessions can be easier if it increases feasibility",
            "rather remove filler than compromise must-hit sessions",
        ],
        "summary": f"Development needs: {summary}",
    }


def update_block_objective(state: dict, mesocycle: dict, phase: dict,
                           development_needs: dict, race_demands: dict) -> dict:
    today = date.today().isoformat()
    primary = development_needs.get("primary_focus", "durability")
    secondary = development_needs.get("secondary_focus")
    target_name = race_demands.get("target_name", "main_target")
    signature = "|".join([
        phase.get("phase", "Base"),
        str(mesocycle.get("block_number", 1)),
        str(mesocycle.get("week_in_block", 1)),
        primary,
        target_name,
    ])

    existing = state.get("block_objective", {})
    if existing.get("signature") == signature:
        return existing

    focus_text = {
        "recovery": "acute recovery (first few days) to absorb fatigue, followed by normal training/key sessions later in the block",
        "consistency": "increase consistency so that important sessions actually get done",
        "calibration": "calibrate FTP/zones so the rest of the block gets the right dose",
        "durability": "build durability for many hours in the saddle without losing quality",
        "fueling": "train race-relevant nutrition and stomach tolerance",
        "threshold": "raise sustainable power and efficiency around threshold",
        "vo2": "increase aerobic top capacity and headroom",
        "pacing": "get more even load and better control of intensity",
        "polarization": "polarize intensity distribution for better adaptation",
    }

    objective = {
        "signature": signature,
        "created": today,
        "phase": phase.get("phase", "Base"),
        "primary_focus": primary,
        "secondary_focus": secondary,
        "target_name": target_name,
        "objective": focus_text.get(primary, primary),
        "must_hit_sessions": development_needs.get("must_hit_sessions", []),
        "flex_sessions": development_needs.get("flex_sessions", []),
        "success_markers": race_demands.get("markers", [])[:4],
        "review_after": (date.today() + timedelta(days=7)).isoformat(),
    }
    state["block_objective"] = objective
    return objective

def training_phase(races, today):
    future = sorted([r for r in races if datetime.strptime(
        r.get("start_date_local", r.get("date","2099-01-01"))[:10], "%Y-%m-%d").date() >= today],
        key=lambda r: r.get("start_date_local",""))
    if not future: return {"phase": "Base", "rule": "Base training: 1-2 interval sessions/week (Z4-Z5), 1 tempo session (Z3), rest Z2. Avoid intervals ONLY if HRV=LOW or TSB < -20."}
    nr = future[0]
    dt = (datetime.strptime(nr["start_date_local"][:10], "%Y-%m-%d").date() - today).days
    nm = nr.get("name","Race")
    if dt < 7:  return {"phase": "Race Week", "rule": f"{nm} in {dt}d. Activation."}
    if dt < 28: return {"phase": "Taper",     "rule": f"{nm} in {dt}d. -30% volume, maintain intensity."}
    if dt < 84: return {"phase": "Build",     "rule": f"{nm} in {dt}d. Build intensity."}
    return {"phase": "Base", "rule": f"{nm} in {dt}d. Base training: 1-2 interval sessions/week (Z4-Z5), 1 tempo session (Z3), rest Z2."}


# ══════════════════════════════════════════════════════════════════════════════
# RACE WEEK PROTOCOL
# ══════════════════════════════════════════════════════════════════════════════

def race_week_protocol(races: list, today: date, dominant_sport: str = "") -> dict:
    """
    Generates day-specific race-week protocol (last 7 days before race).

    Protocol:
      -6d: Last medium session (90min Z2 + 2x5min Z4)
      -5d: Short Z2 (45min) + leg strength (easy, 15min)
      -4d: Rest or 30min Z1
      -3d: Activation: 60min Z2 with 3x3min Z4 (short, sharp)
      -2d: Short Z1 (30min) - spin legs
      -1d: Rest OR 20min Z1 with 3x30s race pace
      Race day: RACE

    Returns: dict with protocol, days, race_name, is_active
    """
    future = sorted([
        r for r in races
        if datetime.strptime(r.get("start_date_local", "2099-01-01")[:10], "%Y-%m-%d").date() > today
    ], key=lambda r: r.get("start_date_local", ""))

    if not future:
        return {"is_active": False, "protocol": [], "race_name": None}

    race = future[0]
    race_date = datetime.strptime(race["start_date_local"][:10], "%Y-%m-%d").date()
    days_to_race = (race_date - today).days
    race_name = race.get("name", "Race")

    if days_to_race > 7 or days_to_race <= 0:
        return {"is_active": False, "protocol": [], "race_name": race_name, "days_to_race": days_to_race}

    # Pick the sport to use for pre-race sessions.
    # Prefer an indoor/low-impact variant of the dominant sport for controlled taper sessions.
    _indoor_map = {
        "Ride":      "VirtualRide",
        "Run":       "Run",
        "RollerSki": "VirtualRide",
        "NordicSki": "VirtualRide",
        "Swim":      "Swim",
    }
    _sport_types = {s["intervals_type"] for s in SPORTS}
    _ds = dominant_sport or (SPORTS[0]["intervals_type"] if SPORTS else "VirtualRide")
    _prerace_sport = _indoor_map.get(_ds, _ds)
    # Fall back to VirtualRide if chosen sport isn't available
    if _prerace_sport not in _sport_types:
        _prerace_sport = next(
            (s["intervals_type"] for s in SPORTS if s.get("injury_risk") == "low"),
            "VirtualRide",
        )

    # Build day-specific protocol
    protocol = []

    day_templates = {
        6: {
            "title": f"🏁 Pre-race: Last medium session ({race_name} in 6d)",
            "type": _prerace_sport, "dur": 90, "slot": "MAIN",
            "steps": [
                {"d": 20, "z": "Z2", "desc": "Warm-up"},
                {"d": 30, "z": "Z2", "desc": "Endurance - focus on feel"},
                {"d": 5, "z": "Z4", "desc": "Last Z4 effort - race pace"},
                {"d": 5, "z": "Z1", "desc": "Rest"},
                {"d": 5, "z": "Z4", "desc": "Second Z4 effort - controlled"},
                {"d": 15, "z": "Z2", "desc": "Easy"},
                {"d": 10, "z": "Z1", "desc": "Cool-down"},
            ],
            "desc": "Last session with substance. No records - just confirm form. Race nutrition strategy: test CHO intake."
        },
        5: {
            "title": f"🏁 Pre-race: Easy {_prerace_sport} + quick strength ({race_name} in 5d)",
            "type": _prerace_sport, "dur": 45, "slot": "MAIN",
            "steps": [
                {"d": 15, "z": "Z2", "desc": "Warm-up"},
                {"d": 20, "z": "Z2", "desc": "Easy - keep legs moving"},
                {"d": 10, "z": "Z1", "desc": "Cool-down"},
            ],
            "desc": "Easy day. Short bike + optional 15min mobility exercises. No exhaustion."
        },
        4: {
            "title": f"🏁 Pre-race: Rest ({race_name} in 4d)",
            "type": "Rest", "dur": 0, "slot": "MAIN", "steps": [],
            "desc": "Rest day. Walk OK. Focus: sleep, hydration, nutrition. Carb load."
        },
        3: {
            "title": f"🏁 Pre-race: Activation ({race_name} in 3d)",
            "type": _prerace_sport, "dur": 55, "slot": "MAIN",
            "steps": [
                {"d": 15, "z": "Z2", "desc": "Warm-up - easy"},
                {"d": 3, "z": "Z4", "desc": "Activation 1 - wake up legs"},
                {"d": 3, "z": "Z1", "desc": "Rest"},
                {"d": 3, "z": "Z4", "desc": "Activation 2 - sharp but short"},
                {"d": 3, "z": "Z1", "desc": "Rest"},
                {"d": 3, "z": "Z4", "desc": "Activation 3 - last time Z4 before race"},
                {"d": 15, "z": "Z2", "desc": "Easy back"},
                {"d": 10, "z": "Z1", "desc": "Cool-down"},
            ],
            "desc": "ACTIVATION! Short, sharp Z4 efforts wake up the nervous system. Max effort 7/10. Not heavy."
        },
        2: {
            "title": f"🏁 Pre-race: Easy {_prerace_sport} ({race_name} in 2d)",
            "type": _prerace_sport, "dur": 30, "slot": "MAIN",
            "steps": [
                {"d": 10, "z": "Z1", "desc": "Easy"},
                {"d": 10, "z": "Z2", "desc": "Light pressure - nothing more"},
                {"d": 10, "z": "Z1", "desc": "Cool-down"},
            ],
            "desc": "Just spin the legs. 30 min max. Save everything for the race."
        },
        1: {
            "title": f"🏁 Pre-race: Rest/Short activation ({race_name} TOMORROW!)",
            "type": _prerace_sport, "dur": 20, "slot": "MAIN",
            "steps": [
                {"d": 10, "z": "Z1", "desc": "Extremely easy"},
                {"d": 1, "z": "Z5", "desc": "30s sprint - race-pace reminder"},
                {"d": 2, "z": "Z1", "desc": "Rest"},
                {"d": 1, "z": "Z5", "desc": "30s sprint"},
                {"d": 6, "z": "Z1", "desc": "Cool-down"},
            ],
            "desc": "Optional! 20 min with 2x30s sprints. Pack the bag. Check equipment. Sleep 8h+."
        },
        0: {
            "title": f"🏁 RACE DAY: {race_name}!",
            "type": _ds, "dur": 0, "slot": "MAIN", "steps": [],
            "desc": f"RACE DAY! {race_name}. Warm-up 15-20min. Eat breakfast 3h before. 90g CHO/h during. Good luck! 💪"
        },
    }

    for d_before, template in day_templates.items():
        target_date = race_date - timedelta(days=d_before)
        if target_date >= today:
            protocol.append({
                "date":       target_date.isoformat(),
                "days_before": d_before,
                **template,
            })

    return {
        "is_active":    True,
        "race_name":    race_name,
        "race_date":    race_date.isoformat(),
        "days_to_race": days_to_race,
        "protocol":     protocol,
    }


def format_race_week_for_prompt(rw: dict) -> str:
    """Formats the race-week protocol for the AI prompt."""
    if not rw.get("is_active"):
        return ""

    lines = [
        f"🏁 RACE WEEK PROTOCOL – {rw['race_name']} ({rw['race_date']})",
        f"  Days left: {rw['days_to_race']}",
        "",
        "  ⚠️ FOLLOW THIS PROTOCOL EXACTLY. No deviations allowed.",
        "  Overrides all other rules (mesocycle, workout library, etc).",
        "",
    ]
    for p in rw["protocol"]:
        steps_text = " → ".join(f"{s['d']}min {s['z']}" for s in p.get("steps", []))
        lines.append(f"  {p['date']} (-{p['days_before']}d): {p['title']}")
        lines.append(f"    {p['type']} | {p['dur']}min")
        if steps_text:
            lines.append(f"    Steps: {steps_text}")
        lines.append(f"    {p['desc']}")
        lines.append("")

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# RETURN TO PLAY
# ══════════════════════════════════════════════════════════════════════════════

def check_return_to_play(activities: list, today: date) -> dict:
    """
    Checks if the athlete has had 5 or more days in a row completely without training
    and in that case triggers a Return to Play protocol.
    """
    days_off = 0
    for i in range(1, 14):
        check_date = (today - timedelta(days=i)).isoformat()
        daily_acts = [a for a in activities if a.get("start_date_local", "")[:10] == check_date]
        moving_time = sum((a.get("moving_time") or a.get("elapsed_time") or 0) for a in daily_acts)
        tss = sum((a.get("icu_training_load", 0) or 0) for a in daily_acts)
        if moving_time < 900 and tss < 10:  # < 15 min AND < 10 TSS counts as rest day
            daily_acts = [a for a in activities if a.get("start_date_local", "")[:10] == check_date and a.get("type") not in ("Rest", "Note")]
        
        if not daily_acts:
            days_off += 1
            continue
            
        total_time = sum((a.get("moving_time") or a.get("elapsed_time") or 0) for a in daily_acts)
        total_tss  = sum((a.get("icu_training_load") or 0) for a in daily_acts)
        has_rpe    = any((a.get("perceived_exertion") or 0) > 0 for a in daily_acts)
        has_strength = any(a.get("type", "") in ("WeightTraining", "Strength") for a in daily_acts)
        
        if total_time >= 900 or total_tss >= 10 or has_rpe or has_strength:
            break  # Training logged and valid -> break rest day chain!
        else:
            break
            days_off += 1  # Activity was completely insignificant (e.g. 5 min walk)
    return {"is_active": days_off >= 5, "days_off": days_off}

# ══════════════════════════════════════════════════════════════════════════════
# TAPER QUALITY SCORE
# ══════════════════════════════════════════════════════════════════════════════

def taper_quality_score(fitness_history: list, race_date: Optional[date],
                        taper_days: int = 14) -> dict:
    """
    Measures if the taper is executed correctly:
    - CTL should drop 5-10% during taper
    - TSB should rise to +5 to +15 on race day
    - ATL should drop quickly (30-50%)

    Returns:
      is_in_taper:     bool
      taper_day:       int (which day of the taper)
      ctl_drop_pct:    actual CTL decrease in %
      tsb_rise:        TSB change
      atl_drop_pct:    ATL decrease in %
      score:           0-100 quality score
      verdict:         text assessment
      adjustments:     list of adjustments if it goes wrong
    """
    if not race_date or not fitness_history:
        return {"is_in_taper": False, "score": None}

    today = date.today()
    days_to_race = (race_date - today).days

    if days_to_race > taper_days or days_to_race < 0:
        return {"is_in_taper": False, "score": None, "days_to_race": days_to_race}

    taper_day = taper_days - days_to_race  # Day 1, 2, ... of the taper
    taper_progress = taper_day / taper_days  # 0.0 → 1.0

    # CTL at taper start vs now
    taper_start_idx = max(0, len(fitness_history) - taper_day - 1)
    ctl_at_start = fitness_history[taper_start_idx].get("ctl", 0) if taper_start_idx < len(fitness_history) else 0
    ctl_now = fitness_history[-1].get("ctl", 0) if fitness_history else 0
    atl_at_start = fitness_history[taper_start_idx].get("atl", 0) if taper_start_idx < len(fitness_history) else 0
    atl_now = fitness_history[-1].get("atl", 0) if fitness_history else 0
    tsb_at_start = fitness_history[taper_start_idx].get("tsb", 0) if taper_start_idx < len(fitness_history) else 0
    tsb_now = fitness_history[-1].get("tsb", 0) if fitness_history else 0

    ctl_drop_pct = round((ctl_at_start - ctl_now) / max(ctl_at_start, 1) * 100, 1) if ctl_at_start else 0
    atl_drop_pct = round((atl_at_start - atl_now) / max(atl_at_start, 1) * 100, 1) if atl_at_start else 0
    tsb_rise = round(tsb_now - tsb_at_start, 1)

    # Expected values at this point in the taper
    expected_ctl_drop = taper_progress * 8  # Expected 5-10% CTL drop at the end
    expected_atl_drop = taper_progress * 40  # ATL should drop 30-50%
    expected_tsb = taper_progress * 15  # TSB should rise ~15 points

    # Scoring (0-100)
    score = 0

    # CTL drop: 5-10% = perfect, <3% = too little rest, >15% = too much
    if 3 <= ctl_drop_pct <= 12:
        score += 35
    elif ctl_drop_pct < 3:
        score += max(0, 35 - (3 - ctl_drop_pct) * 10)
    else:
        score += max(0, 35 - (ctl_drop_pct - 12) * 5)

    # ATL drop: 25-50% = good
    if 20 <= atl_drop_pct <= 55:
        score += 30
    elif atl_drop_pct < 20:
        score += max(0, 30 - (20 - atl_drop_pct))
    else:
        score += max(0, 30 - (atl_drop_pct - 55))

    # TSB rise: should be positive and rising
    if tsb_rise > 0:
        score += min(35, round(tsb_rise * 3))
    else:
        score += max(0, 35 + round(tsb_rise * 3))

    score = max(0, min(100, score))

    # Verdict
    if score >= 80:
        verdict = "✅ Excellent taper! Form and freshness are building optimally."
    elif score >= 60:
        verdict = "🟡 OK taper, but room for improvement."
    elif score >= 40:
        verdict = "🟠 The taper is not working optimally."
    else:
        verdict = "🔴 Taper failing - address immediately."

    # Adjustments
    adjustments = []
    if ctl_drop_pct < 2 and taper_day >= 5:
        adjustments.append("CTL dropping too slowly - you are training too hard during the taper. Reduce volume more.")
    if ctl_drop_pct > 15:
        adjustments.append("CTL dropping too fast - you are resting too much. Keep short activation sessions.")
    if atl_drop_pct < 15 and taper_day >= 7:
        adjustments.append("ATL not dropping enough - still too high acute load.")
    if tsb_now < -5 and days_to_race < 5:
        adjustments.append(f"⚠️ TSB still negative ({tsb_now}) with {days_to_race}d left! Rest more.")
    if tsb_now > 25 and days_to_race > 3:
        adjustments.append("TSB very high - risk of losing sharpness. Add short activation sessions.")

    return {
        "is_in_taper":   True,
        "taper_day":     taper_day,
        "taper_days":    taper_days,
        "days_to_race":  days_to_race,
        "ctl_at_start":  round(ctl_at_start, 1),
        "ctl_now":       round(ctl_now, 1),
        "ctl_drop_pct":  ctl_drop_pct,
        "atl_at_start":  round(atl_at_start, 1),
        "atl_now":       round(atl_now, 1),
        "atl_drop_pct":  atl_drop_pct,
        "tsb_at_start":  round(tsb_at_start, 1),
        "tsb_now":       round(tsb_now, 1),
        "tsb_rise":      tsb_rise,
        "score":         score,
        "verdict":       verdict,
        "adjustments":   adjustments,
        "summary": (
            f"Taper day {taper_day}/{taper_days} | Score: {score}/100 {verdict}\n"
            f"  CTL: {round(ctl_at_start)}→{round(ctl_now)} ({ctl_drop_pct:+.1f}%) | "
            f"ATL: {round(atl_at_start)}→{round(atl_now)} ({atl_drop_pct:+.1f}%) | "
            f"TSB: {round(tsb_at_start)}→{round(tsb_now)} ({tsb_rise:+.1f})"
            + ("\n  Adjustments: " + " ".join(adjustments) if adjustments else "")
        ),
    }



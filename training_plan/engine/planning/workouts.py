from training_plan.core.common import *
from training_plan.engine.libraries import *
from training_plan.engine.planning.learning import WORKOUT_LIBRARY
from training_plan.engine.calendar_context import sport_group
from training_plan.engine.planning.state import is_ai_generated, load_state, save_state

def recommend_prehab(injury_note: str, dominant_sport: str) -> dict:
    """Väljer rätt prehab-rutin baserat på skada och dominant sport."""
    inj = (injury_note or "").lower()
    if any(k in inj for k in ["knee", "knä", "hip", "höft", "thigh", "lår", "back", "rygg", "it-band", "piriformis", "ischiasnerv"]):
        key = "cyclist"
    elif any(k in inj for k in ["calf", "vad", "achilles", "hälsena", "foot", "fot", "ankle", "ankel", "shin", "skena", "plantar"]):
        key = "runner"
    elif dominant_sport in ("Ride", "VirtualRide"):
        key = "cyclist"
    elif dominant_sport == "Run":
        key = "runner"
    else:
        key = "general"
    return PREHAB_LIBRARY[key]


def pre_race_logistics_advice(days_to_race: int) -> str:
    """Returnerar logistik- och sömnråd baserat på dagar kvar till tävling."""
    if days_to_race > 14:
        return ""
    advice = []
    if days_to_race == 14:
        advice.append("2 weeks to start: Confirm accommodation, packing list ready, helmet/shoes checked.")
    elif days_to_race == 7:
        advice.append("1 week: Bike service (tires, cables, brake pads). Test race nutrition in training. Charge Garmin.")
    elif days_to_race == 3:
        advice.append("3 days: Registration. Start carb loading. Sleep 8h+. Minimal travel stress.")
    elif days_to_race == 2:
        advice.append("Day before: Rest and prepare. Fix bib/chip. Pack bag the night before. Sleep 9h if possible.")
    elif days_to_race == 1:
        advice.append("TOMORROW IS RACE DAY: Breakfast: rice/oatmeal + banana. Pack bag. 9h sleep. No new foods.")
    return " | ".join(advice)


def get_strength_workout_for_phase(mesocycle: dict) -> dict:
    """
    Väljer rätt styrkefas baserat på mesocykelvecka och träningsfas.
    Fas 1 (bas): Hög rep kroppsvikt → Fas 2 (bygg): Tyngre kroppsvikt → Fas 3 (underhåll): Stabilitet.
    """
    week = mesocycle.get("week_in_block", 1)
    is_deload = mesocycle.get("is_deload", False)
    phase_name = mesocycle.get("phase_name", "Base") if isinstance(mesocycle, dict) else "Base"

    if is_deload or phase_name in ("Taper", "Race Week"):
        return STRENGTH_LIBRARY["underhall_styrka"]
    elif week <= 2:
        return STRENGTH_LIBRARY["bas_styrka"]
    else:
        return STRENGTH_LIBRARY["bygg_styrka"]


def get_next_workouts(levels: dict, phase: str) -> str:
    lines = ["WORKOUT LIBRARY – Next progression per type:"]
    for wk_key, wk_def in WORKOUT_LIBRARY.items():
        if phase not in wk_def.get("phase", []):
            continue
        current_level = levels.get(wk_key, 1)
        rec_level = min(current_level, len(wk_def["levels"]))
        lvl = wk_def["levels"][rec_level - 1]
        steps_text = " → ".join(f"{s['d']}min {s['z']}" for s in lvl["steps"])
        lines.append(
            f"  [{wk_key}] {wk_def['name']} — Level {rec_level}: {lvl['label']}"
            f"\n    Steps: {steps_text} (Total: {lvl['total_min']}min)"
            f"\n    Sport: {', '.join(wk_def['sport'])}"
        )
        if rec_level < len(wk_def["levels"]):
            nxt = wk_def["levels"][rec_level]
            lines.append(f"    → NEXT LEVEL ({rec_level+1}): {nxt['label']} ({nxt['total_min']}min)")
    return "\n".join(lines)


def build_progression_directive(levels: dict, phase: str) -> str:
    """
    Return a tightly-scoped progression object for the AI prompt.

    For each workout type relevant to the current phase, shows:
      - Current level label (what to do now)
      - Exact step sequence (copy-paste ready)
      - Target: next level label and what it will unlock

    The AI should copy the current-level steps verbatim into workout_steps.
    Only progress to the next level after the athlete masters the current one (RPE ≤ 7).
    """
    lines = ["PROGRESSION DIRECTIVE – copy current-level steps verbatim into workout_steps:"]
    for wk_key, wk_def in WORKOUT_LIBRARY.items():
        if phase not in wk_def.get("phase", []):
            continue
        current_level = levels.get(wk_key, 1)
        max_level = len(wk_def["levels"])
        rec_level = min(current_level, max_level)
        lvl = wk_def["levels"][rec_level - 1]
        steps_detail = " → ".join(f"{s['d']}min {s['z']} ({s['desc']})" for s in lvl["steps"])
        lines.append(
            f"\n  [{wk_key}] {wk_def['name']} ({', '.join(wk_def['sport'])})"
            f"\n    CURRENT (Level {rec_level}/{max_level}): {lvl['label']} — {lvl['total_min']}min total"
            f"\n    Steps: {steps_detail}"
        )
        if rec_level < max_level:
            nxt = wk_def["levels"][rec_level]
            nxt_steps = " → ".join(f"{s['d']}min {s['z']}" for s in nxt["steps"])
            lines.append(
                f"    TARGET (Level {rec_level + 1}): {nxt['label']} — {nxt['total_min']}min total"
                f"\n    Next steps: {nxt_steps}"
                f"\n    Unlock condition: athlete completes current level with RPE ≤ 7"
            )
        else:
            lines.append(f"    STATUS: Max level reached — maintain quality, vary execution context.")
    return "\n".join(lines)


def workout_key_for(planned_name: str, planned_dur: int = 0) -> Optional[str]:
    """The library workout a planned session came from.

    Planner sessions are titled "<library name> – <level>", so the name decides. Older or
    AI-written titles fall back to an "NxM" interval structure, or the duration of a long ride.
    """
    name = (planned_name or "").lower()
    for key, wk_def in sorted(WORKOUT_LIBRARY.items(), key=lambda kv: -len(kv[1]["name"])):
        if name.startswith(wk_def["name"].lower()):
            return key
    wk_key = None
    for key, wk_def in WORKOUT_LIBRARY.items():
        for lvl in wk_def["levels"]:
            label = lvl["label"].lower()
            # Matchar på struktur som "4x8min"
            key_parts = re.findall(r"(\d+)\s*[x×]\s*(\d+)", label)
            if key_parts:
                reps, mins = key_parts[0]
                if re.search(rf"{reps}\s*[x×]\s*{mins}", planned_name):
                    wk_key = key
                    break
            # Matchar på total duration för långpass
            if key == "long_ride_progression":
                if abs(planned_dur - lvl["total_min"]) < lvl["total_min"] * 0.10:
                    wk_key = key
                    break
        if wk_key:
            break
    return wk_key


def check_and_advance_workout_progression(yesterday_planned: Optional[dict], yesterday_actuals: list, state: dict):
    """Move yesterday's library session one level up or down, once per session.

    - Up when it was done and felt controlled: RPE <= 7 and/or feel <= 3 (whatever was logged).
    - Down when it was too hard: RPE >= 9 or feel >= 4.
    - Unchanged without RPE or feel: no logged response, no change (a completed session
      alone says nothing about how hard it was).
    The activity must be in the same sport as the planned session, and a session is judged
    only once even when the planner runs several times a day.
    """
    if not yesterday_planned or not yesterday_actuals or not is_ai_generated(yesterday_planned):
        return
    session_id = f"{(yesterday_planned.get('start_date_local') or '')[:10]}|{yesterday_planned.get('name') or ''}"
    if state.get("progression_checked") == session_id:
        return
    planned_type = yesterday_planned.get("type") or ""
    actual = next((a for a in yesterday_actuals
                   if sport_group(a.get("type")) == sport_group(planned_type)), None) if planned_type else yesterday_actuals[0]
    if actual is None:
        return
    wk_key = workout_key_for(yesterday_planned.get("name") or "",
                             round((yesterday_planned.get("moving_time", 0) or 0) / 60))
    if not wk_key:
        return # Inget bibliotekspass hittades
    state["progression_checked"] = session_id

    rpe = actual.get("perceived_exertion")
    feel = actual.get("feel")
    if rpe is None and feel is None:
        log.info(f"ℹ️ Session '{wk_key}' done without RPE/feel – level unchanged.")
        return
    too_hard = (rpe is not None and rpe >= 9) or (feel is not None and feel >= 4)
    controlled = (rpe is None or rpe <= 7) and (feel is None or feel <= 3)
    if too_hard:
        log.info(f"🔻 Session '{wk_key}' was too hard (RPE: {rpe}, Feel: {feel}). One level down.")
        change_workout_level(wk_key, state, -1)
    elif controlled:
        log.info(f"✅ Session '{wk_key}' mastered (RPE: {rpe or 'N/A'}, Feel: {feel or 'N/A'}).")
        advance_workout_level(wk_key, state) # Denna funktion sparar state
    else:
        log.info(f"🟡 Session '{wk_key}' completed but not mastered (RPE: {rpe}, Feel: {feel}). Not advancing.")


def change_workout_level(wk_key: str, state: dict, step: int):
    levels = state.get("workout_levels", {})
    max_level = len(WORKOUT_LIBRARY.get(wk_key, {}).get("levels", []))
    new = max(1, min(levels.get(wk_key, 1) + step, max_level))
    if new != levels.get(wk_key, 1):
        levels[wk_key] = new
        state["workout_levels"] = levels
        save_state(state)
        log.info(f"Workout library: {wk_key} → level {new}")


def reduce_levels_after_break(state: dict, break_id: str):
    """One level down for every workout after a break (illness, travel): once per break."""
    if not state.get("workout_levels") or state.get("levels_reduced_for") == break_id:
        return
    state["workout_levels"] = {k: max(1, v - 1) for k, v in state["workout_levels"].items()}
    state["levels_reduced_for"] = break_id
    save_state(state)
    log.info("📉 Workout levels lowered one step after the break.")


def advance_workout_level(wk_key: str, state: dict):
    levels = state.get("workout_levels", {})
    current = levels.get(wk_key, 1)
    max_level = len(WORKOUT_LIBRARY.get(wk_key, {}).get("levels", []))
    if current < max_level:
        levels[wk_key] = current + 1
        state["workout_levels"] = levels
        save_state(state)
        log.info(f"📈 Workout library: {wk_key} advanced to level {current + 1}")


def autoregulate_from_yesterday(yesterday_raw: dict, state: dict) -> list:
    """
    Analyserar gårdagens prestation och justerar passprogressionen i realtid.
    Returnerar en lista med signaler som injiceras i AI-prompten.

    - RPE <= 5 + mycket bra känsla (feel <= 2): dubbel-avancering + FTP-test-signal
    - Missat pass: signal om att INTE kompensera
    """
    signals = []
    if not yesterday_raw:
        return signals

    rpe   = yesterday_raw.get("rpe")
    feel  = yesterday_raw.get("feel")
    wk_key = yesterday_raw.get("workout_key")
    missed = yesterday_raw.get("missed", False)

    session_id = yesterday_raw.get("session_id")
    if (rpe is not None and feel is not None and rpe <= 5 and feel <= 2 and wk_key
            and state.get("autoregulated") != session_id):
        state["autoregulated"] = session_id
        levels = state.get("workout_levels", {})
        current = levels.get(wk_key, 1)
        max_level = len(WORKOUT_LIBRARY.get(wk_key, {}).get("levels", []))
        # One extra level on top of the normal one: +2 in total for an exceptionally easy session.
        steps = min(1, max_level - current)
        if steps > 0:
            levels[wk_key] = current + steps
            state["workout_levels"] = levels
            save_state(state)
            log.info(f"⚡ AUTOREGULATION: {wk_key} +{steps} levels (RPE {rpe}, Feel {feel})")
            signals.append(
                f"AUTOREGULATION: Athlete performed exceptionally well yesterday (RPE {rpe}/10, Feel {feel}/5). "
                f"Workout progression {wk_key} advanced {steps} steps. "
                f"Consider an FTP test within 7 days – current FTP may be underestimated."
            )

    if missed:
        signals.append(
            "MISSED SESSION YESTERDAY: Do NOT compensate with extra volume today. "
            "Keep planned TSS limit. Nearest easy day prioritizes maximum recovery."
        )

    return signals


# ══════════════════════════════════════════════════════════════════════════════
# 6. FTP TEST CHECK
# ══════════════════════════════════════════════════════════════════════════════

def ftp_test_check(activities: list, planned: list, athlete: dict) -> dict:
    ftp_keywords = ["ftp", "ramp test", "ramptest", "20min test", "20 min test", "cp20", "all out", "benchmark"]
    
    current_ftp = None
    for ss in athlete.get("sportSettings", []):
        stypes = ss.get("types", []) if isinstance(ss.get("types"), list) else [ss.get("type")]
        if any(t in ("Ride", "VirtualRide") for t in stypes) and ss.get("ftp"):
            current_ftp = ss["ftp"]
            break

    today = date.today().isoformat()
    for p in planned:
        if p.get("start_date_local", "")[:10] >= today:
            name = (p.get("name", "") or "").lower()
            if any(kw in name for kw in ftp_keywords):
                return {
                    "days_since_test": None,
                    "needs_test": False,
                    "current_ftp": current_ftp,
                    "if_suggests_update": False,
                    "recommendation": f"FTP test already scheduled ({p.get('start_date_local', '')[:10]}).",
                    "reasons": [],
                    "suggested_protocol": ""
                }

    last_test_date = None
    for a in reversed(activities):
        name = (a.get("name", "") or "").lower()
        if any(kw in name for kw in ftp_keywords):
            try:
                last_test_date = datetime.strptime(a["start_date_local"][:10], "%Y-%m-%d").date()
                break
            except Exception:
                continue
    state = load_state()
    saved_test = state.get("last_ftp_test")
    if saved_test:
        try:
            saved_dt = datetime.strptime(saved_test, "%Y-%m-%d").date()
            if last_test_date is None or saved_dt > last_test_date:
                last_test_date = saved_dt
        except Exception:
            pass
    days_since = (date.today() - last_test_date).days if last_test_date else None
    recent_ifs = [
        a.get("icu_intensity", 0) or 0
        for a in activities[-10:]
        if a.get("icu_intensity") and a.get("type") in ("Ride", "VirtualRide")
    ]
    high_if_count = sum(1 for x in recent_ifs if x > 1.05)
    if_suggests_update = high_if_count >= 3 and len(recent_ifs) >= 5
    needs_test = False
    reasons = []
    if days_since is None:
        needs_test = True
        reasons.append("No FTP test found in history")
    elif days_since > 42:
        needs_test = True
        reasons.append(f"{days_since} days since last test (recommended: every 6th week)")
    if if_suggests_update:
        needs_test = True
        reasons.append(f"{high_if_count} of last {len(recent_ifs)} sessions had IF > 1.05 – FTP may be too low")
    recommendation = ""
    if needs_test:
        recommendation = "🔬 TIME FOR FTP TEST! " + ". ".join(reasons) + "."
    else:
        recommendation = f"FTP test OK (last {days_since}d ago)."
    return {
        "days_since_test":    days_since,
        "needs_test":         needs_test,
        "current_ftp":        current_ftp,
        "if_suggests_update": if_suggests_update,
        "recommendation":     recommendation,
        "reasons":            reasons,
        "suggested_protocol": (
            "Recommended protocol - choose ONE of these:\n"
            "\n"
            "  A) RAMP TEST (recommended for beginners/indoors):\n"
            "     Warm-up 10min Z1 → Ramp: increase power 20W every 1min until exhaustion.\n"
            "     Starting watts: approx 50% FTP. FTP = 75% of highest completed minute average power.\n"
            "     Total time approx 25-35min. Easy to perform to max.\n"
            "\n"
            "  B) 20-MINUTE TEST (classic):\n"
            "     Warm-up 15min Z2 + 2×3min Z4 + 5min Z1 →\n"
            "     20min all-out effort → FTP = average power × 0.95\n"
            "     Total time approx 50-60min. Requires experience with even pacing.\n"
            "\n"
            "  Do on a rested day (TSB > 5). Full gas. Zwift/Garmin measures automatically."
        ) if needs_test else "",
    }


# ══════════════════════════════════════════════════════════════════════════════
# 7. WEEKLY REPORT
# ══════════════════════════════════════════════════════════════════════════════

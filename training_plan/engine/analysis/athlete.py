from training_plan.core.common import *
from training_plan.engine.libraries import *
from training_plan.engine.planning import *
from training_plan.engine.utils import safe_date_str, safe_date
from training_plan.engine.analysis.data import _sorted_wellness

def parse_zones(athlete):
    lines = []
    names = {"Ride":"Cycling","Run":"Running","NordicSki":"Cross-country skiing","RollerSki":"Roller skiing","VirtualRide":"Zwift"}
    for ss in athlete.get("sportSettings", []):
        stypes = ss.get("types", []) if isinstance(ss.get("types"), list) else [ss.get("type")]
        t_names = [names.get(x, x) for x in stypes if x]
        t = "/".join(t_names) if t_names else "Standard zones"
        parts = []
        if ss.get("ftp"):    parts.append(f"FTP {ss['ftp']}W")
        if ss.get("lthr"):   parts.append(f"LTHR {ss['lthr']}bpm")
        if ss.get("max_hr"): parts.append(f"MaxHR {ss['max_hr']}bpm")
        if parts: lines.append(f"  {t}: {', '.join(parts)}")
        ftp = ss.get("ftp"); lthr = ss.get("lthr")
        zones = ss.get("zones") or []; hr_z = ss.get("hrZones") or []
        if ftp and zones:
            zs = " | ".join(f"{z.get('name','Z'+str(i+1))}: {round(z.get('min',0)*ftp/100)}-{round(z.get('max',0)*ftp/100)}W"
                            for i,z in enumerate(zones) if z.get("min") and z.get("max"))
            if zs: lines.append(f"    Power zones: {zs}")
        if lthr and hr_z:
            zs = " | ".join(f"{z.get('name','Z'+str(i+1))}: {round(z.get('min',0)*lthr/100)}-{round(z.get('max',0)*lthr/100)}bpm"
                            for i,z in enumerate(hr_z) if z.get("min") and z.get("max"))
            if zs: lines.append(f"    HR zones: {zs}")
    return "\n".join(lines) if lines else "  No sport settings found."

def env_nutrition(temp_max, duration_min, first_zone, all_zones=None):
    advice = []
    zones = all_zones if all_zones else [first_zone]
    entirely_low = all(z in ("Z1", "Z2", "Zone 1", "Zone 2") for z in zones)
    if temp_max > 25: advice.append("HEAT: +200ml/h. Electrolytes (>=800mg Na/l).")
    elif temp_max < 0: advice.append("COLD: Drink according to schedule. Keep drink lukewarm.")
    if entirely_low and duration_min < 90: advice.append("TRAIN LOW: Opportunity to ride fasted for fat adaptation.")
    return advice

def biometric_vetoes(hrv, life_stress):
    rules = []
    if hrv["state"] == "LOW" or hrv["stability"] == "UNSTABLE":
        rules.append("HRV_LOW: No sessions above Z2. Convert to Z1/rest.")
    elif hrv["state"] == "SLIGHTLY_LOW":
        rules.append("HRV_SLIGHTLY_LOW: Avoid Z4+.")
    if life_stress >= 4:
        rules.append("LIFE_STRESS_HIGH: No intervals above threshold. Lower IF by 15%.")
    return rules

# ══════════════════════════════════════════════════════════════════════════════
# YESTERDAY ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════

def analyze_yesterday(yesterday_planned, yesterday_actuals, activities) -> str:
    """
    Builds a detailed analysis of yesterday's planned vs actual sessions
    that is sent to the AI for feedback.
    """
    yesterday_date = (date.today() - timedelta(days=1)).isoformat()
    if not yesterday_planned or not is_ai_generated(yesterday_planned):
        if yesterday_actuals:
            a = yesterday_actuals[0]
            return (
                f"YESTERDAY ({yesterday_date}): No AI-planned session yesterday, but activity registered:\n"
                f"  Type: {a.get('type','?')} | {round((a.get('moving_time',0) or 0)/60)}min | "
                f"TSS: {a.get('icu_training_load','?')} | HR: {a.get('average_heartrate','?')}bpm | "
                f"RPE: {a.get('perceived_exertion','?')}"
            )
        # Nothing planned, no activity - nothing to give feedback on
        return ""

    planned_name = yesterday_planned.get("name", "?")
    planned_type = yesterday_planned.get("type", "?")
    planned_dur = round((yesterday_planned.get("moving_time", 0) or 0) / 60)
    planned_desc = (yesterday_planned.get("description", "") or "").replace(AI_TAG, "").strip()[:500]

    if not yesterday_actuals:
        return (
            f"MISSED SESSION YESTERDAY ({yesterday_date}):\n"
            f"  Planned: {planned_name} ({planned_type}, {planned_dur}min)\n"
            f"  Description: {planned_desc[:200]}\n"
            f"  Actual: Nothing registered.\n"
            f"  -> Give feedback: What was missed? Is it a compliance trend?"
        )

    lines = [f"YESTERDAY'S ({yesterday_date}) PLANNED SESSION:\n  {planned_name} ({planned_type}, {planned_dur}min)"]
    lines.append(f"  Plan description: {planned_desc[:300]}")
    lines.append(f"\nYESTERDAY'S ACTUAL ACTIVITY(IES):")

    for a in yesterday_actuals:
        actual_dur = round((a.get("moving_time", 0) or 0) / 60)
        actual_dist = round((a.get("distance", 0) or 0) / 1000, 1)
        lines.append(
            f"  {a.get('type','?')} | {actual_dur}min | {actual_dist}km | "
            f"TSS: {fmt(a.get('icu_training_load'))} | "
            f"HR: {fmt(a.get('average_heartrate'),'bpm')} (max {fmt(a.get('max_heartrate'),'bpm')}) | "
            f"NP: {fmt(a.get('icu_weighted_avg_watts'),'W')} | IF: {fmt(a.get('icu_intensity'))} | "
            f"RPE: {fmt(a.get('perceived_exertion'))} | Feel: {fmt(a.get('feel'))}/5"
        )

        # Comparison analysis
        dur_diff = actual_dur - planned_dur
        if abs(dur_diff) > 10:
            lines.append(f"  Δ Duration: {dur_diff:+d}min vs planned")

        # Zone analysis
        pz = format_zone_times(a.get("icu_zone_times")); hz = format_zone_times(a.get("icu_hr_zone_times"))
        if pz: lines.append(f"  Power zones: {pz}")
        if hz: lines.append(f"  HR zones: {hz}")

    lines.append(
        "\n  -> Give feedback: Was the plan followed? Right intensity? What can be improved? "
        "Was nutrition sufficient? Concrete tips."
    )


# ── ATHLETE PROFILE ───────────────────────────────────────────────────────────

def _parse_birth_date(value):
    if not value:
        return None
    text = str(value)[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except Exception:
        return None


def athlete_profile(athlete: dict | None, wellness: list | None = None) -> dict:
    """Extract coach-relevant athlete stats from intervals.icu data when present."""
    athlete = athlete or {}
    wellness = _sorted_wellness(wellness or [])

    birth = (
        _parse_birth_date(athlete.get("dob"))
        or _parse_birth_date(athlete.get("dateOfBirth"))
        or _parse_birth_date(athlete.get("birthDate"))
        or _parse_birth_date(athlete.get("birthday"))
    )
    age = None
    if birth:
        today = date.today()
        age = today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))

    weight = athlete.get("weight") or athlete.get("weightKg")
    if weight is None:
        for w in reversed(wellness):
            if w.get("weight"):
                weight = w.get("weight")
                break
    try:
        weight = round(float(weight), 1) if weight is not None else None
    except (TypeError, ValueError):
        weight = None

    settings = athlete.get("sportSettings", []) if isinstance(athlete.get("sportSettings"), list) else []
    ftp_by_sport = {}
    max_hr_by_sport = {}
    lthr_by_sport = {}
    for setting in settings:
        types = setting.get("types", []) if isinstance(setting.get("types"), list) else [setting.get("type")]
        for sport in [item for item in types if item]:
            if setting.get("ftp"):
                ftp_by_sport[sport] = setting.get("ftp")
            if setting.get("max_hr"):
                max_hr_by_sport[sport] = setting.get("max_hr")
            if setting.get("lthr"):
                lthr_by_sport[sport] = setting.get("lthr")

    cycling_ftp = next((ftp_by_sport[s] for s in ("Ride", "VirtualRide") if s in ftp_by_sport), None)
    watts_per_kg = None
    if cycling_ftp and weight:
        try:
            watts_per_kg = round(float(cycling_ftp) / weight, 2)
        except (TypeError, ValueError, ZeroDivisionError):
            watts_per_kg = None

    return {
        "name": athlete.get("name") or athlete.get("athleteName") or "",
        "age": age,
        "sex": athlete.get("sex") or athlete.get("gender"),
        "weight_kg": weight,
        "ftp_by_sport": ftp_by_sport,
        "max_hr_by_sport": max_hr_by_sport,
        "lthr_by_sport": lthr_by_sport,
        "cycling_ftp_w_per_kg": watts_per_kg,
    }


def format_athlete_profile(athlete: dict | None, wellness: list | None = None) -> str:
    profile = athlete_profile(athlete, wellness)
    parts = []
    if profile.get("age") is not None:
        parts.append(f"Age {profile['age']}")
    if profile.get("sex"):
        parts.append(f"Sex/gender {profile['sex']}")
    if profile.get("weight_kg") is not None:
        parts.append(f"Weight {profile['weight_kg']}kg")
    if profile.get("cycling_ftp_w_per_kg") is not None:
        parts.append(f"Cycling FTP {profile['cycling_ftp_w_per_kg']}W/kg")
    if profile.get("ftp_by_sport"):
        ftp_text = ", ".join(f"{sport} {ftp}W" for sport, ftp in sorted(profile["ftp_by_sport"].items()))
        parts.append(f"FTP by sport: {ftp_text}")
    if profile.get("max_hr_by_sport"):
        hr_text = ", ".join(f"{sport} {hr}bpm" for sport, hr in sorted(profile["max_hr_by_sport"].items()))
        parts.append(f"Max HR by sport: {hr_text}")
    if not parts:
        return "No explicit age/weight/sex profile found; individualization relies on training history, zones, wellness, and compliance."
    return " | ".join(parts)


# ── TSS REFERENCE ─────────────────────────────────────────────────────────────

def compute_tss_reference(activities: list) -> str:
    """Return a calibrated TSS cheat sheet derived from the athlete's own history.

    Groups completed sessions by sport, computes median TSS/hour per sport (and
    by intensity for VirtualRide where power data is available), then formats a
    compact reference string to inject into the generation prompt.

    Falls back to theoretical zone-formula values if there is too little data for
    a given sport type.
    """
    _SPORTS = tuple(s["intervals_type"] for s in SPORTS if s["intervals_type"] not in ("WeightTraining", "Rest"))
    _MIN_DURATION_H = 20 / 60   # exclude sessions < 20 min
    _MIN_TSS = 10
    _MAX_TSS_PER_H = 200        # sanity cap

    def _median(vals):
        if not vals:
            return None
        s = sorted(vals)
        m = len(s) // 2
        return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2

    # Collect (duration_h, tss, if_val_or_None) per sport
    by_sport: dict[str, list] = {s: [] for s in _SPORTS}
    for a in activities:
        sport = a.get("type", "")
        if sport not in _SPORTS:
            continue
        tss = a.get("icu_training_load") or 0
        dur_h = ((a.get("moving_time") or a.get("elapsed_time") or 0)) / 3600
        if tss < _MIN_TSS or dur_h < _MIN_DURATION_H:
            continue
        rate = tss / dur_h
        if rate > _MAX_TSS_PER_H:
            continue
        if_val = session_intensity(a)   # returns 0.0–2.0 or None
        by_sport[sport].append((dur_h, tss, rate, if_val))

    lines = ["  TSS CHEAT SHEET (calibrated from your training history):"]

    # ── VirtualRide (power-based → reliable IF split) ─────────────────────────
    vr = by_sport["VirtualRide"]
    if vr:
        easy = [r for _, _, r, ifv in vr if ifv is not None and ifv < 0.80]
        hard = [r for _, _, r, ifv in vr if ifv is not None and ifv >= 0.80]
        all_rates = [r for _, _, r, _ in vr]
        lines.append(f"  VirtualRide/Zwift (power-based, N={len(vr)}):")
        if len(easy) >= 3:
            h = round(_median(easy))
            lines.append(f"    Easy/Z2 (IF<0.80):   1h={h} | 90min={round(h*1.5)} | 2h={h*2} | 3h={h*3} | 4h={h*4} TSS")
        else:
            lines.append("    Easy/Z2:   1h≈49 | 90min≈74 | 2h≈98 | 3h≈147 | 4h≈196 TSS (formula, limited data)")
        if len(hard) >= 3:
            h = round(_median(hard))
            lines.append(f"    Hard/Z3-Z5 (IF≥0.80): 1h={h} | 70min={round(h*70/60)} | 90min={round(h*1.5)} TSS")
        else:
            lines.append("    Hard/Z3-Z5: 1h≈82 | 70min≈96 | 90min≈123 TSS (formula, limited data)")
    else:
        lines.append("  VirtualRide/Zwift: 1h≈49 | 2h≈98 | 3h≈147 | 4h≈196 TSS (formula, no history yet)")

    # ── Ride outdoor (HR-based → no reliable intensity split) ─────────────────
    rides = by_sport["Ride"]
    if len(rides) >= 3:
        h = round(_median([r for _, _, r, _ in rides]))
        lines.append(f"  Ride outdoor (HR-based, N={len(rides)}): 1h={h} | 2h={h*2} | 3h={h*3} | 4h={h*4} | 5h={h*5} TSS")
    else:
        lines.append("  Ride outdoor (HR-based): 1h≈44 | 2h≈88 | 3h≈132 | 4h≈176 | 5h≈220 TSS (limited data)")

    # ── Run ───────────────────────────────────────────────────────────────────
    runs = by_sport["Run"]
    if len(runs) >= 3:
        h = round(_median([r for _, _, r, _ in runs]))
        lines.append(f"  Run (N={len(runs)}): 1h={h} | 90min={round(h*1.5)} | 2h={h*2} TSS")
    else:
        lines.append("  Run: 1h≈55 | 90min≈83 | 2h≈110 TSS (limited data)")

    # ── RollerSki ─────────────────────────────────────────────────────────────
    rs = by_sport["RollerSki"]
    if len(rs) >= 3:
        h = round(_median([r for _, _, r, _ in rs]))
        lines.append(f"  RollerSki (N={len(rs)}): 1h={h} | 90min={round(h*1.5)} | 2h={h*2} TSS")
    else:
        lines.append("  RollerSki: 1h≈50 | 90min≈75 | 2h≈100 TSS (limited data)")

    lines.append("  WeightTraining: ~15-20 TSS/session | Rest: 0 TSS")
    return "\n".join(lines)
    return "\n".join(lines)

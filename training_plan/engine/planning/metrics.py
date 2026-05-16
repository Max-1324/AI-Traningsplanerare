from training_plan.core.common import *
from training_plan.core.models import AppState
from training_plan.engine.libraries import *
from training_plan.engine.utils import safe_date_str

def _weekly_tss_history(activities: list, weeks: int = 6) -> list[dict]:
    today = date.today()
    result = []
    for w in range(weeks, 0, -1):
        week_start = today - timedelta(days=today.weekday() + 7 * w)
        week_end   = week_start + timedelta(days=7)
        tss = sum(
            a.get("icu_training_load", 0) or 0
            for a in activities
            if safe_date_str(a) and week_start.isoformat() <= safe_date_str(a) < week_end.isoformat()
        )
        result.append({"week_start": week_start.isoformat(), "tss": round(tss)})
    return result




def format_zone_times(zt) -> str:
    """Formaterar zon-tider från intervals.icu-data till kompakt text."""
    if not zt or not isinstance(zt, list):
        return ""
    result = []
    for i, s in enumerate(zt):
        if isinstance(s, dict):
            secs = s.get("secs") or s.get("seconds") or s.get("time") or 0
        elif isinstance(s, (int, float)):
            secs = s
        else:
            continue
        if secs and secs > 30:
            result.append(f"Z{i+1}:{round(secs/60)}m")
    return " ".join(result)


_KEY_SESSION_CATEGORIES = {"ftp_test", "long_ride", "threshold", "vo2"}
_SESSION_CATEGORY_LABELS = {
    "ftp_test":   "FTP calibration",
    "long_ride":  "Long ride / durability",
    "threshold":  "Threshold",
    "vo2":        "VO2max",
    "endurance":  "Aerobic base",
    "strength":   "Strength",
    "recovery":   "Recovery",
    "general":    "General session",
}


def session_duration_min(item: dict) -> int:
    direct_min = item.get("duration_min")
    try:
        if direct_min is not None:
            value = int(round(float(direct_min)))
            if value > 0:
                return value
    except Exception:
        pass
    secs = item.get("moving_time") or item.get("elapsed_time") or 0
    return round(secs / 60) if secs else 0


def session_intensity(item: dict) -> float | None:
    val = item.get("icu_intensity")
    try:
        if val is not None:
            v = float(val)
            return v / 100.0 if v > 5.0 else v
        return None
    except Exception:
        return None


def classify_session_category(item: dict) -> str:
    name = (item.get("name") or item.get("title") or "").lower()
    sport = item.get("type") or item.get("intervals_type") or ""
    dur = session_duration_min(item)
    intf = session_intensity(item)

    if any(k in name for k in ["ftp", "ramp test", "ramptest", "20 min test", "20min test", "benchmark"]):
        return "ftp_test"
    if sport == "WeightTraining" or "styrka" in name or "strength" in name:
        return "strength"
    if sport in ("Ride", "VirtualRide") and dur >= 180:
        return "long_ride"
    if any(k in name for k in ["vo2", "intervall", "intervaller", "4x4", "5x5", "fartlek"]):
        return "vo2"
    if any(k in name for k in ["tröskel", "threshold", "sweet spot", "tempo"]):
        return "threshold"
    if intf is not None and dur >= 35:
        if intf >= 0.98:
            return "vo2"
        if intf >= 0.87:
            return "threshold"
        if intf <= 0.65 and dur <= 60:
            return "recovery"
        if dur >= 75 and intf <= 0.80:
            return "endurance"
    if dur >= 75:
        return "endurance"
    if dur > 0 and dur <= 45:
        return "recovery"
    return "general"


def polarization_analysis(activities: list, days: int = 21) -> dict:
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    relevant = [a for a in activities if safe_date_str(a) and safe_date_str(a) >= cutoff]
    if not relevant:
        return {
            "days": days,
            "low_pct": 0,
            "mid_pct": 0,
            "high_pct": 0,
            "verdict": "Insufficient data.",
            "summary": f"Polarization: no activity data last {days} days.",
        }

    zone_mins = [0.0] * 7
    for a in relevant:
        zt = a.get("icu_hr_zone_times") or a.get("icu_zone_times") or []
        for i, z in enumerate(zt):
            if isinstance(z, dict):
                secs = z.get("secs") or z.get("seconds") or z.get("time") or 0
            elif isinstance(z, (int, float)):
                secs = z
            else:
                continue
            if i < 7:
                zone_mins[i] += secs / 60

    total = sum(zone_mins) or 1.0
    low_pct = round((zone_mins[0] + zone_mins[1]) / total * 100)
    mid_pct = round(zone_mins[2] / total * 100) if len(zone_mins) > 2 else 0
    high_pct = round(sum(zone_mins[3:]) / total * 100) if len(zone_mins) > 3 else 0

    if low_pct >= 75 and mid_pct <= 15:
        verdict = "Good polarized distribution."
    elif mid_pct > 20:
        verdict = "Too much Z3/black zone - shift time to pure Z2 or pure Z4+."
    elif high_pct < 8 and low_pct > 85:
        verdict = "Very easy distribution - can tolerate more quality stimuli if recovery is good."
    else:
        verdict = "Neutral distribution."

    return {
        "days": days,
        "low_pct": low_pct,
        "mid_pct": mid_pct,
        "high_pct": high_pct,
        "verdict": verdict,
        "summary": f"Polarization last {days}d: Z1-Z2 {low_pct}% | Z3 {mid_pct}% | Z4+ {high_pct}%. {verdict}",
    }


def session_quality_analysis(activities: list, days: int = 28) -> dict:
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    relevant = [a for a in activities if safe_date_str(a) and safe_date_str(a) >= cutoff]
    if not relevant:
        return {
            "days": days,
            "overall_score": None,
            "category_scores": {},
            "priority_alerts": ["No activity data for session quality."],
            "recent_sessions": [],
            "summary": f"Session quality: no data last {days} days.",
        }

    def clamp_local(v, lo=0, hi=100):
        return max(lo, min(hi, int(round(v))))

    category_scores: dict[str, dict] = {}
    recent_sessions = []

    for a in relevant:
        cat = classify_session_category(a)
        if cat not in _KEY_SESSION_CATEGORIES | {"endurance", "strength", "recovery"}:
            continue

        dur = session_duration_min(a)
        intf = session_intensity(a)
        rpe = a.get("perceived_exertion")
        feel = a.get("feel")
        score = 60

        if cat == "long_ride":
            score = 65
            if dur >= 240:
                score += 15
            elif dur >= 180:
                score += 8
            if intf is not None and intf > 0.82:
                score -= 10
            if rpe is not None:
                score += 10 if rpe <= 6 else (-15 if rpe >= 8 else 0)
            if feel is not None:
                score += 8 if feel <= 2 else (-8 if feel >= 4 else 0)
        elif cat == "endurance":
            score = 60
            if dur >= 90:
                score += 8
            if intf is not None and intf <= 0.80:
                score += 10
            elif intf is not None and intf > 0.85:
                score -= 10
            if rpe is not None:
                score += 8 if rpe <= 5 else (-10 if rpe >= 7 else 0)
        elif cat == "threshold":
            score = 62
            if intf is not None and 0.87 <= intf <= 1.02:
                score += 12
            if rpe is not None:
                score += 10 if 6 <= rpe <= 8 else (-8 if rpe >= 9 else -4 if rpe <= 4 else 0)
            if feel is not None:
                score += 6 if feel <= 3 else (-10 if feel >= 4 else 0)
        elif cat == "vo2":
            score = 60
            if intf is not None and intf >= 0.98:
                score += 10
            if rpe is not None:
                score += 10 if 7 <= rpe <= 9 else (-8 if rpe <= 5 else 0)
            if feel is not None:
                score += 5 if feel <= 3 else (-8 if feel >= 4 else 0)
        elif cat == "strength":
            score = 62
            if feel is not None:
                score += 8 if feel <= 3 else (-8 if feel >= 4 else 0)
            if rpe is not None and rpe >= 8:
                score -= 8
        elif cat == "recovery":
            score = 70
            if intf is not None and intf > 0.70:
                score -= 12
            if rpe is not None and rpe > 5:
                score -= 10

        score = clamp_local(score)
        verdict = "GOOD" if score >= 75 else ("OK" if score >= 60 else "POOR")
        bucket = category_scores.setdefault(cat, {"count": 0, "sum": 0, "good": 0, "poor": 0})
        bucket["count"] += 1
        bucket["sum"] += score
        if verdict == "GOOD":
            bucket["good"] += 1
        elif verdict == "POOR":
            bucket["poor"] += 1

        recent_sessions.append({
            "date": safe_date_str(a),
            "name": a.get("name", "?"),
            "category": cat,
            "score": score,
            "verdict": verdict,
        })

    alerts = []
    normalized_scores = {}
    for cat, data in category_scores.items():
        avg_score = round(data["sum"] / data["count"], 1)
        normalized_scores[cat] = {
            "count": data["count"],
            "avg_score": avg_score,
            "good": data["good"],
            "poor": data["poor"],
        }
        if cat in _KEY_SESSION_CATEGORIES and data["count"] >= 2 and avg_score < 65:
            alerts.append(f"{_SESSION_CATEGORY_LABELS.get(cat, cat)}: low session quality ({avg_score}/100).")
        if cat in {"threshold", "vo2"} and data["count"] == 0:
            alerts.append(f"{_SESSION_CATEGORY_LABELS.get(cat, cat)}: no clear sessions last {days} days.")

    key_scores = [
        v["avg_score"] for k, v in normalized_scores.items()
        if k in _KEY_SESSION_CATEGORIES and v["count"] > 0
    ]
    overall_score = round(sum(key_scores) / len(key_scores), 1) if key_scores else None
    recent_lines = [
        f"  {s['date']} | {_SESSION_CATEGORY_LABELS.get(s['category'], s['category'])}: {s['score']}/100 [{s['verdict']}] | {s['name']}"
        for s in recent_sessions[-5:]
    ]
    summary = (
        f"Session quality last {days}d: {overall_score}/100."
        if overall_score is not None else
        f"Session quality last {days}d: insufficient data for key sessions."
    )
    if alerts:
        summary += " " + " ".join(alerts[:2])

    return {
        "days": days,
        "overall_score": overall_score,
        "category_scores": normalized_scores,
        "priority_alerts": alerts,
        "recent_sessions": recent_lines,
        "summary": summary,
    }


def race_demands_analysis(races: list, activities: list) -> dict:
    today = date.today()
    future = sorted([
        r for r in races
        if r.get("start_date_local", "")[:10]
        and datetime.strptime(r["start_date_local"][:10], "%Y-%m-%d").date() >= today
    ], key=lambda r: r.get("start_date_local", ""))

    target = future[0] if future else None
    target_name = target.get("name", "Main race") if target else "Main race"
    target_date = target.get("start_date_local", "")[:10] if target else ""
    days_to_race = (datetime.strptime(target_date, "%Y-%m-%d").date() - today).days if target_date else None

    cycling = [a for a in activities if a.get("type") in ("Ride", "VirtualRide")]
    cutoff_56 = (today - timedelta(days=56)).isoformat()
    cutoff_21 = (today - timedelta(days=21)).isoformat()
    recent_cycling = [a for a in cycling if safe_date_str(a) and safe_date_str(a) >= cutoff_56]
    recent_21 = [a for a in cycling if safe_date_str(a) and safe_date_str(a) >= cutoff_21]

    longest_ride = max((session_duration_min(a) for a in recent_cycling), default=0)
    rides_3h = sum(1 for a in recent_cycling if session_duration_min(a) >= 180)
    rides_4h = sum(1 for a in recent_cycling if session_duration_min(a) >= 240)
    rides_5h = sum(1 for a in recent_cycling if session_duration_min(a) >= 300)
    threshold_21d = sum(1 for a in recent_21 if classify_session_category(a) == "threshold")
    vo2_21d = sum(1 for a in recent_21 if classify_session_category(a) == "vo2")
    fueling_sims = sum(1 for a in recent_cycling if session_duration_min(a) >= 180)

    demands = [
        "Aerobic durability for 4-6h cycling at an even pace.",
        "Nutrition tolerance: 80-100g CHO/h on long rides.",
        "Pacing: avoid riding long rides too hard early on.",
        "Riding position and muscular durability over many hours.",
    ]
    markers = [
        f"Longest ride last 8w: {round(longest_ride/60, 1) if longest_ride else 0}h",
        f"Number of rides >=3h: {rides_3h}",
        f"Number of rides >=4h: {rides_4h}",
        f"Threshold sessions last 21d: {threshold_21d}",
        f"VO2 sessions last 21d: {vo2_21d}",
        f"Long fueling repetitions (>=3h): {fueling_sims}",
    ]
    gaps = []
    if longest_ride < 240:
        gaps.append("Durability gap: longest ride is under 4h.")
    if rides_4h < 2 and (days_to_race is None or days_to_race > 28):
        gaps.append("Specific endurance gap: too few rides over 4h.")
    if fueling_sims < 2 and (days_to_race is None or days_to_race > 21):
        gaps.append("Fueling gap: too few long nutrition repetitions.")
    if threshold_21d < 1 and (days_to_race is None or days_to_race > 21):
        gaps.append("Threshold gap: too little work around sustainable power last 3 weeks.")
    if vo2_21d < 1 and (days_to_race is None or days_to_race > 35):
        gaps.append("VO2 gap: no clear high-quality oxygen stimuli last 3 weeks.")

    must_have = []
    if any("Durability gap" in g for g in gaps):
        must_have.append("1 long Z2 ride progressively building towards 4-6h.")
    if any("Fueling gap" in g for g in gaps):
        must_have.append("1 long nutrition repetition with clear CHO goal.")
    if any("Threshold gap" in g for g in gaps):
        must_have.append("1 threshold session for sustainable power/economy.")
    if any("VO2 gap" in g for g in gaps):
        must_have.append("1 short VO2 stimuli if recovery allows.")

    summary = (
        f"Race demands ({target_name}{' ' + target_date if target_date else ''}): "
        f"longest ride {round(longest_ride/60,1) if longest_ride else 0}h | >=4h rides {rides_4h} | "
        f"threshold {threshold_21d}/21d | VO2 {vo2_21d}/21d. "
        + ("Gaps: " + " ".join(gaps[:3]) if gaps else "Current profile covers main demands fairly well.")
    )
    return {
        "target_name": target_name,
        "target_date": target_date,
        "days_to_race": days_to_race,
        "demands": demands,
        "markers": markers,
        "gaps": gaps,
        "must_have_sessions": must_have,
        "summary": summary,
    }


def coach_confidence_analysis(data_quality: dict, activities: list, wellness: list, fitness: list, hrv: dict) -> dict:
    score = 100
    reasons = []

    if len(activities) < 10:
        score -= 20
        reasons.append(f"few activities in history ({len(activities)})")
    if len(wellness) < 7:
        score -= 15
        reasons.append(f"limited wellness data ({len(wellness)} days)")
    if len(fitness) < 14:
        score -= 10
        reasons.append(f"short fitness history ({len(fitness)} days)")
    if hrv.get("state") == "INSUFFICIENT_DATA":
        score -= 10
        reasons.append("insufficient HRV data")
    warnings = len((data_quality or {}).get("warnings", []))
    if warnings >= 5:
        score -= 20
        reasons.append(f"many data quality warnings ({warnings})")
    elif warnings >= 2:
        score -= 10
        reasons.append(f"some data quality uncertainty ({warnings})")
    
    log.info(f"🔍 History check: {len(activities)} activities approved, {warnings} warnings found.")


    if score >= 85:
        level = "HIGH"
        advice = "Data looks robust - the coach can be offensive within safe boundaries."
    elif score >= 65:
        level = "MEDIUM"
        advice = "Sufficient data quality - good for coaching but some decisions should be pragmatic."
    else:
        level = "LOW"
        advice = "Uncertain data foundation - prioritize simplicity, feasibility, and clear key sessions."

    return {
        "score": score,
        "level": level,
        "reasons": reasons,
        "advice": advice,
        "summary": f"Coach confidence: {level} ({score}/100). {advice}"
                   + (f" Reasons: {', '.join(reasons)}." if reasons else ""),
    }


def _weeks_since_deload(weekly_tss: list) -> int:
    if len(weekly_tss) < 2:
        return 0
    avg = sum(w["tss"] for w in weekly_tss) / len(weekly_tss) if weekly_tss else 1
    if avg == 0:
        return 0
    for i in range(len(weekly_tss) - 1, -1, -1):
        if weekly_tss[i]["tss"] < avg * 0.65:
            return len(weekly_tss) - 1 - i
    return len(weekly_tss)


# ══════════════════════════════════════════════════════════════════════════════
# 2. CTL TRAJECTORY
# ══════════════════════════════════════════════════════════════════════════════

def ctl_trajectory(ctl_now: float, race_date: Optional[date], target_ctl: float,
                   taper_days: int = 14) -> dict:
    if race_date is None:
        return {
            "has_target": False,
            "message": "No A-race scheduled. Doing general build.",
            "required_weekly_tss": None,
            "ctl_gap": None,
        }
    today = date.today()
    days_to_race = (race_date - today).days
    if days_to_race <= 0:
        return {"has_target": False, "message": "The race has passed.", "required_weekly_tss": None, "ctl_gap": None}
    build_days = max(days_to_race - taper_days, 1)
    pre_taper_target = target_ctl + 4
    decay = 41 / 42
    decay_n = decay ** build_days
    if (1 - decay_n) == 0:
        required_daily = ctl_now
    else:
        required_daily = (pre_taper_target - ctl_now * decay_n) / (1 - decay_n)
    required_weekly = round(required_daily * 7)
    ctl_gap = round(target_ctl - ctl_now, 1)
    max_reasonable_daily = ctl_now * 1.5
    is_achievable = required_daily <= max_reasonable_daily
    milestones = []
    for weeks_ahead in [2, 4, 6, 8]:
        d = weeks_ahead * 7
        if d < build_days:
            projected = required_daily + (ctl_now - required_daily) * (decay ** d)
            milestones.append({"weeks": weeks_ahead, "projected_ctl": round(projected, 1)})
    ramp_per_week = round((pre_taper_target - ctl_now) / max(build_days / 7, 1), 1)
    return {
        "has_target":          True,
        "race_date":           race_date.isoformat(),
        "days_to_race":        days_to_race,
        "ctl_now":             round(ctl_now, 1),
        "target_ctl":          target_ctl,
        "ctl_gap":             ctl_gap,
        "required_weekly_tss": required_weekly,
        "required_daily_tss":  round(required_daily),
        "ramp_per_week":       ramp_per_week,
        "is_achievable":       is_achievable,
        "milestones":          milestones,
        "build_days":          build_days,
        "taper_start":         (race_date - timedelta(days=taper_days)).isoformat(),
        "message": (
            f"Goal: CTL {target_ctl} by {race_date.isoformat()} ({days_to_race}d left). "
            f"Now: CTL {round(ctl_now)}. Gap: {ctl_gap}. "
            f"Requires ~{required_weekly} TSS/week ({round(required_daily)} TSS/day). "
            f"Ramp: +{ramp_per_week} CTL/week. "
            + ("✅ Achievable." if is_achievable else "⚠️ Aggressive ramp – consider lowering target CTL.")
        ),
    }


def ctl_ontrack_check(trajectory: dict, ctl_now: float, fitness_history: list) -> str:
    """Ger en enkel status om atleten är på rätt spår mot CTL-målet för A-race."""
    if not trajectory.get("has_target"):
        return ""
    gap = trajectory["ctl_gap"]
    ramp = trajectory["ramp_per_week"]
    # Kolla om senaste 2 veckors CTL faktiskt stiger tillräckligt snabbt
    if len(fitness_history) >= 14:
        ctl_2w_ago = fitness_history[-14].get("ctl", ctl_now)
        actual_ramp = round((ctl_now - ctl_2w_ago) / 2, 1)
        ramp_status = f" (actual ramp: +{actual_ramp} CTL/w, needed: +{ramp})"
    else:
        ramp_status = ""
    if gap <= 2:
        return f"✅ ON TRACK – CTL within {gap} points of target{ramp_status}"
    elif gap <= 8:
        return f"🟡 SLIGHTLY BEHIND – {gap} CTL points left, needs +{ramp} CTL/week{ramp_status}"
    else:
        return f"🔴 BEHIND SCHEDULE – {gap} CTL points left, increase weekly volume now{ramp_status}"


# ══════════════════════════════════════════════════════════════════════════════
# 3. COMPLIANCE ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════


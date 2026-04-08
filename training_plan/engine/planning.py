from training_plan.core.common import *
from training_plan.engine.libraries import *
from training_plan.engine.utils import safe_date_str

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {}

def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False))


_FAILURE_MEMORY_LIMIT = 8
_FAILURE_MEMORY_MIN_SCORE = 0.5
_FAILURE_MEMORY_DECAY = 0.85
_FAILURE_CATEGORIES = {
    "complexity_overload": "Plans became too complex and likely hurt execution.",
    "lost_key_sessions": "Important key sessions were weakened, misplaced, or not protected enough.",
    "low_specificity": "The plan drifted away from block objective or race demands.",
    "high_risk_load": "Load/risk balance was too aggressive or physiologically unsafe.",
    "repeated_veto": "Python had to veto the plan repeatedly, meaning the structure was not legal from the start.",
    "low_tss": "Planned load ended up too low relative to budget and objective.",
    "filler_sessions": "The plan contained low-value filler instead of clear purpose.",
    "weak_individualization": "The plan did not adapt well enough to this athlete's profile or constraints.",
    "uncertainty_high": "The plan relied too much on weak or uncertain data.",
}


def _failure_memory_bucket(state: dict) -> dict:
    bucket = state.setdefault("failure_memory", {})
    bucket.setdefault("patterns", {})
    return bucket


def _categorize_failure_signals(trace, changes: list[str]) -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []
    if not trace or not trace.review or not trace.scores:
        return results

    review = trace.review
    scores = trace.scores
    must_fix_text = " ".join(review.must_fix or []).lower()
    advice_text = " ".join(review.coaching_advice or []).lower()
    summary_text = f"{review.summary} {trace.rationale}".lower()
    change_text = " ".join(changes or []).lower()

    if scores.simplicity <= 5 or "filler" in must_fix_text or "filler" in advice_text:
        results.append(("complexity_overload", "Simplicity was weak or filler sessions appeared."))
    if review.key_sessions.rating in ("WEAK", "CRITICAL") or "must-hit" in must_fix_text or "key session" in must_fix_text:
        results.append(("lost_key_sessions", "Key sessions were not protected strongly enough."))
    if scores.specificity <= 5 or review.race_demands.rating in ("WEAK", "CRITICAL"):
        results.append(("low_specificity", "Specificity to block/race demands was too weak."))
    if scores.risk >= 7 or review.load_and_risk.rating in ("WEAK", "CRITICAL"):
        results.append(("high_risk_load", "Risk/load balance was too aggressive or unstable."))
    if any("veto" in c.lower() for c in changes):
        results.append(("repeated_veto", "Python had to veto parts of the plan."))
    if "tss-deficit veto" in change_text or "too low" in must_fix_text or "missing" in must_fix_text and "tss" in must_fix_text:
        results.append(("low_tss", "The plan under-shot the intended load budget."))
    if "filler" in must_fix_text or "filler" in summary_text:
        results.append(("filler_sessions", "The plan included low-value filler sessions."))
    if scores.simplicity <= 5 or review.individualization.rating in ("WEAK", "CRITICAL"):
        results.append(("weak_individualization", "The plan was not individualized enough."))
    if scores.confidence <= 4 or len(review.uncertainty_sources or []) >= 3:
        results.append(("uncertainty_high", "The plan depended on too much uncertainty."))

    deduped = []
    seen = set()
    for key, reason in results:
        if key in seen:
            continue
        seen.add(key)
        deduped.append((key, reason))
    return deduped


def update_failure_memory(state: dict, trace, changes: list[str]) -> dict:
    bucket = _failure_memory_bucket(state)
    patterns = bucket.setdefault("patterns", {})
    today = date.today()
    last_updated = bucket.get("last_updated")
    if last_updated:
        try:
            days_since = max((today - date.fromisoformat(last_updated)).days, 0)
        except Exception:
            days_since = 0
        if days_since > 0:
            decay_factor = _FAILURE_MEMORY_DECAY ** days_since
            for item in patterns.values():
                item["score"] = round(item.get("score", 0.0) * decay_factor, 3)

    for category, reason in _categorize_failure_signals(trace, changes):
        item = patterns.setdefault(category, {
            "score": 0.0,
            "count": 0,
            "last_seen": today.isoformat(),
            "example": reason,
            "label": _FAILURE_CATEGORIES.get(category, category),
        })
        item["score"] = round(item.get("score", 0.0) + 1.0, 3)
        item["count"] = item.get("count", 0) + 1
        item["last_seen"] = today.isoformat()
        item["example"] = reason
        item["label"] = _FAILURE_CATEGORIES.get(category, category)

    filtered = {
        key: value for key, value in patterns.items()
        if value.get("score", 0.0) >= _FAILURE_MEMORY_MIN_SCORE
    }
    ranked = sorted(
        filtered.items(),
        key=lambda kv: (-kv[1].get("score", 0.0), -kv[1].get("count", 0), kv[0]),
    )[:_FAILURE_MEMORY_LIMIT]
    bucket["patterns"] = {key: value for key, value in ranked}
    bucket["last_updated"] = today.isoformat()
    return bucket


def format_failure_memory(memory: dict) -> str:
    if not memory:
        return ""
    patterns = memory.get("patterns", {})
    if not patterns:
        return ""
    ranked = sorted(
        patterns.items(),
        key=lambda kv: (-kv[1].get("score", 0.0), -kv[1].get("count", 0), kv[0]),
    )[:3]
    if not ranked:
        return ""
    lines = ["FAILURE MEMORY (recent recurring planning mistakes to avoid):"]
    for key, item in ranked:
        if item.get("count", 0) < 2 and item.get("score", 0.0) < 1.5:
            continue
        lines.append(
            f"  - Avoid {key}: {item.get('label', key)} "
            f"(score {round(item.get('score', 0.0), 1)}, seen {item.get('count', 0)}x). "
            f"Recent example: {item.get('example', '')}"
        )
    return "\n".join(lines) if len(lines) > 1 else ""

def is_ai_generated(w):
    return AI_TAG in (w.get("description") or "")

# ══════════════════════════════════════════════════════════════════════════════
# 1 & 5. MESOCYCLE PERIODIZATION + AUTO DELOAD
# ══════════════════════════════════════════════════════════════════════════════

def determine_mesocycle(fitness_history: list, activities: list, state: dict) -> dict:
    today = date.today()
    weekly_tss = _weekly_tss_history(activities, weeks=6)
    weeks_since_deload = _weeks_since_deload(weekly_tss)
    saved_block    = state.get("mesocycle_block", 1)
    saved_week     = state.get("mesocycle_week", 1)
    saved_date     = state.get("mesocycle_last_update", "")
    if saved_date and saved_date >= (today - timedelta(days=1)).isoformat():
        week_in_block = saved_week
        block_number  = saved_block
    else:
        if today.weekday() == 0:
            week_in_block = (saved_week % 4) + 1
            block_number  = saved_block + (1 if saved_week == 4 else 0)
        else:
            week_in_block = saved_week
            block_number  = saved_block
    deload_reason = ""
    forced_deload = False
    if weeks_since_deload >= 4 and week_in_block != 4:
        forced_deload = True
        deload_reason = f"FORCED DELOAD: {weeks_since_deload} weeks without rest. The body needs recovery."
        week_in_block = 4
    is_deload = (week_in_block == 4)
    if is_deload:
        load_factor = 0.60
        if not deload_reason:
            deload_reason = "Planned deload week (week 4 of 4). Reduced volume and intensity."
    else:
        load_factor = 1.0 + (week_in_block - 1) * 0.05
    state["mesocycle_block"]       = block_number
    state["mesocycle_week"]        = week_in_block
    state["mesocycle_last_update"] = today.isoformat()
    return {
        "week_in_block":      week_in_block,
        "is_deload":          is_deload,
        "block_number":       block_number,
        "load_factor":        round(load_factor, 2),
        "weeks_since_deload": weeks_since_deload,
        "deload_reason":      deload_reason,
        "forced_deload":      forced_deload,
    }


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
        level = "LOW"
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

def compliance_analysis(planned_events: list, activities: list, days: int = 28) -> dict:
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    planned = [
        e for e in planned_events
        if e.get("category") == "WORKOUT"
        and is_ai_generated(e)          # räkna bara AI-planerade pass, inte manuella/externa
        and e.get("start_date_local", "")[:10] >= cutoff
        and e.get("start_date_local", "")[:10] < date.today().isoformat()
    ]
    plan_by_date = {}
    for p in planned:
        d = p.get("start_date_local", "")[:10]
        plan_by_date.setdefault(d, []).append(p)
    act_by_date = {}
    for a in activities:
        d = safe_date_str(a)
        if d and d >= cutoff:
            act_by_date.setdefault(d, []).append(a)
    total_planned   = len(planned)
    total_completed = 0
    missed_by_type  = {}
    completed_by_type = {}
    intensity_planned  = 0
    intensity_missed   = 0
    weighted_total = 0.0
    weighted_done = 0.0
    key_total = 0
    key_done = 0
    weights = {
        "ftp_test": 2.0, "long_ride": 2.0, "threshold": 2.0, "vo2": 2.0,
        "endurance": 1.0, "strength": 1.0, "recovery": 0.5, "general": 1.0,
    }
    for d, plans in plan_by_date.items():
        actuals = act_by_date.get(d, [])
        actual_types = {a.get("type", "") for a in actuals}
        for p in plans:
            p_type = p.get("type", "")
            p_name = (p.get("name", "") or "").lower()
            p_cat = classify_session_category(p)
            weight = weights.get(p_cat, 1.0)
            matched = p_type in actual_types or len(actuals) > 0
            weighted_total += weight
            if matched:
                total_completed += 1
                completed_by_type[p_type] = completed_by_type.get(p_type, 0) + 1
                weighted_done += weight
            else:
                missed_by_type[p_type] = missed_by_type.get(p_type, 0) + 1
            if p_cat in _KEY_SESSION_CATEGORIES:
                key_total += 1
                if matched:
                    key_done += 1
            is_intensity = any(kw in p_name for kw in ["intervall", "z4", "z5", "tempo", "fartlek", "vo2"])
            if is_intensity:
                intensity_planned += 1
                if not matched:
                    intensity_missed += 1
    completion_rate = round(total_completed / total_planned * 100) if total_planned > 0 else 100
    weighted_completion_rate = round(weighted_done / weighted_total * 100) if weighted_total > 0 else 100
    key_completion_rate = round(key_done / key_total * 100) if key_total > 0 else 100
    patterns = []
    if completion_rate < 70:
        patterns.append(f"⚠️ Low compliance ({completion_rate}%) – athlete skips too many sessions.")
    elif completion_rate < 85:
        patterns.append(f"Medium compliance ({completion_rate}%) – room for improvement.")
    if weighted_completion_rate < completion_rate - 10:
        patterns.append(
            f"⚠️ Key sessions missed more often than total ({weighted_completion_rate}% weighted compliance)."
        )
    if intensity_planned > 0 and intensity_missed / intensity_planned > 0.4:
        patterns.append(
            f"⚠️ Athlete often skips intensity sessions "
            f"({intensity_missed}/{intensity_planned} missed). "
            f"Consider shorter/more fun intervals."
        )
    for sport, count in missed_by_type.items():
        sport_total = count + completed_by_type.get(sport, 0)
        if sport_total > 0 and count / sport_total > 0.5:
            patterns.append(
                f"Athlete avoids {sport} ({count}/{sport_total} missed). "
                f"Switch to alternative sport or lower volume."
            )
    return {
        "period_days":          days,
        "total_planned":        total_planned,
        "total_completed":      total_completed,
        "completion_rate":      completion_rate,
        "missed_by_type":       missed_by_type,
        "completed_by_type":    completed_by_type,
        "intensity_planned":    intensity_planned,
        "intensity_missed":     intensity_missed,
        "weighted_completion_rate": weighted_completion_rate,
        "key_completion_rate": key_completion_rate,
        "patterns":             patterns,
        "summary": (
            f"Compliance last {days}d: {total_completed}/{total_planned} sessions completed "
            f"({completion_rate}%). Weighted compliance: {weighted_completion_rate}%. Key sessions: {key_completion_rate}%. "
            + (f"Missed intensity sessions: {intensity_missed}/{intensity_planned}. " if intensity_planned > 0 else "")
            + " ".join(patterns)
        ),
    }


def update_learned_patterns(state: dict, planned_events: list, activities: list) -> dict:
    """Uppdaterar lärda mönster i state-filen: sport×veckodag, hög-RPE-typer, AM/PM."""
    patterns = state.get("learned_patterns", {
        "skip_by_sport_dow": {}, "high_rpe_by_type": {}, "time_of_day": {}
    })
    cutoff = (date.today() - timedelta(days=90)).isoformat()
    act_by_date: dict = {}
    for a in activities:
        d = a.get("start_date_local", "")[:10]
        if d >= cutoff:
            act_by_date.setdefault(d, []).append(a)

    for e in planned_events:
        if not (is_ai_generated(e) or e.get("category") == "WORKOUT"):
            continue
        d = e.get("start_date_local", "")[:10]
        if d < cutoff or d >= date.today().isoformat():
            continue
        sport = e.get("type", "Unknown")
        dow   = str(date.fromisoformat(d).weekday())
        key   = f"{sport}_{dow}"
        sp    = patterns["skip_by_sport_dow"].setdefault(key, {"planned": 0, "skipped": 0})
        sp["planned"] += 1
        completed = bool(act_by_date.get(d))
        if not completed:
            sp["skipped"] += 1

        if completed:
            act = act_by_date[d][0]
            rpe = act.get("perceived_exertion")
            if rpe is not None:
                hr  = patterns["high_rpe_by_type"].setdefault(sport, {"count": 0, "high_rpe_count": 0})
                hr["count"] += 1
                if rpe > 7:
                    hr["high_rpe_count"] += 1
            slot = "AM" if "(AM)" in (e.get("name") or "") else ("PM" if "(PM)" in (e.get("name") or "") else "MAIN")
            tod  = patterns["time_of_day"].setdefault(slot, {"count": 0, "completed": 0})
            tod["count"] += 1
            tod["completed"] += 1
        else:
            slot = "AM" if "(AM)" in (e.get("name") or "") else ("PM" if "(PM)" in (e.get("name") or "") else "MAIN")
            patterns["time_of_day"].setdefault(slot, {"count": 0, "completed": 0})["count"] += 1

    patterns["last_updated"] = date.today().isoformat()
    return patterns


def format_learned_patterns(patterns: dict) -> str:
    """Formaterar lärda mönster för AI-prompten – visar bara signifikanta fynd."""
    if not patterns:
        return ""
    days_en = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    lines = []
    for key, v in patterns.get("skip_by_sport_dow", {}).items():
        if v["planned"] >= 3 and v["skipped"] / v["planned"] > 0.5:
            sport, dow = key.rsplit("_", 1)
            lines.append(f"  Athlete often skips {sport} on {days_en[int(dow)]} ({v['skipped']}/{v['planned']} missed)")
    for sport, v in patterns.get("high_rpe_by_type", {}).items():
        if v["count"] >= 3 and v["high_rpe_count"] / v["count"] > 0.5:
            lines.append(f"  {sport} often results in high RPE ({v['high_rpe_count']}/{v['count']} sessions RPE>7)")
    for slot, v in patterns.get("time_of_day", {}).items():
        if v["count"] >= 5 and slot == "AM" and v["completed"] / v["count"] < 0.70:
            lines.append(f"  AM sessions are rarely completed ({round(v['completed']/v['count']*100)}%) – avoid AM")
    if not lines:
        return ""
    return "LEARNED PATTERNS (history):\n" + "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# 4. WORKOUT LIBRARY WITH PROGRESSION
# ══════════════════════════════════════════════════════════════════════════════

WORKOUT_LIBRARY = {
    "threshold_intervals": {
        "name":  "Threshold intervals (Z4)",
        "sport": ["VirtualRide", "Ride"],
        "phase": ["Base", "Build", "Grundtraning"],
        "levels": [
            {"level": 1, "label": "4×4min Z4 / 3min rest",   "steps": [
                {"d": 15, "z": "Z2", "desc": "Warm-up"},
                {"d": 4, "z": "Z4", "desc": "Interval 1 @ FTP"},
                {"d": 3, "z": "Z1", "desc": "Rest"},
                {"d": 4, "z": "Z4", "desc": "Interval 2"},
                {"d": 3, "z": "Z1", "desc": "Rest"},
                {"d": 4, "z": "Z4", "desc": "Interval 3"},
                {"d": 3, "z": "Z1", "desc": "Rest"},
                {"d": 4, "z": "Z4", "desc": "Interval 4"},
                {"d": 10, "z": "Z1", "desc": "Cool-down"},
            ], "total_min": 50},
            {"level": 2, "label": "4×5min Z4 / 3min rest",   "steps": [
                {"d": 15, "z": "Z2", "desc": "Warm-up"},
                {"d": 5, "z": "Z4", "desc": "Interval 1 @ FTP"},
                {"d": 3, "z": "Z1", "desc": "Rest"},
                {"d": 5, "z": "Z4", "desc": "Interval 2"},
                {"d": 3, "z": "Z1", "desc": "Rest"},
                {"d": 5, "z": "Z4", "desc": "Interval 3"},
                {"d": 3, "z": "Z1", "desc": "Rest"},
                {"d": 5, "z": "Z4", "desc": "Interval 4"},
                {"d": 10, "z": "Z1", "desc": "Cool-down"},
            ], "total_min": 54},
            {"level": 3, "label": "5×5min Z4 / 2.5min rest", "steps": [
                {"d": 15, "z": "Z2", "desc": "Warm-up"},
                {"d": 5, "z": "Z4", "desc": "Interval 1"},
                {"d": 3, "z": "Z1", "desc": "Rest"},
                {"d": 5, "z": "Z4", "desc": "Interval 2"},
                {"d": 3, "z": "Z1", "desc": "Rest"},
                {"d": 5, "z": "Z4", "desc": "Interval 3"},
                {"d": 3, "z": "Z1", "desc": "Rest"},
                {"d": 5, "z": "Z4", "desc": "Interval 4"},
                {"d": 3, "z": "Z1", "desc": "Rest"},
                {"d": 5, "z": "Z4", "desc": "Interval 5"},
                {"d": 10, "z": "Z1", "desc": "Cool-down"},
            ], "total_min": 62},
            {"level": 4, "label": "4×8min Z4 / 3min rest",   "steps": [
                {"d": 15, "z": "Z2", "desc": "Warm-up"},
                {"d": 8, "z": "Z4", "desc": "Interval 1 - keep even power"},
                {"d": 3, "z": "Z1", "desc": "Rest"},
                {"d": 8, "z": "Z4", "desc": "Interval 2"},
                {"d": 3, "z": "Z1", "desc": "Rest"},
                {"d": 8, "z": "Z4", "desc": "Interval 3"},
                {"d": 3, "z": "Z1", "desc": "Rest"},
                {"d": 8, "z": "Z4", "desc": "Interval 4"},
                {"d": 10, "z": "Z1", "desc": "Cool-down"},
            ], "total_min": 66},
            {"level": 5, "label": "3×12min Z4 / 4min rest",  "steps": [
                {"d": 15, "z": "Z2", "desc": "Warm-up"},
                {"d": 12, "z": "Z4", "desc": "Interval 1 - think race pace"},
                {"d": 4, "z": "Z1", "desc": "Rest"},
                {"d": 12, "z": "Z4", "desc": "Interval 2"},
                {"d": 4, "z": "Z1", "desc": "Rest"},
                {"d": 12, "z": "Z4", "desc": "Interval 3"},
                {"d": 10, "z": "Z1", "desc": "Cool-down"},
            ], "total_min": 69},
        ],
    },
    "vo2max_intervals": {
        "name":  "VO2max intervals (Z5)",
        "sport": ["VirtualRide"],
        "phase": ["Build", "Base", "Grundtraning"],
        "levels": [
            {"level": 1, "label": "5×3min Z5 / 3min rest", "steps": [
                {"d": 15, "z": "Z2", "desc": "Warm-up incl 2x30s hard"},
                {"d": 3, "z": "Z5", "desc": "VO2max 1"}, {"d": 3, "z": "Z1", "desc": "Rest"},
                {"d": 3, "z": "Z5", "desc": "VO2max 2"}, {"d": 3, "z": "Z1", "desc": "Rest"},
                {"d": 3, "z": "Z5", "desc": "VO2max 3"}, {"d": 3, "z": "Z1", "desc": "Rest"},
                {"d": 3, "z": "Z5", "desc": "VO2max 4"}, {"d": 3, "z": "Z1", "desc": "Rest"},
                {"d": 3, "z": "Z5", "desc": "VO2max 5"}, {"d": 10, "z": "Z1", "desc": "Cool-down"},
            ], "total_min": 55},
            {"level": 2, "label": "6×3min Z5 / 3min rest", "steps": [
                {"d": 15, "z": "Z2", "desc": "Warm-up"},
                {"d": 3, "z": "Z5", "desc": "VO2max 1"}, {"d": 3, "z": "Z1", "desc": "Rest"},
                {"d": 3, "z": "Z5", "desc": "VO2max 2"}, {"d": 3, "z": "Z1", "desc": "Rest"},
                {"d": 3, "z": "Z5", "desc": "VO2max 3"}, {"d": 3, "z": "Z1", "desc": "Rest"},
                {"d": 3, "z": "Z5", "desc": "VO2max 4"}, {"d": 3, "z": "Z1", "desc": "Rest"},
                {"d": 3, "z": "Z5", "desc": "VO2max 5"}, {"d": 3, "z": "Z1", "desc": "Rest"},
                {"d": 3, "z": "Z5", "desc": "VO2max 6"}, {"d": 10, "z": "Z1", "desc": "Cool-down"},
            ], "total_min": 61},
            {"level": 3, "label": "5×4min Z5 / 3min rest", "steps": [
                {"d": 15, "z": "Z2", "desc": "Warm-up"},
                {"d": 4, "z": "Z5", "desc": "VO2max 1"}, {"d": 3, "z": "Z1", "desc": "Rest"},
                {"d": 4, "z": "Z5", "desc": "VO2max 2"}, {"d": 3, "z": "Z1", "desc": "Rest"},
                {"d": 4, "z": "Z5", "desc": "VO2max 3"}, {"d": 3, "z": "Z1", "desc": "Rest"},
                {"d": 4, "z": "Z5", "desc": "VO2max 4"}, {"d": 3, "z": "Z1", "desc": "Rest"},
                {"d": 4, "z": "Z5", "desc": "VO2max 5"}, {"d": 10, "z": "Z1", "desc": "Cool-down"},
            ], "total_min": 60},
            {"level": 4, "label": "4×5min Z5 / 4min rest", "steps": [
                {"d": 15, "z": "Z2", "desc": "Warm-up"},
                {"d": 5, "z": "Z5", "desc": "VO2max 1"}, {"d": 4, "z": "Z1", "desc": "Rest"},
                {"d": 5, "z": "Z5", "desc": "VO2max 2"}, {"d": 4, "z": "Z1", "desc": "Rest"},
                {"d": 5, "z": "Z5", "desc": "VO2max 3"}, {"d": 4, "z": "Z1", "desc": "Rest"},
                {"d": 5, "z": "Z5", "desc": "VO2max 4"}, {"d": 10, "z": "Z1", "desc": "Cool-down"},
            ], "total_min": 61},
        ],
    },
    "tempo_sustained": {
        "name":  "Tempo session (Z3)",
        "sport": ["VirtualRide", "Ride"],
        "phase": ["Base", "Build", "Grundtraning"],
        "levels": [
            {"level": 1, "label": "2×15min Z3 / 5min rest", "steps": [
                {"d": 15, "z": "Z2", "desc": "Warm-up"},
                {"d": 15, "z": "Z3", "desc": "Tempo block 1"},
                {"d": 5, "z": "Z1", "desc": "Rest"},
                {"d": 15, "z": "Z3", "desc": "Tempo block 2"},
                {"d": 10, "z": "Z1", "desc": "Cool-down"},
            ], "total_min": 60},
            {"level": 2, "label": "2×20min Z3 / 5min rest", "steps": [
                {"d": 15, "z": "Z2", "desc": "Warm-up"},
                {"d": 20, "z": "Z3", "desc": "Tempo block 1"},
                {"d": 5, "z": "Z1", "desc": "Rest"},
                {"d": 20, "z": "Z3", "desc": "Tempo block 2"},
                {"d": 10, "z": "Z1", "desc": "Cool-down"},
            ], "total_min": 70},
            {"level": 3, "label": "1×40min Z3", "steps": [
                {"d": 15, "z": "Z2", "desc": "Warm-up"},
                {"d": 40, "z": "Z3", "desc": "Tempo - constant pressure"},
                {"d": 10, "z": "Z1", "desc": "Cool-down"},
            ], "total_min": 65},
            {"level": 4, "label": "1×60min Z3 (race sim)", "steps": [
                {"d": 15, "z": "Z2", "desc": "Warm-up"},
                {"d": 60, "z": "Z3", "desc": "Tempo - race simulation"},
                {"d": 10, "z": "Z1", "desc": "Cool-down"},
            ], "total_min": 85},
        ],
    },
    "long_ride_progression": {
        "name":  "Progressive long ride (Event specific)",
        "sport": ["Ride", "VirtualRide"],
        "phase": ["Base", "Build", "Grundtraning"],
        "levels": [
            {"level": 1, "label": "3h Z2 long ride",   "steps": [
                {"d": 180, "z": "Z2", "desc": "Endurance base - even pace"},
            ], "total_min": 180},
            {"level": 2, "label": "3.5h Z2 long ride",  "steps": [
                {"d": 210, "z": "Z2", "desc": "Endurance base - focus on nutrition"},
            ], "total_min": 210},
            {"level": 3, "label": "4h Z2 long ride + tempo", "steps": [
                {"d": 60,  "z": "Z2", "desc": "Warm-up - find the rhythm"},
                {"d": 20,  "z": "Z3", "desc": "Tempo effort in the middle"},
                {"d": 100, "z": "Z2", "desc": "Back to endurance zone"},
                {"d": 60,  "z": "Z2", "desc": "Final block - keep form"},
            ], "total_min": 240},
            {"level": 4, "label": "4.5h simulation", "steps": [
                {"d": 90,  "z": "Z2", "desc": "Block 1 - find race pace"},
                {"d": 10,  "z": "Z3", "desc": "Tempo stomp (simulates hill)"},
                {"d": 80,  "z": "Z2", "desc": "Block 2"},
                {"d": 10,  "z": "Z3", "desc": "Tempo stomp"},
                {"d": 80,  "z": "Z2", "desc": "Block 3 - fatigue simulation"},
            ], "total_min": 270},
            {"level": 5, "label": "5h+ race simulation", "steps": [
                {"d": 120, "z": "Z2", "desc": "Stage 1 - full race nutrition (90g CHO/h)"},
                {"d": 15,  "z": "Z3", "desc": "Tempo - simulates main climb"},
                {"d": 90,  "z": "Z2", "desc": "Stage 2 - mental endurance"},
                {"d": 15,  "z": "Z3", "desc": "Final push - simulates last 30km"},
                {"d": 60,  "z": "Z2", "desc": "Roll out - easy to finish"},
            ], "total_min": 300},
        ],
    },

    # ── Tävlingsförberedelse: Vätternrundan-specifika pass ─────────────────
    "race_simulation": {
        "name":  "Race simulation (Event specific)",
        "sport": ["Ride", "VirtualRide"],
        "phase": ["Build", "Taper"],
        "levels": [
            {"level": 1, "label": "2h Z2 + 30min Z3 + 15min Z4", "steps": [
                {"d": 120, "z": "Z2", "desc": "Race pace - practice nutrition (60g CHO/h)"},
                {"d": 30,  "z": "Z3", "desc": "Tempo increase - simulates hilly section"},
                {"d": 15,  "z": "Z4", "desc": "Race effort - keep even power"},
                {"d": 15,  "z": "Z1", "desc": "Cool-down"},
            ], "total_min": 180},
            {"level": 2, "label": "3h Z2 + 45min Z3 + 20min Z4", "steps": [
                {"d": 180, "z": "Z2", "desc": "Long base - focus on pacing and nutrition"},
                {"d": 45,  "z": "Z3", "desc": "Tempo block - simulates hills"},
                {"d": 20,  "z": "Z4", "desc": "Final effort - finish strong"},
                {"d": 15,  "z": "Z1", "desc": "Cool-down"},
            ], "total_min": 260},
            {"level": 3, "label": "4h Z2 + 60min Z3 + 20min Z4", "steps": [
                {"d": 240, "z": "Z2", "desc": "Full race base - 90g CHO/h, test all race day nutrition"},
                {"d": 60,  "z": "Z3", "desc": "Fatigue simulation - hills after 4h"},
                {"d": 20,  "z": "Z4", "desc": "Final kick - simulate main climb effort"},
                {"d": 20,  "z": "Z1", "desc": "Cool-down"},
            ], "total_min": 340},
        ],
    },
    "climb_simulation": {
        "name":  "Climb simulation (hill specific Z4)",
        "sport": ["VirtualRide", "Ride"],
        "phase": ["Build", "Taper"],
        "levels": [
            {"level": 1, "label": "4×8min Z4 Climb simulation", "steps": [
                {"d": 20, "z": "Z2", "desc": "Warm-up"},
                {"d": 8,  "z": "Z4", "desc": "Climb interval 1 - 5% incline feel, even power"},
                {"d": 4,  "z": "Z1", "desc": "Rest"},
                {"d": 8,  "z": "Z4", "desc": "Climb interval 2"},
                {"d": 4,  "z": "Z1", "desc": "Rest"},
                {"d": 8,  "z": "Z4", "desc": "Climb interval 3"},
                {"d": 4,  "z": "Z1", "desc": "Rest"},
                {"d": 8,  "z": "Z4", "desc": "Climb interval 4 - finish strong"},
                {"d": 15, "z": "Z1", "desc": "Cool-down"},
            ], "total_min": 79},
            {"level": 2, "label": "5×10min Z4 Climb simulation", "steps": [
                {"d": 20, "z": "Z2", "desc": "Warm-up"},
                {"d": 10, "z": "Z4", "desc": "Interval 1"},
                {"d": 4,  "z": "Z1", "desc": "Rest"},
                {"d": 10, "z": "Z4", "desc": "Interval 2"},
                {"d": 4,  "z": "Z1", "desc": "Rest"},
                {"d": 10, "z": "Z4", "desc": "Interval 3"},
                {"d": 4,  "z": "Z1", "desc": "Rest"},
                {"d": 10, "z": "Z4", "desc": "Interval 4"},
                {"d": 4,  "z": "Z1", "desc": "Rest"},
                {"d": 10, "z": "Z4", "desc": "Interval 5 - simulate top of climb"},
                {"d": 15, "z": "Z1", "desc": "Cool-down"},
            ], "total_min": 101},
        ],
    },
    "pacing_practice": {
        "name":  "Pacing practice - negative split",
        "sport": ["Ride", "VirtualRide"],
        "phase": ["Build", "Taper"],
        "levels": [
            {"level": 1, "label": "2h negative split (Z2 → Z3)", "steps": [
                {"d": 60, "z": "Z2", "desc": "First hour - hold back, save energy"},
                {"d": 50, "z": "Z3", "desc": "Second hour - increase gradually to tempo pace"},
                {"d": 10, "z": "Z1", "desc": "Cool-down"},
            ], "total_min": 120},
            {"level": 2, "label": "3h negative split (Z2 → Z3 → Z4)", "steps": [
                {"d": 90, "z": "Z2", "desc": "Endurance base - keep power low"},
                {"d": 60, "z": "Z3", "desc": "Tempo build - increase gradually"},
                {"d": 20, "z": "Z4", "desc": "Final push - simulates the finale"},
                {"d": 10, "z": "Z1", "desc": "Cool-down"},
            ], "total_min": 180},
        ],
    },
}


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


def check_and_advance_workout_progression(yesterday_planned: Optional[dict], yesterday_actuals: list, state: dict):
    """
    Kollar om gårdagens pass var ett lyckat bibliotekspass och avancerar i så fall nivån.
    Ett pass är "lyckat" om det genomfördes med RPE <= 7 och låg/bra känsla (feel <= 3).
    """
    if not yesterday_planned or not yesterday_actuals or not is_ai_generated(yesterday_planned):
        return

    actual = yesterday_actuals[0]
    planned_name = (yesterday_planned.get("name") or "").lower()
    planned_dur = round((yesterday_planned.get("moving_time", 0) or 0) / 60)

    # Försök hitta vilken pass-nyckel från biblioteket som användes
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

    if not wk_key:
        return # Inget bibliotekspass hittades

    rpe = actual.get("perceived_exertion")
    feel = actual.get("feel")

    is_mastered = (rpe is None and feel is None) or (rpe is not None and rpe <= 7 and feel is not None and feel <= 3)

    if is_mastered:
        log.info(f"✅ Session '{wk_key}' mastered (RPE: {rpe or 'N/A'}, Feel: {feel or 'N/A'}).")
        advance_workout_level(wk_key, state) # Denna funktion sparar state
    elif rpe is not None or feel is not None:
        log.info(f"🟡 Session '{wk_key}' completed but not mastered (RPE: {rpe}, Feel: {feel}). Not advancing.")


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

    if rpe is not None and feel is not None and rpe <= 5 and feel <= 2 and wk_key:
        levels = state.get("workout_levels", {})
        current = levels.get(wk_key, 1)
        max_level = len(WORKOUT_LIBRARY.get(wk_key, {}).get("levels", []))
        steps = min(2, max_level - current)  # avancera max 2 steg, max till sista nivå
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

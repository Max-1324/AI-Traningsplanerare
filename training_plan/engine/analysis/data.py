from training_plan.core.common import *
from training_plan.engine.libraries import *
from training_plan.engine.planning import *
from training_plan.engine.utils import safe_date_str, safe_date

def validate_data_quality(activities: list, wellness: list) -> dict:
    """Identifies and filters out data points that are likely measurement errors."""
    warnings: list = []
    filtered_activity_ids: set = set()
    bad_wellness_dates: set = set()

    for a in activities:
        aid = a.get("id") or a.get("start_date_local", "")
        tss = a.get("icu_training_load") or 0
        dur = (a.get("moving_time") or a.get("elapsed_time") or 0) / 60
        intf = session_intensity(a) or 0.0
        name_lower = (a.get("name") or "").lower()
        is_race = "race" in name_lower or a.get("workout_type") == "race"
        if intf > 1.8 and not is_race:
            warnings.append(f"High IF {intf:.2f} on {safe_date_str(a)} – likely incorrect FTP, filtered from analysis")
            filtered_activity_ids.add(aid)
        elif tss > 600:
            warnings.append(f"Unreasonable TSS {tss} on {safe_date_str(a)} – filtered")
            filtered_activity_ids.add(aid)
        elif 0 < dur < 5 and tss > 10:
            warnings.append(f"Short activity ({dur:.0f}min) with TSS {tss} on {safe_date_str(a)} – filtered")
            filtered_activity_ids.add(aid)

    for w in wellness:
        d = w.get("id", "")[:10]
        hrv = w.get("hrv") or 0
        sleep = w.get("sleepSecs") or 0
        if hrv == 0:
            bad_wellness_dates.add(d)
            warnings.append(f"HRV not logged {d} – excluded from HRV analysis")
        elif hrv > 200:
            bad_wellness_dates.add(d)
            warnings.append(f"Unreasonable HRV {hrv}ms {d} – likely measurement error, filtered")
        if 0 < sleep < 7200:
            warnings.append(f"Very short sleep {sleep/3600:.1f}h {d} – check watch settings")
        elif sleep > 57600:
            bad_wellness_dates.add(d)
            warnings.append(f"Unreasonable sleep {sleep/3600:.1f}h {d} – likely watch reset, filtered")

    if warnings:
        for w in warnings:
            log.info(f"⚠️ Data quality: {w}")

    return {
        "warnings": warnings,
        "filtered_activity_ids": filtered_activity_ids,
        "bad_wellness_dates": bad_wellness_dates,
        "has_issues": bool(warnings),
    }

# ══════════════════════════════════════════════════════════════════════════════
# MOTIVATION ANALYSIS & PSYCHOLOGICAL COACHING
# ══════════════════════════════════════════════════════════════════════════════

def analyze_motivation(wellness: list, activities: list) -> dict:
    """Analyzes 14-day feel trend to early identify burnout risk."""
    cutoff = (date.today() - timedelta(days=14)).isoformat()
    week2_cutoff = (date.today() - timedelta(days=7)).isoformat()

    recent_acts = [a for a in activities if safe_date_str(a) >= cutoff and a.get("feel") is not None]
    feel_vals = [a["feel"] for a in recent_acts]
    avg_feel = sum(feel_vals) / len(feel_vals) if feel_vals else 3.0

    w1_feels = [a["feel"] for a in recent_acts if cutoff <= safe_date_str(a) < week2_cutoff]
    w2_feels = [a["feel"] for a in recent_acts if safe_date_str(a) >= week2_cutoff]
    avg_w1 = sum(w1_feels) / len(w1_feels) if w1_feels else avg_feel
    avg_w2 = sum(w2_feels) / len(w2_feels) if w2_feels else avg_feel

    # Intervals feel scale is interpreted as 1=strong/better ... 5=weak/worse.
    # Lower values are therefore better, so trend direction is inverted.
    delta = avg_w2 - avg_w1
    if delta < -0.3:
        trend = "IMPROVING"
    elif delta > 0.3:
        trend = "DECLINING"
    else:
        trend = "STABLE"

    # Count weeks with declining feel (compare with even older data)
    weeks_declining = 0
    if trend == "DECLINING":
        weeks_declining = 1
        older_cutoff = (date.today() - timedelta(days=28)).isoformat()
        older_acts = [a for a in activities if older_cutoff <= safe_date_str(a) < cutoff and a.get("feel") is not None]
        avg_older = sum(a["feel"] for a in older_acts) / len(older_acts) if older_acts else avg_feel
        if avg_w1 > avg_older + 0.3:
            weeks_declining = 2

    if avg_feel > 4.0 and weeks_declining >= 2:
        state = "BURNOUT_RISK"
    elif avg_feel >= 3.5:
        state = "FATIGUED"
    elif avg_feel <= 2.0 and trend in ("IMPROVING", "STABLE"):
        state = "MOTIVATED"
    else:
        state = "NEUTRAL"

    return {
        "state": state,
        "trend": trend,
        "avg_feel": round(avg_feel, 2),
        "weeks_declining": weeks_declining,
        "n_activities": len(feel_vals),
        "summary": f"Motivation: {state} | Trend: {trend} | Avg feel: {avg_feel:.1f}/5 ({len(feel_vals)} sessions last 14d)",
    }

def _wellness_sort_key(item: dict) -> str:
    return (
        str(item.get("id") or "")
        or str(item.get("date") or "")
        or str(item.get("start_date_local") or "")
    )


def _sorted_wellness(wellness: list) -> list:
    return sorted(wellness or [], key=_wellness_sort_key)


def _sorted_activities(activities: list) -> list:
    return sorted(activities or [], key=lambda item: safe_date(item) or datetime.min)


def calculate_hrv(wellness):
    wellness = _sorted_wellness(wellness)
    vals = [w.get("hrv") for w in wellness if (w.get("hrv") or 0) > 0]
    if len(vals) < 7:
        return {"today": None, "avg7d": None, "avg60d": None, "cv7d": None,
                "state": "INSUFFICIENT_DATA", "trend": "UNKNOWN", "stability": "UNKNOWN", "deviation_pct": 0.0}
    last7 = vals[-7:]; avg7 = sum(last7)/len(last7); avg60 = sum(vals)/len(vals)
    # Om dagens HRV inte är loggad än (saknas eller är 0 i API-svaret), använd 7d-snittet
    today_raw = (wellness[-1].get("hrv") or 0) if wellness else 0
    today = today_raw if today_raw > 0 else round(avg7, 1)
    cv7 = (math.sqrt(sum((x-avg7)**2 for x in last7)/len(last7)) / avg7 * 100) if avg7 else 0
    
    dev_7d = (avg7 - avg60) / avg60 if avg60 else 0
    dev_today = (today - avg60) / avg60 if avg60 else 0
    
    trend = "DOWN" if dev_7d < -0.05 else ("UP" if dev_7d > 0.05 else "STABLE")
    stability = "VERY_STABLE" if cv7 < 8 else ("STABLE" if cv7 < 12 else "UNSTABLE")
    
    if dev_7d < -0.10 or dev_today < -0.25:
        state = "LOW"
    elif dev_7d < -0.05 or dev_today < -0.15:
        state = "SLIGHTLY_LOW"
    elif dev_7d > 0.05 or dev_today > 0.15:
        state = "HIGH"
    else:
        state = "NORMAL"
        
    return {"today": today, "avg7d": round(avg7,1), "avg60d": round(avg60,1),
            "cv7d": round(cv7,1), "state": state, "trend": trend, "stability": stability,
            "deviation_pct": round(dev_today*100,1)}

def calculate_readiness_score(hrv: dict, wellness: list, activities: list) -> dict:
    """Composite readiness score 0-100 based on HRV, sleep, resting HR trend, RPE, and feel."""
    def clamp(v, lo=0, hi=100): return max(lo, min(hi, v))

    wellness = _sorted_wellness(wellness)
    activities = _sorted_activities(activities)

    # HRV (35%) – deviation_pct: -30..+15 -> 0..100
    dev = hrv.get("deviation_pct", 0)
    hrv_sc = clamp(int((dev + 30) / 45 * 100))

    # Sleep (25%) – last night, 4..9h -> 0..100
    recent_sleep = next((w.get("sleepSecs") for w in reversed(wellness) if w.get("sleepSecs")), None)
    sleep_h = (recent_sleep / 3600) if recent_sleep else 7.0
    sleep_sc = clamp(int((sleep_h - 4) / 5 * 100))

    # Resting HR trend (15%) – slope last 7 days
    rhr_vals = [w.get("restingHR") for w in wellness[-7:] if w.get("restingHR")]
    if len(rhr_vals) >= 3:
        slope = (rhr_vals[-1] - rhr_vals[0]) / (len(rhr_vals) - 1)
        rhr_sc = 90 if slope < -0.3 else (40 if slope > 0.3 else 70)
    else:
        rhr_sc = 70

    # RPE (15%) – avg last 5 sessions, 4..9 inverted -> 0..100
    rpes = [a["perceived_exertion"] for a in activities[-5:] if a.get("perceived_exertion")]
    mean_rpe = sum(rpes) / len(rpes) if rpes else 6.0
    rpe_sc = clamp(int((9 - mean_rpe) / 5 * 100))

    # Feel (10%) – avg last 5 sessions, 1..5 where lower is better -> 0..100
    feels = [a["feel"] for a in activities[-5:] if a.get("feel")]
    mean_feel = sum(feels) / len(feels) if feels else 3.0
    feel_sc = clamp(int((5 - mean_feel) / 4 * 100))

    score = int(hrv_sc*0.35 + sleep_sc*0.25 + rhr_sc*0.15 + rpe_sc*0.15 + feel_sc*0.10)
    label = "PEAK" if score >= 80 else ("GOOD" if score >= 65 else ("NORMAL" if score >= 50 else ("LOW" if score >= 35 else "CRITICAL")))

    limiters = []
    for name, value in sorted(
        {
            "hrv": hrv_sc,
            "sleep": sleep_sc,
            "rhr": rhr_sc,
            "rpe": rpe_sc,
            "feel": feel_sc,
        }.items(),
        key=lambda item: item[1],
    ):
        if value < 70:
            limiters.append(f"{name}={value}")

    return {
        "score": score, "label": label,
        "components": {"hrv": hrv_sc, "sleep": sleep_sc, "rhr": rhr_sc, "rpe": rpe_sc, "feel": feel_sc},
        "raw_inputs": {
            "hrv_deviation_pct": round(dev, 1),
            "sleep_hours": round(sleep_h, 1),
            "rhr_slope_7d": round(slope, 2) if len(rhr_vals) >= 3 else None,
            "avg_rpe_last5": round(mean_rpe, 1),
            "avg_feel_last5": round(mean_feel, 2),
        },
        "limiters": limiters,
        "summary": f"Readiness: {score}/100 ({label}) | HRV:{hrv_sc} Sleep:{sleep_sc} RHR:{rhr_sc} RPE:{rpe_sc} Feel:{feel_sc}",
    }



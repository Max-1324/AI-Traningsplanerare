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
        hrv = w.get("hrv")
        sleep = w.get("sleepSecs") or 0
        if hrv is None or hrv == 0:
            bad_wellness_dates.add(d)
            warnings.append(f"HRV missing/zero {d} – excluded from HRV analysis")
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

def calculate_hrv(wellness):
    vals = [w.get("hrv") for w in wellness if w.get("hrv") is not None]
    if len(vals) < 7:
        return {"today": None, "avg7d": None, "avg60d": None, "cv7d": None,
                "state": "INSUFFICIENT_DATA", "trend": "UNKNOWN", "stability": "UNKNOWN", "deviation_pct": 0.0}
    today = vals[-1]; last7 = vals[-7:]; avg7 = sum(last7)/len(last7); avg60 = sum(vals)/len(vals)
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


def rpe_trend(activities) -> str:
    rpes  = [a["perceived_exertion"] for a in activities[-10:] if a.get("perceived_exertion")]
    feels = [a["feel"]               for a in activities[-10:] if a.get("feel")]
    if len(rpes) < 4:
        return "Insufficient RPE data (< 4 sessions)."
    slope = (rpes[-1] - rpes[0]) / (len(rpes) - 1)
    mean_rpe = sum(rpes) / len(rpes)
    cv = (sum((r - mean_rpe)**2 for r in rpes) / len(rpes))**0.5 / mean_rpe if mean_rpe else 0
    lines = [f"RPE last {len(rpes)} sessions: {[round(r,1) for r in rpes]}"]
    lines.append(f"  Slope: {slope:+.2f}/session | CV: {cv:.2f} | Avg: {mean_rpe:.1f}")
    if slope > 0.3:
        lines.append(f"  ⚠️  RPE RISING (+{slope:.2f}/session) – overtraining risk")
    elif slope < -0.3:
        lines.append(f"  ✅ RPE FALLING ({slope:.2f}/session) – good adaptation")
    else:
        lines.append("  RPE stable – normal variation")
    if cv > 0.25:
        lines.append(f"  ⚠️  RPE VOLATILE (CV={cv:.2f}) – irregular recovery")
    if len(feels) >= 4:
        feel_slope = (feels[-1] - feels[0]) / (len(feels) - 1)
        if feel_slope > 0.3:
            lines.append(f"  ⚠️  FEEL DECLINING ({feel_slope:.2f}/session) – signs of fatigue")
        elif feel_slope < -0.3:
            lines.append(f"  ✅ FEEL IMPROVING ({feel_slope:.2f}/session)")
    return "\n".join(lines)

def analyze_np_if(activities: list) -> dict:
    """Analyzes NP/IF patterns for cycling sports – pacing quality and load trend."""
    cycling = [a for a in activities
               if a.get("type") in ("Ride", "VirtualRide")
               and a.get("icu_weighted_avg_watts")
               and a.get("icu_intensity")][-15:]
    if len(cycling) < 4:
        return {"summary": "Insufficient NP/IF data (< 4 cycling sessions).", "flags": []}

    ifs = [session_intensity(a) or 0.0 for a in cycling]
    nps = [a["icu_weighted_avg_watts"] for a in cycling]
    mean_if = sum(ifs) / len(ifs)
    np_mean = sum(nps) / len(nps)
    np_cv   = (sum((x - np_mean)**2 for x in nps) / len(nps))**0.5 / np_mean if np_mean else 0

    flags = []
    if mean_if > 0.82:
        flags.append(f"IF CONSISTENTLY HIGH: avg {mean_if:.2f} – riding harder than planned zone (Z3/Z4)")
    if np_cv > 0.20:
        flags.append(f"NP VARIATION HIGH (CV={np_cv:.2f}) – uneven week-to-week load")
    if len(cycling) >= 6:
        early_np = sum(a["icu_weighted_avg_watts"] for a in cycling[:3]) / 3
        late_np  = sum(a["icu_weighted_avg_watts"] for a in cycling[-3:]) / 3
        if late_np < early_np * 0.90:
            flags.append(f"FRONT-LOADING TREND: NP early {round(early_np)}W -> late {round(late_np)}W – fading in the block")

    parts = [f"NP/IF ({len(cycling)} cycling sessions): avg NP {round(np_mean)}W | IF {mean_if:.2f}"]
    parts += flags if flags else ["Pacing OK – no obvious IF drift or front-loading"]
    return {"summary": "\n  ".join(parts), "flags": flags, "mean_if": mean_if, "mean_np": round(np_mean)}


def acwr(atl, ctl, fitness_history=None) -> dict:
    if ctl <= 0:
        return {"ratio": 0, "rate": 0, "trend": "UNKNOWN", "action": "UNKNOWN"}
    ratio = atl / ctl
    limit = 1.75 if RISK == "HIGH" else 1.5
    rate = 0.0
    trend = "UNKNOWN"
    if fitness_history and len(fitness_history) >= 14:
        history_ratios = [
            f.get("atl", 0) / max(f.get("ctl", 1), 1)
            for f in fitness_history[-14:]
        ]
        rate = (history_ratios[-1] - history_ratios[0]) / 14
        if   rate > 0.08: trend = "RAPID_INCREASE"
        elif rate > 0.02: trend = "INCREASING"
        elif rate < -0.02: trend = "DECREASING"
        else:             trend = "STABLE"
    if ratio > limit:
        action = "REDUCE_LOAD – ratio in danger zone"
    elif ratio > 1.3 and trend == "RAPID_INCREASE":
        action = "REDUCE_LOAD – rapid increase towards danger zone"
    elif ratio > 1.3 or trend in ("RAPID_INCREASE",):
        action = "MONITOR – monitor closely"
    elif ratio < 0.75 or (ratio < 0.85 and trend == "DECREASING"):
        # Detraining risk: training load dropping below CTL maintenance level
        action = "INCREASE_LOAD – risk of detraining, increase training gradually"
    else:
        action = "SAFE_TO_PROGRESS"
    return {"ratio": round(ratio, 2), "rate": round(rate, 3),
            "trend": trend, "action": action}


def acwr_trend_analysis(fitness_history: list) -> dict:
    """
    Detailed ACWR trend analysis with rolling 7d vs 28d load ratio,
    warning levels and risk assessment.

    Returns:
      weekly_ratios: list of last 6 weeks ACWR
      current_zone: SAFE / MODERATE / HIGH / DANGER
      trend_direction: RISING / FALLING / STABLE
      warning: warning text if relevant
      sparkline: ASCII sparkline of the trend
    """
    if not fitness_history or len(fitness_history) < 28:
        return {
            "weekly_ratios": [],
            "current_zone": "UNKNOWN",
            "trend_direction": "UNKNOWN",
            "warning": "Insufficient data (< 28 days).",
            "sparkline": "",
            "summary": "Insufficient data for ACWR trend analysis.",
        }

    # Calculate daily ACWR for the last 42 days
    daily_ratios = []
    for f in fitness_history[-42:]:
        atl = f.get("atl", 0)
        ctl = max(f.get("ctl", 1), 1)
        daily_ratios.append(round(atl / ctl, 3))

    # Weekly average (last 6 weeks)
    weekly_ratios = []
    for i in range(0, min(len(daily_ratios), 42), 7):
        week_slice = daily_ratios[i:i+7]
        if week_slice:
            weekly_ratios.append(round(sum(week_slice) / len(week_slice), 2))

    current_ratio = daily_ratios[-1] if daily_ratios else 0

    # Zone classification
    if current_ratio < 0.8:
        zone = "UNDERTRAINED"
        zone_emoji = "🔵"
    elif current_ratio <= 1.1:
        zone = "SAFE"
        zone_emoji = "🟢"
    elif current_ratio <= 1.3:
        zone = "MODERATE"
        zone_emoji = "🟡"
    elif current_ratio <= 1.5:
        zone = "HIGH"
        zone_emoji = "🟠"
    else:
        zone = "DANGER"
        zone_emoji = "🔴"

    # Trend direction (last 14 days slope)
    if len(daily_ratios) >= 14:
        recent = daily_ratios[-14:]
        slope = (recent[-1] - recent[0]) / 14
        if slope > 0.015:
            direction = "RISING"
        elif slope < -0.015:
            direction = "FALLING"
        else:
            direction = "STABLE"
    else:
        slope = 0
        direction = "UNKNOWN"

    # Sparkline
    chars = " ▁▂▃▄▅▆▇█"
    if weekly_ratios:
        mn, mx = min(weekly_ratios), max(weekly_ratios)
        rng = mx - mn or 0.1
        sparkline = "".join(
            chars[min(8, int((r - mn) / rng * 8))]
            for r in weekly_ratios
        )
    else:
        sparkline = ""

    # Warning
    warning = ""
    if zone == "DANGER":
        warning = f"🔴 ACWR {current_ratio:.2f} in danger zone (>1.5)! Reduce load immediately."
    elif zone == "HIGH" and direction == "RISING":
        warning = f"🟠 ACWR {current_ratio:.2f} rising towards danger zone. Slow down volume increase."
    elif zone == "UNDERTRAINED" and direction == "FALLING":
        warning = f"🔵 ACWR {current_ratio:.2f} falling – risk of detraining. Increase gradually."
    elif zone == "HIGH":
        warning = f"🟡 ACWR {current_ratio:.2f} high but stable. Monitor closely."

    summary = (
        f"ACWR {current_ratio:.2f} {zone_emoji} {zone} | "
        f"Trend: {direction} ({slope:+.3f}/day) | "
        f"Sparkline: [{sparkline}] | "
        f"{warning}" if warning else
        f"ACWR {current_ratio:.2f} {zone_emoji} {zone} | Trend: {direction} ({slope:+.3f}/day) | [{sparkline}]"
    )

    return {
        "weekly_ratios":    weekly_ratios,
        "current_ratio":    current_ratio,
        "current_zone":     zone,
        "zone_emoji":       zone_emoji,
        "trend_direction":  direction,
        "slope":            round(slope, 4),
        "warning":          warning,
        "sparkline":        sparkline,
        "summary":          summary,
    }


# ══════════════════════════════════════════════════════════════════════════════
# SPORT-SPECIFIC ACWR (per sport type)
# ══════════════════════════════════════════════════════════════════════════════

def per_sport_acwr(activities: list) -> dict:
    """
    Calculates ATL, CTL and ACWR separately per sport type.
    Important for capturing running or roller skiing load that is hidden in total ACWR.
    """
    today = date.today()
    sports = set(a.get("type") for a in activities if a.get("type") and a.get("type") != "Rest")
    result = {}

    for sport in sports:
        sport_acts = [a for a in activities if a.get("type") == sport]
        atl = 0.0
        ctl = 0.0
        for a in sport_acts:
            ds = safe_date_str(a)
            if not ds:
                continue
            try:
                days_ago = (today - datetime.strptime(ds, "%Y-%m-%d").date()).days
            except Exception:
                continue
            
            if days_ago < 0:
                continue
                
            tss = a.get("icu_training_load") or 0
            if days_ago <= 7:
                atl += tss * (1 - days_ago / 7)
            if days_ago <= 28:
                ctl += tss * (1 - days_ago / 28)

        ratio = round(atl / ctl, 2) if ctl > 0 else 0.0
        if ratio > 1.5:
            zone = "DANGER"
            warning = f"ACWR {ratio:.2f} > 1.5 for {sport} – high injury risk!"
        elif ratio > 1.3:
            zone = "HIGH"
            warning = f"ACWR {ratio:.2f} for {sport} – monitor closely"
        elif ctl > 0 and ratio < 0.8:
            zone = "UNDERTRAINED"
            warning = ""
        else:
            zone = "SAFE"
            warning = ""

        result[sport] = {
            "atl":    round(atl, 1),
            "ctl":    round(ctl, 1),
            "ratio":  ratio,
            "zone":   zone,
            "warning": warning,
        }

    return result


def tsb_zone(tsb, ctl, fitness_history):
    if ctl <= 0: return "UNKNOWN"
    hist = [f.get("tsb",0) for f in fitness_history[-60:] if f.get("tsb") is not None]
    if len(hist) > 14:
        low_t = sorted(hist)[len(hist)//10]
        high_t = sorted(hist)[len(hist)*9//10]
    else:
        low_t = -0.30 * ctl; high_t = 0.10 * ctl
    pct = (tsb/ctl)*100
    if   tsb > high_t: return f"PEAKING ({pct:+.0f}% of CTL)"
    elif tsb > 0:      return f"FRESH ({pct:+.0f}%)"
    elif tsb > low_t:  return f"OPTIMAL TRAINING ({pct:+.0f}%)"
    else:              return f"HIGH FATIGUE ({pct:+.0f}%) - rest recommended"

def sport_volumes(activities):
    cutoff = datetime.now() - timedelta(days=7)
    vols = {}
    for a in activities:
        try:
            if datetime.strptime(a["start_date_local"][:10], "%Y-%m-%d") >= cutoff:
                t = a.get("type","Other")
                vols[t] = vols.get(t,0) + ((a.get("moving_time") or a.get("elapsed_time") or 0)/60)
        except: continue
    return vols

def sport_budget(sport_type, activities, manual_workouts) -> dict:
    RISK_GROWTH = {"low": 1.20, "medium": 1.15, "high": 1.10}
    sport_info  = next((s for s in SPORTS if s["intervals_type"] == sport_type), {})
    risk_level  = sport_info.get("injury_risk", "medium")
    growth      = RISK_GROWTH.get(risk_level, 1.15)
    cutoff_14d = datetime.now() - timedelta(days=14)
    cutoff_7d  = datetime.now() - timedelta(days=7)
    past_14d = sum(
        (a.get("moving_time") or a.get("elapsed_time") or 0) / 60 for a in activities
        if a.get("type") == sport_type and safe_date(a) >= cutoff_14d
    )
    past_7d = sum(
        (a.get("moving_time") or a.get("elapsed_time") or 0) / 60 for a in activities
        if a.get("type") == sport_type and safe_date(a) >= cutoff_7d
    )
    basis   = (past_7d + past_14d / 2) / 1.5
    budget  = max(basis * growth, 60)
    locked  = sum(w.get("moving_time", 0) / 60
                  for w in manual_workouts if w.get("type") == sport_type)
    remaining = max(0, budget - locked)
    return {
        "sport":      sport_type,
        "risk":       risk_level,
        "past_7d":    round(past_7d),
        "past_14d":   round(past_14d),
        "basis":      round(basis),
        "max_budget": round(budget),
        "locked":     round(locked),
        "remaining":  round(remaining),
        "growth_pct": round((growth - 1) * 100),
    }




def ctl_ramp_from_daily_tss(ctl: float, daily_tss: float) -> float:
    """Approximated CTL ramp/week from daily TSS according to the 42-day model."""
    return round((daily_tss - ctl) / 6.0, 1)


def choose_target_ramp(ctl: float, mesocycle_factor: float = 1.0,
                       required_weekly_tss: float | None = None,
                       actual_weekly_ramp: float | None = None) -> float:
    """
    Choose target ramp for normal build.

    Philosophy:
      - Normal range: +5-7 CTL/week
      - Bias around +6 CTL/week
      - Build weeks can nudge upwards, but not automatically max everything
      - Detraining returns aggressively to +7
      - Deload still gets its reduction via mesocycle_factor in tss_budget()
    """
    if required_weekly_tss:
        return ctl_ramp_from_daily_tss(ctl, required_weekly_tss / 7.0)
    if actual_weekly_ramp is not None and actual_weekly_ramp < -1:
        return 7.0
    if actual_weekly_ramp is not None:
        if actual_weekly_ramp >= 6.5:
            return 5.0
        if actual_weekly_ramp >= 5.5:
            return 5.5
        if actual_weekly_ramp <= 3.5:
            if mesocycle_factor >= 1.10:
                return 7.0
            if mesocycle_factor >= 1.05:
                return 6.5
            return 6.0
    if mesocycle_factor >= 1.10:
        return 6.5
    if mesocycle_factor >= 1.05:
        return 6.0
    return 5.5

def tss_budget(ctl, tsb, horizon, fitness_history, mesocycle_factor=1.0,
               required_weekly_tss=None, actual_weekly_ramp=None):
    """
    Calculates TSS budget for the horizon based on CTL-ODE physics.

    CTL-ODE: ΔCTL/day = (TSS - CTL) / 42
    To achieve target ramp R CTL/week: TSS_day = CTL + R * 6
    (derived: ΔCTL/week = (TSS_day - CTL) * 7/42 => TSS_day = CTL + ramp * 6)

    Recommended ramp interval (this coach):
      Normal build state: +5-7 CTL/week
      Detraining rebuild: +7.0 CTL/week
      Absolute ceiling (crash block): +8 CTL/week

    - If required_weekly_tss exists (from ctl_trajectory): convert directly.
    - mesocycle_factor is applied to the build part (surplus), not maintenance.
    """
    target_ramp = choose_target_ramp(
        ctl,
        mesocycle_factor=mesocycle_factor,
        required_weekly_tss=required_weekly_tss,
        actual_weekly_ramp=actual_weekly_ramp,
    )
    daily_target = ctl + target_ramp * 6.0

    # Safety cap: +8 CTL/week (crash weeks require manual override)
    daily_cap = ctl + 8.0 * 6.0
    daily_target = min(daily_target, daily_cap)

    # TSB fatigue adjustment: if athlete is clearly exhausted, pull down towards maintenance
        # Use 3-day average to avoid yo-yo effect from single hard sessions
    hist_tsb = [f.get("tsb", 0) for f in fitness_history[-60:] if f.get("tsb") is not None]
    typical_low = sorted(hist_tsb)[max(0, len(hist_tsb) // 5)] if len(hist_tsb) > 14 else -0.30 * ctl
        
    recent_tsb = [f.get("tsb", 0) for f in fitness_history[-3:] if f.get("tsb") is not None]
    avg_recent_tsb = sum(recent_tsb) / len(recent_tsb) if recent_tsb else tsb
        
    if avg_recent_tsb < typical_low:
        daily_target = max(ctl, daily_target * 0.95)

    # Maintenance floor: deload weeks allow 90% of CTL (true recovery)
    daily_floor = ctl * (0.90 if mesocycle_factor < 1.0 else 1.0)

    # Mesocycle factor only on the build part - deload lowers surplus, not maintenance
    surplus = max(daily_target - daily_floor, 0.0)
    daily = daily_floor + surplus * mesocycle_factor

    return round(daily * horizon)


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
        if any("Durability-gap" in g for g in race_demands["gaps"]):
            add(
                "durability",
                84,
                "Race demands show that long durability is still a clear bottleneck.",
                ["1 long Z2 session", "progressive long ride", "train nutrition during long sessions"],
            )
        if any("Fueling-gap" in g for g in race_demands["gaps"]):
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
        if any("IF KONSEKVENT HÖG" in f or "FRONT-LOADING" in f for f in np_flags):
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

def race_week_protocol(races: list, today: date) -> dict:
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

    # Build day-specific protocol
    protocol = []

    day_templates = {
        6: {
            "title": f"🏁 Pre-race: Last medium session ({race_name} in 6d)",
            "type": "VirtualRide", "dur": 90, "slot": "MAIN",
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
            "title": f"🏁 Pre-race: Easy bike + quick strength ({race_name} in 5d)",
            "type": "VirtualRide", "dur": 45, "slot": "MAIN",
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
            "type": "VirtualRide", "dur": 55, "slot": "MAIN",
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
            "title": f"🏁 Pre-race: Spin ({race_name} in 2d)",
            "type": "VirtualRide", "dur": 30, "slot": "MAIN",
            "steps": [
                {"d": 10, "z": "Z1", "desc": "Easy"},
                {"d": 10, "z": "Z2", "desc": "Light pressure - nothing more"},
                {"d": 10, "z": "Z1", "desc": "Cool-down"},
            ],
            "desc": "Just spin the legs. 30 min max. Save everything for the race."
        },
        1: {
            "title": f"🏁 Pre-race: Rest/Short activation ({race_name} TOMORROW!)",
            "type": "VirtualRide", "dur": 20, "slot": "MAIN",
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
            "type": "Ride", "dur": 0, "slot": "MAIN", "steps": [],
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

def env_nutrition(temp_max, duration_min, first_zone):
    advice = []
    low_int = first_zone in ("Z1","Z2","Zone 1","Zone 2")
    if temp_max > 25: advice.append("HEAT: +200ml/h. Electrolytes (>=800mg Na/l).")
    elif temp_max < 0: advice.append("COLD: Drink according to schedule. Keep drink lukewarm.")
    if low_int and duration_min < 90: advice.append("TRAIN LOW: Opportunity to ride fasted for fat adaptation.")
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


# ── TSS REFERENCE ─────────────────────────────────────────────────────────────

def compute_tss_reference(activities: list) -> str:
    """Return a calibrated TSS cheat sheet derived from the athlete's own history.

    Groups completed sessions by sport, computes median TSS/hour per sport (and
    by intensity for VirtualRide where power data is available), then formats a
    compact reference string to inject into the generation prompt.

    Falls back to theoretical zone-formula values if there is too little data for
    a given sport type.
    """
    _SPORTS = ("VirtualRide", "Ride", "Run", "RollerSki")
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

from training_plan.core.common import *
from training_plan.engine.libraries import *
from training_plan.engine.planning import *
from training_plan.engine.utils import safe_date_str, safe_date

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
        except (ValueError, TypeError, KeyError):
            continue
    return vols

_MIN_SPORT_BUDGET: dict[str, int] = {
    "Run":       int(os.getenv("MIN_BUDGET_RUN_MIN",       "60")),
    "RollerSki": int(os.getenv("MIN_BUDGET_ROLLERSKI_MIN", "90")),
}
# Default floors by injury_risk for sports not explicitly listed above.
# Medium-risk sports (RollerSki, NordicSki) get 90min; high-risk (Run) 60min;
# low-risk sports (Ride, VirtualRide, Swim, WeightTraining) get no floor.
_RISK_MIN_FLOOR = {"low": 0, "medium": 90, "high": 60}


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
    basis      = (past_7d + past_14d / 2) / 1.5
    min_floor  = _MIN_SPORT_BUDGET.get(
        sport_type,
        _RISK_MIN_FLOOR.get(risk_level, 60),
    )
    budget     = max(basis * growth, min_floor)
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



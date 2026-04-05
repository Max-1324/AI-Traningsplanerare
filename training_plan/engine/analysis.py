from training_plan.core.common import *
from training_plan.engine.libraries import *
from training_plan.engine.planning import *

def validate_data_quality(activities: list, wellness: list) -> dict:
    """Identifierar och filtrerar bort datapunkter som troligen är mätfel."""
    warnings: list = []
    filtered_activity_ids: set = set()
    bad_wellness_dates: set = set()

    for a in activities:
        aid = a.get("id") or a.get("start_date_local", "")
        tss = a.get("icu_training_load") or 0
        dur = (a.get("moving_time") or a.get("elapsed_time") or 0) / 60
        intf = a.get("icu_intensity") or 0
        name_lower = (a.get("name") or "").lower()
        is_race = "race" in name_lower or "tävling" in name_lower or a.get("workout_type") == "race"
        if intf > 1.8 and not is_race:
            warnings.append(f"Högt IF {intf:.2f} på {_safe_date_str(a)} – troligen felaktig FTP, filtreras från analys")
            filtered_activity_ids.add(aid)
        elif tss > 600:
            warnings.append(f"Orimlig TSS {tss} på {_safe_date_str(a)} – filtreras")
            filtered_activity_ids.add(aid)
        elif 0 < dur < 5 and tss > 10:
            warnings.append(f"Kort aktivitet ({dur:.0f}min) med TSS {tss} på {_safe_date_str(a)} – filtreras")
            filtered_activity_ids.add(aid)

    for w in wellness:
        d = w.get("id", "")[:10]
        hrv = w.get("hrv")
        sleep = w.get("sleepSecs") or 0
        if hrv is None or hrv == 0:
            bad_wellness_dates.add(d)
            warnings.append(f"HRV saknas/noll {d} – exkluderas från HRV-analys")
        elif hrv > 200:
            bad_wellness_dates.add(d)
            warnings.append(f"Orimlig HRV {hrv}ms {d} – troligen mätfel, filtreras")
        if 0 < sleep < 7200:
            warnings.append(f"Mycket kort sömn {sleep/3600:.1f}h {d} – kolla klockinställning")
        elif sleep > 57600:
            bad_wellness_dates.add(d)
            warnings.append(f"Orimlig sömn {sleep/3600:.1f}h {d} – troligen klockreset, filtreras")

    return {
        "warnings": warnings,
        "filtered_activity_ids": filtered_activity_ids,
        "bad_wellness_dates": bad_wellness_dates,
        "has_issues": bool(warnings),
    }

# ══════════════════════════════════════════════════════════════════════════════
# MOTIVATIONSANALYS & PSYKOLOGISK COACHING
# ══════════════════════════════════════════════════════════════════════════════

def analyze_motivation(wellness: list, activities: list) -> dict:
    """Analyserar 14-dagars känslotrend för att tidigt identifiera utbrändningsrisk."""
    cutoff = (date.today() - timedelta(days=14)).isoformat()
    week2_cutoff = (date.today() - timedelta(days=7)).isoformat()

    recent_acts = [a for a in activities if _safe_date_str(a) >= cutoff and a.get("feel") is not None]
    feel_vals = [a["feel"] for a in recent_acts]
    avg_feel = sum(feel_vals) / len(feel_vals) if feel_vals else 3.0

    w1_feels = [a["feel"] for a in recent_acts if cutoff <= _safe_date_str(a) < week2_cutoff]
    w2_feels = [a["feel"] for a in recent_acts if _safe_date_str(a) >= week2_cutoff]
    avg_w1 = sum(w1_feels) / len(w1_feels) if w1_feels else avg_feel
    avg_w2 = sum(w2_feels) / len(w2_feels) if w2_feels else avg_feel

    delta = avg_w2 - avg_w1
    if delta > 0.3:
        trend = "IMPROVING"
    elif delta < -0.3:
        trend = "DECLINING"
    else:
        trend = "STABLE"

    # Räkna veckor med sjunkande känsla (jämför med ännu äldre data)
    weeks_declining = 0
    if trend == "DECLINING":
        weeks_declining = 1
        older_cutoff = (date.today() - timedelta(days=28)).isoformat()
        older_acts = [a for a in activities if older_cutoff <= _safe_date_str(a) < cutoff and a.get("feel") is not None]
        avg_older = sum(a["feel"] for a in older_acts) / len(older_acts) if older_acts else avg_feel
        if avg_w1 < avg_older - 0.3:
            weeks_declining = 2

    if avg_feel < 2.5 and weeks_declining >= 2:
        state = "BURNOUT_RISK"
    elif avg_feel < 2.5 or (avg_feel < 3.0 and trend == "DECLINING"):
        state = "FATIGUED"
    elif avg_feel >= 3.5 and trend in ("IMPROVING", "STABLE"):
        state = "MOTIVATED"
    else:
        state = "NEUTRAL"

    return {
        "state": state,
        "trend": trend,
        "avg_feel": round(avg_feel, 2),
        "weeks_declining": weeks_declining,
        "n_activities": len(feel_vals),
        "summary": f"Motivation: {state} | Trend: {trend} | Snittkänsla: {avg_feel:.1f}/5 ({len(feel_vals)} pass senaste 14d)",
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
    """Sammansatt formpoäng 0–100 baserat på HRV, sömn, viloHR-trend, RPE och känsla."""
    def clamp(v, lo=0, hi=100): return max(lo, min(hi, v))

    # HRV (35%) – deviation_pct: -30..+15 → 0..100
    dev = hrv.get("deviation_pct", 0)
    hrv_sc = clamp(int((dev + 30) / 45 * 100))

    # Sömn (25%) – senaste natten, 4..9h → 0..100
    recent_sleep = next((w.get("sleepSecs") for w in reversed(wellness) if w.get("sleepSecs")), None)
    sleep_h = (recent_sleep / 3600) if recent_sleep else 7.0
    sleep_sc = clamp(int((sleep_h - 4) / 5 * 100))

    # Vilopuls-trend (15%) – slope sista 7 dagar
    rhr_vals = [w.get("restingHR") for w in wellness[-7:] if w.get("restingHR")]
    if len(rhr_vals) >= 3:
        slope = (rhr_vals[-1] - rhr_vals[0]) / (len(rhr_vals) - 1)
        rhr_sc = 90 if slope < -0.3 else (40 if slope > 0.3 else 70)
    else:
        rhr_sc = 70

    # RPE (15%) – snitt sista 5 pass, 4..9 inverterat → 0..100
    rpes = [a["perceived_exertion"] for a in activities[-5:] if a.get("perceived_exertion")]
    mean_rpe = sum(rpes) / len(rpes) if rpes else 6.0
    rpe_sc = clamp(int((9 - mean_rpe) / 5 * 100))

    # Känsla (10%) – snitt sista 5 pass, 1..5 → 0..100
    feels = [a["feel"] for a in activities[-5:] if a.get("feel")]
    mean_feel = sum(feels) / len(feels) if feels else 3.0
    feel_sc = clamp(int((mean_feel - 1) / 4 * 100))

    score = int(hrv_sc*0.35 + sleep_sc*0.25 + rhr_sc*0.15 + rpe_sc*0.15 + feel_sc*0.10)
    label = "TOPP" if score >= 80 else ("BRA" if score >= 65 else ("NORMAL" if score >= 50 else ("LAG" if score >= 35 else "KRITISK")))

    return {
        "score": score, "label": label,
        "components": {"hrv": hrv_sc, "sleep": sleep_sc, "rhr": rhr_sc, "rpe": rpe_sc, "feel": feel_sc},
        "summary": f"Readiness: {score}/100 ({label}) | HRV:{hrv_sc} Sömn:{sleep_sc} ViloHR:{rhr_sc} RPE:{rpe_sc} Känsla:{feel_sc}",
    }


def rpe_trend(activities) -> str:
    rpes  = [a["perceived_exertion"] for a in activities[-10:] if a.get("perceived_exertion")]
    feels = [a["feel"]               for a in activities[-10:] if a.get("feel")]
    if len(rpes) < 4:
        return "Otillräcklig RPE-data (< 4 pass)."
    slope = (rpes[-1] - rpes[0]) / (len(rpes) - 1)
    mean_rpe = sum(rpes) / len(rpes)
    cv = (sum((r - mean_rpe)**2 for r in rpes) / len(rpes))**0.5 / mean_rpe if mean_rpe else 0
    lines = [f"RPE senaste {len(rpes)} pass: {[round(r,1) for r in rpes]}"]
    lines.append(f"  Slope: {slope:+.2f}/pass | CV: {cv:.2f} | Snitt: {mean_rpe:.1f}")
    if slope > 0.3:
        lines.append(f"  ⚠️  RPE STIGANDE (+{slope:.2f}/pass) – risk för överträning")
    elif slope < -0.3:
        lines.append(f"  ✅ RPE SJUNKANDE ({slope:.2f}/pass) – god adaptation")
    else:
        lines.append("  RPE stabil – normal variation")
    if cv > 0.25:
        lines.append(f"  ⚠️  RPE VOLATIL (CV={cv:.2f}) – oregelbunden återhämtning")
    if len(feels) >= 4:
        feel_slope = (feels[-1] - feels[0]) / (len(feels) - 1)
        if feel_slope < -0.3:
            lines.append(f"  ⚠️  KÄNSLA SJUNKER ({feel_slope:.2f}/pass) – tecken på utmattning")
    return "\n".join(lines)

def analyze_np_if(activities: list) -> dict:
    """Analyserar NP/IF-mönster för cykelsporter – pacing-kvalitet och belastningstrend."""
    cycling = [a for a in activities
               if a.get("type") in ("Ride", "VirtualRide")
               and a.get("icu_weighted_avg_watts")
               and a.get("icu_intensity")][-15:]
    if len(cycling) < 4:
        return {"summary": "Otillräcklig NP/IF-data (< 4 cykelpass).", "flags": []}

    ifs = [a["icu_intensity"] for a in cycling]
    nps = [a["icu_weighted_avg_watts"] for a in cycling]
    mean_if = sum(ifs) / len(ifs)
    np_mean = sum(nps) / len(nps)
    np_cv   = (sum((x - np_mean)**2 for x in nps) / len(nps))**0.5 / np_mean if np_mean else 0

    flags = []
    if mean_if > 0.82:
        flags.append(f"IF KONSEKVENT HÖG: snitt {mean_if:.2f} – kör hårdare än planerat zon (Z3/Z4)")
    if np_cv > 0.20:
        flags.append(f"NP-VARIATION HÖG (CV={np_cv:.2f}) – ojämn belastning vecka-till-vecka")
    if len(cycling) >= 6:
        early_np = sum(a["icu_weighted_avg_watts"] for a in cycling[:3]) / 3
        late_np  = sum(a["icu_weighted_avg_watts"] for a in cycling[-3:]) / 3
        if late_np < early_np * 0.90:
            flags.append(f"FRONT-LOADING TREND: NP tidigt {round(early_np)}W → sent {round(late_np)}W – mattning i blocket")

    parts = [f"NP/IF ({len(cycling)} cykelpass): snitt NP {round(np_mean)}W | IF {mean_if:.2f}"]
    parts += flags if flags else ["Pacing OK – ingen uppenbar IF-drift eller front-loading"]
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
        action = "REDUCE_LOAD – ratio i farlig zon"
    elif ratio > 1.3 and trend == "RAPID_INCREASE":
        action = "REDUCE_LOAD – snabb ökning mot farlig zon"
    elif ratio > 1.3 or trend in ("RAPID_INCREASE",):
        action = "MONITOR – övervaka noga"
    elif ratio < 0.75 or (ratio < 0.85 and trend == "DECREASING"):
        # Detraining-risk: träningsbelastning sjunker under CTL-underhållsnivå
        action = "INCREASE_LOAD – risk för detraining, öka träningen gradvis"
    else:
        action = "SAFE_TO_PROGRESS"
    return {"ratio": round(ratio, 2), "rate": round(rate, 3),
            "trend": trend, "action": action}


def acwr_trend_analysis(fitness_history: list) -> dict:
    """
    Detaljerad ACWR-trendanalys med rullande 7d vs 28d belastningskvot,
    varningsnivåer och riskvärdering.

    Returnerar:
      weekly_ratios: lista med senaste 6 veckors ACWR
      current_zone: SAFE / MODERATE / HIGH / DANGER
      trend_direction: RISING / FALLING / STABLE
      warning: varningstext om relevant
      sparkline: ASCII-sparkline av trenden
    """
    if not fitness_history or len(fitness_history) < 28:
        return {
            "weekly_ratios": [],
            "current_zone": "UNKNOWN",
            "trend_direction": "UNKNOWN",
            "warning": "Otillräcklig data (< 28 dagar).",
            "sparkline": "",
            "summary": "Otillräcklig data för ACWR-trendanalys.",
        }

    # Beräkna daglig ACWR för senaste 42 dagar
    daily_ratios = []
    for f in fitness_history[-42:]:
        atl = f.get("atl", 0)
        ctl = max(f.get("ctl", 1), 1)
        daily_ratios.append(round(atl / ctl, 3))

    # Veckosnitt (senaste 6 veckor)
    weekly_ratios = []
    for i in range(0, min(len(daily_ratios), 42), 7):
        week_slice = daily_ratios[i:i+7]
        if week_slice:
            weekly_ratios.append(round(sum(week_slice) / len(week_slice), 2))

    current_ratio = daily_ratios[-1] if daily_ratios else 0

    # Zonklassificering
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

    # Trendriktning (senaste 14 dagars slope)
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

    # Varning
    warning = ""
    if zone == "DANGER":
        warning = f"🔴 ACWR {current_ratio:.2f} i farozonen (>1.5)! Sänk belastningen omedelbart."
    elif zone == "HIGH" and direction == "RISING":
        warning = f"🟠 ACWR {current_ratio:.2f} stigande mot farozonen. Bromsa volymökningen."
    elif zone == "UNDERTRAINED" and direction == "FALLING":
        warning = f"🔵 ACWR {current_ratio:.2f} sjunker – risk för detraining. Öka gradvis."
    elif zone == "HIGH":
        warning = f"🟡 ACWR {current_ratio:.2f} högt men stabilt. Övervaka noga."

    summary = (
        f"ACWR {current_ratio:.2f} {zone_emoji} {zone} | "
        f"Trend: {direction} ({slope:+.3f}/dag) | "
        f"Sparkline: [{sparkline}] | "
        f"{warning}" if warning else
        f"ACWR {current_ratio:.2f} {zone_emoji} {zone} | Trend: {direction} ({slope:+.3f}/dag) | [{sparkline}]"
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
# SPORT-SPECIFIK ACWR (per sporttyp)
# ══════════════════════════════════════════════════════════════════════════════

def per_sport_acwr(activities: list) -> dict:
    """
    Beräknar ATL, CTL och ACWR separat per sporttyp.
    Viktigt för att fånga löpnings- eller rullskidbelastning som döljs i total-ACWR.
    """
    today = date.today()
    sports = set(a.get("type") for a in activities if a.get("type") and a.get("type") != "Rest")
    result = {}

    for sport in sports:
        sport_acts = [a for a in activities if a.get("type") == sport]
        atl = 0.0
        ctl = 0.0
        for a in sport_acts:
            ds = _safe_date_str(a)
            if not ds:
                continue
            try:
                days_ago = (today - datetime.strptime(ds, "%Y-%m-%d").date()).days
            except Exception:
                continue
            tss = a.get("icu_training_load") or 0
            if days_ago <= 7:
                atl += tss * (1 - days_ago / 7)
            if days_ago <= 28:
                ctl += tss * (1 - days_ago / 28)

        ratio = round(atl / ctl, 2) if ctl > 0 else 0.0
        if ratio > 1.5:
            zone = "DANGER"
            warning = f"ACWR {ratio:.2f} > 1.5 för {sport} – hög skaderisk!"
        elif ratio > 1.3:
            zone = "HIGH"
            warning = f"ACWR {ratio:.2f} för {sport} – övervaka noga"
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
    if   tsb > high_t: return f"PEAKING ({pct:+.0f}% av CTL)"
    elif tsb > 0:      return f"FRESH ({pct:+.0f}%)"
    elif tsb > low_t:  return f"OPTIMAL TRANING ({pct:+.0f}%)"
    else:              return f"HÖG TRÖTTHET ({pct:+.0f}%) - vila rekommenderas"

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
    RISK_GROWTH = {"låg": 1.20, "medel": 1.15, "hög": 1.10}
    sport_info  = next((s for s in SPORTS if s["intervals_type"] == sport_type), {})
    risk_level  = sport_info.get("skaderisk", "medel")
    growth      = RISK_GROWTH.get(risk_level, 1.15)
    cutoff_14d = datetime.now() - timedelta(days=14)
    cutoff_7d  = datetime.now() - timedelta(days=7)
    past_14d = sum(
        (a.get("moving_time") or a.get("elapsed_time") or 0) / 60 for a in activities
        if a.get("type") == sport_type and _safe_date(a) >= cutoff_14d
    )
    past_7d = sum(
        (a.get("moving_time") or a.get("elapsed_time") or 0) / 60 for a in activities
        if a.get("type") == sport_type and _safe_date(a) >= cutoff_7d
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


def _safe_date(activity) -> datetime:
    try:
        return datetime.strptime(activity["start_date_local"][:10], "%Y-%m-%d")
    except Exception:
        return datetime(1970, 1, 1)


def ctl_ramp_from_daily_tss(ctl: float, daily_tss: float) -> float:
    """Approximerad CTL-ramp/vecka från daglig TSS enligt 42-dagarsmodellen."""
    return round((daily_tss - ctl) / 6.0, 1)


def choose_target_ramp(ctl: float, mesocycle_factor: float = 1.0,
                       required_weekly_tss: float | None = None,
                       actual_weekly_ramp: float | None = None) -> float:
    """
    Välj mål-ramp för normal uppbyggnad.

    Filosofi:
      - Normalt spann: +5–7 CTL/vecka
      - Bias runt +6 CTL/vecka
      - Byggveckor får gärna nudga uppåt, men inte per automatik maxa allt
      - Detraining återgår aggressivt till +7
      - Deload får fortfarande sin sänkning via mesocycle_factor i tss_budget()
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
    Beräknar TSS-budget för horisonten baserat på CTL-ODE-fysiken.

    CTL-ODE: ΔCTL/dag = (TSS - CTL) / 42
    För att uppnå målramp R CTL/vecka: TSS_dag = CTL + R × 6
    (härleds: ΔCTL/vecka = (TSS_dag − CTL) × 7/42 ⟹ TSS_dag = CTL + ramp × 6)

    Rekommenderat rampintervall (denna coach):
      Normalt uppbyggnadsläge: +5–7 CTL/vecka
      Detreningsåteruppbyggnad:  +7.0 CTL/vecka
      Absolut tak (crash-block):  +8 CTL/vecka

    - Om required_weekly_tss finns (från ctl_trajectory): konvertera direkt.
    - mesocycle_factor appliceras på byggdelen (surplus), inte underhållet.
    """
    target_ramp = choose_target_ramp(
        ctl,
        mesocycle_factor=mesocycle_factor,
        required_weekly_tss=required_weekly_tss,
        actual_weekly_ramp=actual_weekly_ramp,
    )
    daily_target = ctl + target_ramp * 6.0

    # Säkerhetstak: +8 CTL/vecka (crash-veckor kräver manuell override)
    daily_cap = ctl + 8.0 * 6.0
    daily_target = min(daily_target, daily_cap)

    # TSB-trötthetsjustering: om atlet är klart utmattad, dra ned mot underhåll
    # Använd historisk TSB-fördelning för att avgöra vad som är "normalt negativt"
    hist_tsb = [f.get("tsb", 0) for f in fitness_history[-60:] if f.get("tsb") is not None]
    typical_low = sorted(hist_tsb)[max(0, len(hist_tsb) // 5)] if len(hist_tsb) > 14 else -0.30 * ctl
    if tsb < typical_low:
        daily_target = max(ctl, daily_target * 0.85)

    # Underhållsgolv: deload-veckor tillåter 90% av CTL (äkta återhämtning)
    daily_floor = ctl * (0.90 if mesocycle_factor < 1.0 else 1.0)

    # Mesocykel-faktor på bara byggdelen – deload sänker surplus, inte underhåll
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
            f"Readiness {readiness_score}/100 och motivation {motivation_state} kräver mer återhämtning för att kunna absorbera träningen.",
            ["1-2 extra lätta dagar", "kortare huvudpass", "behåll bara mest värdefulla nyckelpass"],
        )
    elif motivation_state == "FATIGUED":
        add(
            "recovery",
            82,
            "Psykologisk/mental trötthet syns i känsla-trenden - lite lägre friktion ger bättre långsiktig utveckling.",
            ["rolig kvalitet i kortare format", "hög genomförbarhet", "undvik onödigt filler"],
        )

    if weighted_compliance < 75 or key_completion < 70:
        add(
            "consistency",
            92,
            f"Viktad compliance {weighted_compliance}% och nyckelpass {key_completion}% är för låg för maximal utveckling.",
            ["2-3 must-hit-pass", "kortare flexpass", "mindre planfriktion på vardagar"],
        )

    if ftp_check and ftp_check.get("needs_test") and phase_name not in ("Race Week",):
        add(
            "calibration",
            86,
            ftp_check["recommendation"],
            ["schemalägg FTP-test", "håll 1-2 dagar enklare före test", "justera framtida zoner efter utfallet"],
        )

    if race_demands and race_demands.get("gaps"):
        if any("Durability-gap" in g for g in race_demands["gaps"]):
            add(
                "durability",
                84,
                "Race demands visar att lång uthållighet fortfarande är en tydlig flaskhals.",
                ["1 långt Z2-pass", "progressiv långtur", "träna nutrition under långpass"],
            )
        if any("Fueling-gap" in g for g in race_demands["gaps"]):
            add(
                "fueling",
                74,
                "Långa nutrition-repetitioner saknas för tävlingsmålet.",
                ["CHO-plan på långpass", "öva 80-100g CHO/h", "logga magtolerans"],
            )

    threshold_count = session_scores.get("threshold", {}).get("count", 0)
    threshold_score = session_scores.get("threshold", {}).get("avg_score", 0)
    if phase_name in ("Base", "Build") and (threshold_count < 2 or threshold_score < 68):
        add(
            "threshold",
            76 if phase_name == "Build" else 68,
            f"Tröskelstimulit är {'få' if threshold_count < 2 else 'för svaga'} för nuvarande fas.",
            ["1 tröskelpass", "håll RPE 6-7", "jämn kvalitet genom alla intervaller"],
        )

    vo2_count = session_scores.get("vo2", {}).get("count", 0)
    vo2_score = session_scores.get("vo2", {}).get("avg_score", 0)
    if phase_name == "Build" and readiness_score >= 60 and (vo2_count < 1 or vo2_score < 65):
        add(
            "vo2",
            70,
            "Byggfas utan tydlig syrestimuli tappar toppfart och headroom.",
            ["1 kort VO2-session", "full återhämtning före/efter", "undvik dubbla hårda dagar"],
        )

    np_flags = (np_if_analysis or {}).get("flags", [])
    if np_flags:
        if any("IF KONSEKVENT HÖG" in f or "FRONT-LOADING" in f for f in np_flags):
            add(
                "pacing",
                72,
                "Pacing/IF-mönstret tyder på att passen blir hårdare än avsett eller tappar jämnhet.",
                ["ett strikt Z2-pass", "ett pacing-fokuserat långpass", "tydligare nutrition och wattdisciplin"],
            )

    if polarization and polarization.get("mid_pct", 0) > 20:
        add(
            "polarization",
            66,
            "För mycket Z3 minskar kvaliteten i både aerob bas och hårda nyckelpass.",
            ["renare Z2-dagar", "renare Z4+/VO2-dagar", "mindre gråzon"],
        )

    if not priorities:
        add(
            "durability",
            60,
            "Inga akuta svagheter sticker ut - fortsätt bygga robust aerob uthållighet.",
            ["1 långt Z2-pass", "1 kvalitetspass", "övrigt stödjande volym"],
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
            "övriga pass får vara enklare om de ökar genomförbarheten",
            "ta hellre bort filler än att kompromissa bort must-hit-pass",
        ],
        "summary": f"Utvecklingsbehov: {summary}",
    }


def update_block_objective(state: dict, mesocycle: dict, phase: dict,
                           development_needs: dict, race_demands: dict) -> dict:
    today = date.today().isoformat()
    primary = development_needs.get("primary_focus", "durability")
    secondary = development_needs.get("secondary_focus")
    target_name = race_demands.get("target_name", "huvudmål")
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
        "recovery": "absorbera tidigare belastning och återställa kvalitet i nästa nyckelpass",
        "consistency": "öka träffsäkerheten så att viktiga pass faktiskt blir gjorda",
        "calibration": "kalibrera FTP/zoner så att resten av blocket får rätt dos",
        "durability": "bygga tålighet för många timmar i sadeln utan att tappa kvalitet",
        "fueling": "träna tävlingsrelevant nutrition och magtolerans",
        "threshold": "höja sustainable power och effektivitet runt tröskeln",
        "vo2": "öka aerob toppkapacitet och headroom",
        "pacing": "få jämnare belastning och bättre kontroll på intensitet",
        "polarization": "renodla intensitetsfördelningen för bättre adaptation",
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
    if not future: return {"phase": "Grundtraning", "rule": "Grundträning: 1-2 intervallpass/vecka (Z4-Z5), 1 tempopass (Z3), resten Z2. Undvik intervaller ENDAST om HRV=LOW eller TSB < -20."}
    nr = future[0]
    dt = (datetime.strptime(nr["start_date_local"][:10], "%Y-%m-%d").date() - today).days
    nm = nr.get("name","Tävling")
    if dt < 7:  return {"phase": "Race Week", "rule": f"{nm} om {dt}d. Aktivering."}
    if dt < 28: return {"phase": "Taper",     "rule": f"{nm} om {dt}d. -30% volym, behåll intensitet."}
    if dt < 84: return {"phase": "Build",     "rule": f"{nm} om {dt}d. Bygg intensitet."}
    return {"phase": "Base", "rule": f"{nm} om {dt}d. Grundträning: 1-2 intervallpass/vecka (Z4-Z5), 1 tempopass (Z3), resten Z2."}


# ══════════════════════════════════════════════════════════════════════════════
# RACE WEEK PROTOCOL
# ══════════════════════════════════════════════════════════════════════════════

def race_week_protocol(races: list, today: date) -> dict:
    """
    Genererar dagspecifikt race-week-protokoll (sista 7 dagarna före tävling).

    Protokollet:
      -6d: Sista medellånga passet (90min Z2 + 2×5min Z4)
      -5d: Kort Z2 (45min) + benstyrka (lätt, 15min)
      -4d: Vila eller 30min Z1
      -3d: Aktivering: 60min Z2 med 3×3min Z4 (korta, skarpa)
      -2d: Kort Z1 (30min) – spinn benen
      -1d: Vila ELLER 20min Z1 med 3×30s race pace
      Race day: TÄVLING

    Returnerar: dict med protocol, days, race_name, is_active
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
    race_name = race.get("name", "Tävling")

    if days_to_race > 7 or days_to_race <= 0:
        return {"is_active": False, "protocol": [], "race_name": race_name, "days_to_race": days_to_race}

    # Bygg dagspecifikt protokoll
    protocol = []

    day_templates = {
        6: {
            "title": f"🏁 Pre-race: Sista medel-passet ({race_name} om 6d)",
            "type": "VirtualRide", "dur": 90, "slot": "MAIN",
            "steps": [
                {"d": 20, "z": "Z2", "desc": "Uppvärmning"},
                {"d": 30, "z": "Z2", "desc": "Uthållighet – fokusera på känsla"},
                {"d": 5, "z": "Z4", "desc": "Sista Z4-insatsen – race pace"},
                {"d": 5, "z": "Z1", "desc": "Vila"},
                {"d": 5, "z": "Z4", "desc": "Andra Z4-insatsen – kontrollerad"},
                {"d": 15, "z": "Z2", "desc": "Lugnt"},
                {"d": 10, "z": "Z1", "desc": "Nedvarvning"},
            ],
            "desc": "Sista passet med substans. Inga rekord – bara bekräfta formen. Race-nutritionsstrategi: testa CHO-intag."
        },
        5: {
            "title": f"🏁 Pre-race: Lätt cykel + snabb styrka ({race_name} om 5d)",
            "type": "VirtualRide", "dur": 45, "slot": "MAIN",
            "steps": [
                {"d": 15, "z": "Z2", "desc": "Uppvärmning"},
                {"d": 20, "z": "Z2", "desc": "Lugnt – håll benen igång"},
                {"d": 10, "z": "Z1", "desc": "Nedvarvning"},
            ],
            "desc": "Lätt dag. Kort cykel + valfritt 15min mobilitetsövningar. Ingen utmattning."
        },
        4: {
            "title": f"🏁 Pre-race: Vila ({race_name} om 4d)",
            "type": "Rest", "dur": 0, "slot": "MAIN", "steps": [],
            "desc": "Vilodag. Promenad OK. Fokus: sömn, hydration, nutrition. Ladda glykogen."
        },
        3: {
            "title": f"🏁 Pre-race: Aktivering ({race_name} om 3d)",
            "type": "VirtualRide", "dur": 55, "slot": "MAIN",
            "steps": [
                {"d": 15, "z": "Z2", "desc": "Uppvärmning – lätt"},
                {"d": 3, "z": "Z4", "desc": "Aktivering 1 – väck benen"},
                {"d": 3, "z": "Z1", "desc": "Vila"},
                {"d": 3, "z": "Z4", "desc": "Aktivering 2 – skarpt men kort"},
                {"d": 3, "z": "Z1", "desc": "Vila"},
                {"d": 3, "z": "Z4", "desc": "Aktivering 3 – sista gången Z4 innan race"},
                {"d": 15, "z": "Z2", "desc": "Lugnt tillbaka"},
                {"d": 10, "z": "Z1", "desc": "Nedvarvning"},
            ],
            "desc": "AKTIVERING! Korta, skarpa Z4-insatser väcker nervsystemet. Max ansträngning 7/10. Ej tungt."
        },
        2: {
            "title": f"🏁 Pre-race: Spinn ({race_name} om 2d)",
            "type": "VirtualRide", "dur": 30, "slot": "MAIN",
            "steps": [
                {"d": 10, "z": "Z1", "desc": "Lugnt"},
                {"d": 10, "z": "Z2", "desc": "Lätt tryck – inte mer"},
                {"d": 10, "z": "Z1", "desc": "Nedvarvning"},
            ],
            "desc": "Bara spinna benen. 30 min max. Spara allt till tävlingen."
        },
        1: {
            "title": f"🏁 Pre-race: Vila/Kort aktivering ({race_name} IMORGON!)",
            "type": "VirtualRide", "dur": 20, "slot": "MAIN",
            "steps": [
                {"d": 10, "z": "Z1", "desc": "Extremt lugnt"},
                {"d": 1, "z": "Z5", "desc": "30s sprint – race-pace-påminnelse"},
                {"d": 2, "z": "Z1", "desc": "Vila"},
                {"d": 1, "z": "Z5", "desc": "30s sprint"},
                {"d": 6, "z": "Z1", "desc": "Nedvarvning"},
            ],
            "desc": "Valfritt! 20 min med 2×30s sprints. Packa väskan. Kolla utrustning. Sov 8h+."
        },
        0: {
            "title": f"🏁 TÄVLINGSDAG: {race_name}!",
            "type": "Ride", "dur": 0, "slot": "MAIN", "steps": [],
            "desc": f"TÄVLINGSDAG! {race_name}. Uppvärmning 15-20min. Ät frukost 3h före. 90g CHO/h under. Lycka till! 💪"
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
    """Formaterar race-week-protokollet för AI-prompten."""
    if not rw.get("is_active"):
        return ""

    lines = [
        f"🏁 RACE WEEK PROTOCOL – {rw['race_name']} ({rw['race_date']})",
        f"  Dagar kvar: {rw['days_to_race']}",
        "",
        "  ⚠️ FÖLJ DETTA PROTOKOLL EXAKT. Inga avvikelser tillåtna.",
        "  Överstyr alla andra regler (mesocykel, passbibliotek, etc).",
        "",
    ]
    for p in rw["protocol"]:
        steps_text = " → ".join(f"{s['d']}min {s['z']}" for s in p.get("steps", []))
        lines.append(f"  {p['date']} (-{p['days_before']}d): {p['title']}")
        lines.append(f"    {p['type']} | {p['dur']}min")
        if steps_text:
            lines.append(f"    Steg: {steps_text}")
        lines.append(f"    {p['desc']}")
        lines.append("")

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# RETURN TO PLAY
# ══════════════════════════════════════════════════════════════════════════════

def check_return_to_play(activities: list, today: date) -> dict:
    """
    Kollar om atleten haft 5 eller fler dagar i rad helt utan träning
    och triggar i så fall ett Return to Play-protokoll.
    """
    days_off = 0
    for i in range(1, 14):
        check_date = (today - timedelta(days=i)).isoformat()
        daily_acts = [a for a in activities if a.get("start_date_local", "")[:10] == check_date]
        moving_time = sum((a.get("moving_time") or a.get("elapsed_time") or 0) for a in daily_acts)
        tss = sum((a.get("icu_training_load", 0) or 0) for a in daily_acts)
        if moving_time < 900 and tss < 10:  # < 15 min OCH < 10 TSS räknas som vilodag
            daily_acts = [a for a in activities if a.get("start_date_local", "")[:10] == check_date and a.get("type") not in ("Rest", "Note")]
        
        if not daily_acts:
            days_off += 1
            continue
            
        total_time = sum((a.get("moving_time") or a.get("elapsed_time") or 0) for a in daily_acts)
        total_tss  = sum((a.get("icu_training_load") or 0) for a in daily_acts)
        has_rpe    = any((a.get("perceived_exertion") or 0) > 0 for a in daily_acts)
        has_strength = any(a.get("type", "") in ("WeightTraining", "Strength") for a in daily_acts)
        
        if total_time >= 900 or total_tss >= 10 or has_rpe or has_strength:
            break  # Träning loggad och giltig -> bryt vilodagskedjan!
        else:
            break
            days_off += 1  # Aktiviteten var helt obetydlig (t.ex. 5 min promenad)
    return {"is_active": days_off >= 5, "days_off": days_off}

# ══════════════════════════════════════════════════════════════════════════════
# TAPER QUALITY SCORE
# ══════════════════════════════════════════════════════════════════════════════

def taper_quality_score(fitness_history: list, race_date: Optional[date],
                        taper_days: int = 14) -> dict:
    """
    Mäter om tapern genomförs korrekt:
    - CTL bör sjunka 5-10% under taper
    - TSB bör stiga till +5 till +15 på tävlingsdagen
    - ATL bör sjunka snabbt (30-50%)

    Returnerar:
      is_in_taper:     bool
      taper_day:       int (vilken dag av tapern)
      ctl_drop_pct:    faktisk CTL-minskning i %
      tsb_rise:        TSB-förändring
      atl_drop_pct:    ATL-minskning i %
      score:           0-100 kvalitetspoäng
      verdict:         textbedömning
      adjustments:     lista med korrigeringar om det går fel
    """
    if not race_date or not fitness_history:
        return {"is_in_taper": False, "score": None}

    today = date.today()
    days_to_race = (race_date - today).days

    if days_to_race > taper_days or days_to_race < 0:
        return {"is_in_taper": False, "score": None, "days_to_race": days_to_race}

    taper_day = taper_days - days_to_race  # Dag 1, 2, ... av tapern
    taper_progress = taper_day / taper_days  # 0.0 → 1.0

    # CTL vid taper-start vs nu
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

    # Förväntade värden vid denna punkt i tapern
    expected_ctl_drop = taper_progress * 8  # Förväntad 5-10% CTL-drop vid slutet
    expected_atl_drop = taper_progress * 40  # ATL bör sjunka 30-50%
    expected_tsb = taper_progress * 15  # TSB bör stiga ~15 poäng

    # Poängsättning (0-100)
    score = 0

    # CTL-drop: 5-10% = perfekt, <3% = för lite vila, >15% = för mycket
    if 3 <= ctl_drop_pct <= 12:
        score += 35
    elif ctl_drop_pct < 3:
        score += max(0, 35 - (3 - ctl_drop_pct) * 10)
    else:
        score += max(0, 35 - (ctl_drop_pct - 12) * 5)

    # ATL-drop: 25-50% = bra
    if 20 <= atl_drop_pct <= 55:
        score += 30
    elif atl_drop_pct < 20:
        score += max(0, 30 - (20 - atl_drop_pct))
    else:
        score += max(0, 30 - (atl_drop_pct - 55))

    # TSB-rise: bör vara positiv och stigande
    if tsb_rise > 0:
        score += min(35, round(tsb_rise * 3))
    else:
        score += max(0, 35 + round(tsb_rise * 3))

    score = max(0, min(100, score))

    # Verdict
    if score >= 80:
        verdict = "✅ Utmärkt taper! Form och fräschhet byggs optimalt."
    elif score >= 60:
        verdict = "🟡 OK taper, men rum för förbättring."
    elif score >= 40:
        verdict = "🟠 Tapern funkar inte optimalt."
    else:
        verdict = "🔴 Tapern misslyckas – åtgärda omedelbart."

    # Korrigeringar
    adjustments = []
    if ctl_drop_pct < 2 and taper_day >= 5:
        adjustments.append("CTL sjunker för långsamt – du tränar för hårt under tapern. Sänk volymen mer.")
    if ctl_drop_pct > 15:
        adjustments.append("CTL sjunker för snabbt – du vilar för mycket. Behåll korta aktiveringspass.")
    if atl_drop_pct < 15 and taper_day >= 7:
        adjustments.append("ATL sjunker inte tillräckligt – fortfarande för hög akut belastning.")
    if tsb_now < -5 and days_to_race < 5:
        adjustments.append(f"⚠️ TSB fortfarande negativ ({tsb_now}) med {days_to_race}d kvar! Vila mer.")
    if tsb_now > 25 and days_to_race > 3:
        adjustments.append("TSB väldigt hög – risk att tappa skärpa. Lägg in korta aktiveringspass.")

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
            f"Taper dag {taper_day}/{taper_days} | Score: {score}/100 {verdict}\n"
            f"  CTL: {round(ctl_at_start)}→{round(ctl_now)} ({ctl_drop_pct:+.1f}%) | "
            f"ATL: {round(atl_at_start)}→{round(atl_now)} ({atl_drop_pct:+.1f}%) | "
            f"TSB: {round(tsb_at_start)}→{round(tsb_now)} ({tsb_rise:+.1f})"
            + ("\n  Korrigeringar: " + " ".join(adjustments) if adjustments else "")
        ),
    }


def parse_zones(athlete):
    lines = []
    names = {"Ride":"Cykling","Run":"Löpning","NordicSki":"Längdskidor","RollerSki":"Rullskidor","VirtualRide":"Zwift"}
    for ss in athlete.get("sportSettings", []):
        stypes = ss.get("types", []) if isinstance(ss.get("types"), list) else [ss.get("type")]
        t_names = [names.get(x, x) for x in stypes if x]
        t = "/".join(t_names) if t_names else "Standardzoner"
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
            if zs: lines.append(f"    Effektzoner: {zs}")
        if lthr and hr_z:
            zs = " | ".join(f"{z.get('name','Z'+str(i+1))}: {round(z.get('min',0)*lthr/100)}-{round(z.get('max',0)*lthr/100)}bpm"
                            for i,z in enumerate(hr_z) if z.get("min") and z.get("max"))
            if zs: lines.append(f"    HR-zoner: {zs}")
    return "\n".join(lines) if lines else "  Inga sportinställningar hittades."

def env_nutrition(temp_max, duration_min, first_zone):
    advice = []
    low_int = first_zone in ("Z1","Z2","Zon 1","Zon 2")
    if temp_max > 25: advice.append("VÄRME: +200ml/h. Elektrolyter (>=800mg Na/l).")
    elif temp_max < 0: advice.append("KYLA: Drick enligt schema. Håll drycken ljummen.")
    if low_int and duration_min < 90: advice.append("TRAIN LOW: Möjlighet att köra fastande för fettadaptering.")
    return advice

def biometric_vetoes(hrv, life_stress):
    rules = []
    if hrv["state"] == "LOW" or hrv["stability"] == "UNSTABLE":
        rules.append("HRV_LOW: Inga pass över Z2. Konvertera till Z1/vila.")
    elif hrv["state"] == "SLIGHTLY_LOW":
        rules.append("HRV_SLIGHTLY_LOW: Undvik Z4+.")
    if life_stress >= 4:
        rules.append("LIVSSTRESS_HÖG: Inga intervaller över tröskel. Sänk IF med 15%.")
    return rules

# ══════════════════════════════════════════════════════════════════════════════
# YESTERDAY ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════

def analyze_yesterday(yesterday_planned, yesterday_actuals, activities) -> str:
    """
    Bygger en detaljerad analys av gårdagens planerade vs faktiska pass
    som skickas till AI:n för feedback.
    """
    yesterday_date = (date.today() - timedelta(days=1)).isoformat()
    if not yesterday_planned or not is_ai_generated(yesterday_planned):
        if yesterday_actuals:
            a = yesterday_actuals[0]
            return (
                f"GÅRDAGEN ({yesterday_date}): Inget AI-planerat pass igår, men aktivitet registrerad:\n"
                f"  Typ: {a.get('type','?')} | {round((a.get('moving_time',0) or 0)/60)}min | "
                f"TSS: {a.get('icu_training_load','?')} | HR: {a.get('average_heartrate','?')}bpm | "
                f"RPE: {a.get('perceived_exertion','?')}"
            )
        # Inget planerat, ingen aktivitet – inget att ge feedback om
        return ""

    planned_name = yesterday_planned.get("name", "?")
    planned_type = yesterday_planned.get("type", "?")
    planned_dur = round((yesterday_planned.get("moving_time", 0) or 0) / 60)
    planned_desc = (yesterday_planned.get("description", "") or "").replace(AI_TAG, "").strip()[:500]

    if not yesterday_actuals:
        return (
            f"MISSAT PASS IGÅR ({yesterday_date}):\n"
            f"  Planerat: {planned_name} ({planned_type}, {planned_dur}min)\n"
            f"  Beskrivning: {planned_desc[:200]}\n"
            f"  Faktiskt: Ingenting registrerat.\n"
            f"  → Ge feedback: Vad missades? Är det en compliance-trend?"
        )

    lines = [f"GÅRDAGENS ({yesterday_date}) PLANERADE PASS:\n  {planned_name} ({planned_type}, {planned_dur}min)"]
    lines.append(f"  Plan-beskrivning: {planned_desc[:300]}")
    lines.append(f"\nGÅRDAGENS FAKTISKA AKTIVITET(ER):")

    for a in yesterday_actuals:
        actual_dur = round((a.get("moving_time", 0) or 0) / 60)
        actual_dist = round((a.get("distance", 0) or 0) / 1000, 1)
        lines.append(
            f"  {a.get('type','?')} | {actual_dur}min | {actual_dist}km | "
            f"TSS: {fmt(a.get('icu_training_load'))} | "
            f"HR: {fmt(a.get('average_heartrate'),'bpm')} (max {fmt(a.get('max_heartrate'),'bpm')}) | "
            f"NP: {fmt(a.get('icu_weighted_avg_watts'),'W')} | IF: {fmt(a.get('icu_intensity'))} | "
            f"RPE: {fmt(a.get('perceived_exertion'))} | Känsla: {fmt(a.get('feel'))}/5"
        )

        # Jämförelseanalys
        dur_diff = actual_dur - planned_dur
        if abs(dur_diff) > 10:
            lines.append(f"  Δ Duration: {dur_diff:+d}min vs planerat")

        # Zonanalys
        pz = format_zone_times(a.get("icu_zone_times")); hz = format_zone_times(a.get("icu_hr_zone_times"))
        if pz: lines.append(f"  Effektzoner: {pz}")
        if hz: lines.append(f"  HR-zoner: {hz}")

    lines.append(
        "\n  → Ge feedback: Följdes planen? Rätt intensitet? Vad kan förbättras? "
        "Var nutritionen tillräcklig? Konkreta tips."
    )

    return "\n".join(lines)



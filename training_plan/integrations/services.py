from training_plan.core.common import *
from training_plan.engine.analysis import *
from training_plan.engine.planning import *
from training_plan.engine.postprocess import estimate_tss_coggan

def save_daily_note_to_icu(plan, changes):
    """
    Sparar dagens sammanfattning som en NOTE idag, och gårdagens 
    feedback som en separat NOTE igår.
    """
    today_date = date.today()
    today_str = today_date.isoformat()
    yesterday_str = (today_date - timedelta(days=1)).isoformat()
    
    # --- Bygg innehåll för IDAG ---
    lines_today = ["🤖 DAGENS SAMMANFATTNING:"]
    lines_today.append(plan.summary)
        
    if changes:
        lines_today.append("")
        lines_today.append("🔧 JUSTERINGAR (Post-processing):")
        for c in changes:
            lines_today.append(f"  • {c}")
    note_today = "\n".join(lines_today)

    # --- Bygg innehåll för IGÅR ---
    note_yesterday = None
    if plan.yesterday_feedback:
            note_yesterday = f"📝 COACH-FEEDBACK:\n{plan.yesterday_feedback}"
    
    try:
        # Rensa tidigare skapade loggar från idag OCH igår (för att undvika dubbletter)
        existing = icu_get(f"/athlete/{ATHLETE_ID}/events", {
            "oldest": yesterday_str,
            "newest": (today_date + timedelta(days=1)).isoformat(),
        })
        
        for e in existing:
            if e.get("category") == "NOTE":
                date_local = e.get("start_date_local", "")[:10]
                
                if e.get("name") == "🤖 AI Coach Logg" and date_local == today_str:
                    requests.put(
                        f"{BASE}/athlete/{ATHLETE_ID}/events/bulk-delete",
                        auth=AUTH, timeout=15, json=[{"id": e["id"]}],
                    ).raise_for_status()
                
                if e.get("name") == "📝 Coach-feedback" and date_local == yesterday_str:
                    requests.put(
                        f"{BASE}/athlete/{ATHLETE_ID}/events/bulk-delete",
                        auth=AUTH, timeout=15, json=[{"id": e["id"]}],
                    ).raise_for_status()

        # 1. Spara Dagens Logg (På dagens datum kl 05:00)
        requests.post(f"{BASE}/athlete/{ATHLETE_ID}/events", auth=AUTH, timeout=10, json={
            "category": "NOTE",
            "start_date_local": today_str + "T05:00:00",
            "name": "🤖 AI Coach Logg",
            "description": note_today + f"\n\n{AI_TAG}",
            "color": "#8E44AD"  # Lila färg
        }).raise_for_status()

        # 2. Spara Gårdagens Feedback (På gårdagens datum kl 18:00)
        if note_yesterday:
            requests.post(f"{BASE}/athlete/{ATHLETE_ID}/events", auth=AUTH, timeout=10, json={
                "category": "NOTE",
                "start_date_local": yesterday_str + "T18:00:00",
                "name": "📝 Coach-feedback",
                "description": note_yesterday + f"\n\n{AI_TAG}",
                "color": "#8E44AD"  # Lila färg
            }).raise_for_status()
            
        log.info("📝 Daglig coach-logg och feedback sparad uppdelat i intervals.icu")
    except Exception as e:
        log.warning(f"Kunde inte spara daglig coach-logg: {e}")

def generate_weekly_report(activities: list, wellness: list, fitness: list,
                           mesocycle: dict, trajectory: dict,
                           compliance: dict, ftp_check: dict,
                           acwr_trend: dict, taper_score: dict,
                           ai_feedback: str = "",
                           motivation: dict = None,
                           development_needs: dict = None,
                           block_objective: dict = None,
                           race_demands: dict = None,
                           session_quality: dict = None,
                           coach_confidence: dict = None,
                           polarization: dict = None) -> str:
    today = date.today()
    week_start = today - timedelta(days=today.weekday() + 7)
    week_end   = week_start + timedelta(days=7)
    week_end_incl = week_start + timedelta(days=6)
    week_acts = [
        a for a in activities
        if _safe_date_str(a) and week_start.isoformat() <= _safe_date_str(a) < week_end.isoformat()
    ]
    total_min   = sum((a.get("moving_time", 0) or 0) / 60 for a in week_acts)
    total_tss   = sum((a.get("icu_training_load", 0) or 0) for a in week_acts)
    total_dist  = sum((a.get("distance", 0) or 0) / 1000 for a in week_acts)
    zone_mins = [0.0] * 7
    for a in week_acts:
        hr_zones = a.get("icu_hr_zone_times") or a.get("icu_zone_times") or []
        for i, z in enumerate(hr_zones):
            if isinstance(z, dict):
                secs = z.get("secs", 0) or z.get("seconds", 0)
            elif isinstance(z, (int, float)):
                secs = z
            else:
                continue
            if i < 7:
                zone_mins[i] += secs / 60
    total_zone_min = sum(zone_mins) or 1
    zone_pct = [round(z / total_zone_min * 100) for z in zone_mins]
    low_pct  = zone_pct[0] + zone_pct[1] if len(zone_pct) > 1 else 0
    mid_pct  = zone_pct[2] if len(zone_pct) > 2 else 0
    high_pct = sum(zone_pct[3:]) if len(zone_pct) > 3 else 0
    if low_pct >= 75 and mid_pct <= 15:
        polar_verdict = "✅ Bra polariserad fördelning"
    elif mid_pct > 20:
        polar_verdict = "⚠️ För mycket Z3 (svartzon) – mer ren Z2 eller ren Z4+"
    else:
        polar_verdict = "Neutral fördelning"
    ctl_values = [f.get("ctl", 0) for f in fitness[-14:] if f.get("ctl") is not None]
    ctl_delta = round(ctl_values[-1] - ctl_values[-8], 1) if len(ctl_values) >= 8 else 0
    sport_min = {}
    for a in week_acts:
        t = a.get("type", "Other")
        sport_min[t] = sport_min.get(t, 0) + (a.get("moving_time", 0) or 0) / 60
    sport_lines = " | ".join(f"{k}: {round(v)}min" for k, v in sorted(sport_min.items(), key=lambda x: -x[1]))
    week_wellness = [
        w for w in wellness
        if w.get("id", "")[:10] >= week_start.isoformat()
        and w.get("id", "")[:10] < week_end.isoformat()
    ]
    sleep_vals = [w.get("sleepSecs", 0) / 3600 for w in week_wellness if w.get("sleepSecs")]
    avg_sleep = round(sum(sleep_vals) / len(sleep_vals), 1) if sleep_vals else "N/A"
    hrv_vals = [w.get("hrv") for w in week_wellness if w.get("hrv")]
    avg_hrv = round(sum(hrv_vals) / len(hrv_vals)) if hrv_vals else "N/A"
    report = f"""━━━ VECKORAPPORT {week_start.isoformat()} → {week_end_incl.isoformat()} ━━━

📊 SAMMANFATTNING
  Tid:      {round(total_min)}min ({round(total_min/60, 1)}h)
  TSS:      {round(total_tss)}
  Distans:  {round(total_dist, 1)}km
  Pass:     {len(week_acts)}st
  CTL:      {round(ctl_values[-1]) if ctl_values else 'N/A'} (Δ{ctl_delta:+.1f} senaste veckan)"""

    if ai_feedback:
        report += f"\n\n🤖 COACH-FEEDBACK\n  {ai_feedback}"

    report += f"""

🏋️ SPORTFÖRDELNING
  {sport_lines or 'Ingen data'}

📈 ZONDISTRIBUTION
  Z1-Z2 (låg): {low_pct}% | Z3 (medel): {mid_pct}% | Z4+ (hög): {high_pct}%
  {polar_verdict}

💤 ÅTERHÄMTNING
  Sömn-snitt: {avg_sleep}h | HRV-snitt: {avg_hrv}ms

🔄 MESOCYKEL
  Block {mesocycle['block_number']}, Vecka {mesocycle['week_in_block']}/4
  {'🟡 DELOAD-VECKA' if mesocycle['is_deload'] else f'Laddningsvecka ({mesocycle["load_factor"]:.0%})'}
  {mesocycle['deload_reason'] if mesocycle['deload_reason'] else ''}

🎯 CTL-TRAJEKTORIA
  {trajectory['message'] if trajectory.get('has_target') else 'Ingen A-tävling schemalagd.'}
"""
    if trajectory.get("milestones"):
        report += "  Milstolpar:\n"
        for m in trajectory["milestones"]:
            report += f"    +{m['weeks']}v: CTL {m['projected_ctl']}\n"

    if acwr_trend and acwr_trend.get("summary"):
        report += f"""
📈 ACWR-TREND
  {acwr_trend['summary']}
"""

    if taper_score and taper_score.get("is_in_taper"):
        score = int(taper_score.get('score', 0))
        length = 20
        filled_length = int(length * score / 100)
        bar = '█' * filled_length + '░' * (length - filled_length)
        report += f"""
📉 TAPER-KVALITET (Dag {taper_score['taper_day']}/{taper_score['taper_days']})
  Poäng: {score}/100 {bar}
  {taper_score.get('verdict', '')}
  Detaljer: CTL {taper_score.get('ctl_drop_pct'):+.1f}%, ATL {taper_score.get('atl_drop_pct'):+.1f}%, TSB Δ{taper_score.get('tsb_rise'):+.1f}
  Justeringar: {' '.join(taper_score.get('adjustments', [])) or 'Inga, allt ser bra ut.'}
"""

    report += f"""
📋 COMPLIANCE
  {compliance['summary']}
"""
    if motivation:
        report += f"""
🧠 MOTIVATION & PSYKOLOGI
  {motivation['summary']}
"""
        if motivation["state"] in ("BURNOUT_RISK", "FATIGUED"):
            report += f"  ⚠️ Prioritera återhämtning och variation nästa vecka.\n"

    if block_objective:
        report += f"""
🎯 BLOCKMÅL
  Primärt fokus: {block_objective.get('primary_focus', '?')}
  Sekundärt fokus: {block_objective.get('secondary_focus') or 'Inget sekundärt fokus'}
  Objective: {block_objective.get('objective', '')}
  Must-hit: {' | '.join(block_objective.get('must_hit_sessions', [])) or 'Inga definierade'}
"""

    if development_needs:
        prio_lines = []
        for p in development_needs.get("priorities", [])[:3]:
            prio_lines.append(f"  - {p['area']} ({p['score']}): {p['why']}")
        report += "\n📌 UTVECKLINGSBEHOV\n" + ("\n".join(prio_lines) if prio_lines else "  Inga tydliga utvecklingsbehov identifierade.")

    if race_demands:
        report += f"""

🏁 RACE DEMANDS
  {race_demands.get('summary', '')}
  {' | '.join(race_demands.get('markers', [])[:4]) if race_demands.get('markers') else 'Inga markörer'}
"""

    if session_quality:
        report += f"""
🛠️ PASSKVALITET
  {session_quality.get('summary', '')}
"""
        if session_quality.get("recent_sessions"):
            report += "\n" + "\n".join(session_quality["recent_sessions"][:4]) + "\n"

    if polarization:
        report += f"""
⚖️ POLARISATION
  {polarization.get('summary', '')}
"""

    if coach_confidence:
        report += f"""
🧭 COACH CONFIDENCE
  {coach_confidence.get('summary', '')}
"""

    report += f"""
🔬 FTP-STATUS
  {ftp_check['recommendation']}
  {ftp_check.get('suggested_protocol', '')}
"""
    return report.strip()


def save_weekly_report_to_icu(report: str):
    today = date.today()
    last_monday = today - timedelta(days=today.weekday() + 7)
    last_sunday = last_monday + timedelta(days=6)
    week_num = last_monday.isocalendar()[1]
    try:
        existing = icu_get(f"/athlete/{ATHLETE_ID}/events", {
            "oldest": last_monday.isoformat(),
            "newest": (today + timedelta(days=1)).isoformat(),
        })
        for e in existing:
            if REPORT_TAG in (e.get("description") or ""):
                log.info("📊 Veckorapport finns redan för denna vecka, hoppar över.")
                return
        requests.post(f"{BASE}/athlete/{ATHLETE_ID}/events", auth=AUTH, timeout=10, json={
            "category": "NOTE",
            "start_date_local": last_sunday.isoformat() + "T23:50:00",
            "name": f"📊 Veckorapport v{week_num}",
            "description": report + f"\n\n{REPORT_TAG}",
            "color": "#4A90D9",
        }).raise_for_status()
        log.info(f"📊 Veckorapport sparad i intervals.icu ({last_sunday.isoformat()})")
    except Exception as e:
        log.warning(f"Kunde inte spara veckorapport: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# INTERVALS.ICU – HÄMTNING
# ══════════════════════════════════════════════════════════════════════════════

def icu_get(path, params=None):
    r = requests.get(f"{BASE}{path}", auth=AUTH, params=params or {}, timeout=15)
    r.raise_for_status()
    return r.json()

def fetch_athlete():
    return icu_get(f"/athlete/{ATHLETE_ID}")

def fetch_activities(days):
    return icu_get(f"/athlete/{ATHLETE_ID}/activities", {
        "oldest": (date.today() - timedelta(days=days)).isoformat(),
        "fields": ("name,type,start_date_local,distance,moving_time,elapsed_time,"
                   "icu_training_load,average_heartrate,"
                   "max_heartrate,icu_weighted_avg_watts,icu_intensity,"
                   "icu_zone_times,icu_hr_zone_times,perceived_exertion,feel"),
    })

def fetch_wellness(days):
    return icu_get(f"/athlete/{ATHLETE_ID}/wellness", {
        "oldest": (date.today() - timedelta(days=days)).isoformat(),
        "newest": date.today().isoformat(),
    })

def fetch_fitness(days):
    wellness = icu_get(f"/athlete/{ATHLETE_ID}/wellness", {
        "oldest": (date.today() - timedelta(days=days)).isoformat(),
        "newest": date.today().isoformat(),
    })
    fitness = []
    for w in wellness:
        atl = w.get("icu_atl") or w.get("atl")
        ctl = w.get("icu_ctl") or w.get("ctl")
        if atl is not None and ctl is not None:
            fitness.append({
                "date": w.get("id", ""),
                "atl":  atl,
                "ctl":  ctl,
                "tsb":  ctl - atl,
            })
    return fitness

def fetch_planned_workouts(horizon):
    return icu_get(f"/athlete/{ATHLETE_ID}/events", {
        "oldest": date.today().isoformat(),
        "newest": (date.today() + timedelta(days=horizon)).isoformat(),
    })

def fetch_all_planned_events(days_back=28, days_forward=0):
    return icu_get(f"/athlete/{ATHLETE_ID}/events", {
        "oldest": (date.today() - timedelta(days=days_back)).isoformat(),
        "newest": (date.today() + timedelta(days=days_forward)).isoformat(),
    })

def fetch_races(days_ahead=180):
    try:
        evts = icu_get(f"/athlete/{ATHLETE_ID}/events", {
            "oldest": date.today().isoformat(),
            "newest": (date.today() + timedelta(days=days_ahead)).isoformat(),
        })
        return [e for e in evts if e.get("category") == "RACE"]
    except Exception:
        return []

def get_taper_config(races: list, today: date) -> dict:
    """Hittar nästa tävling och bestämmer taper-längd baserat på prioritet i namnet (A/B/C)."""
    future_races = sorted([
        r for r in races
        if datetime.strptime(r.get("start_date_local", "2099-01-01")[:10], "%Y-%m-%d").date() >= today
    ], key=lambda r: r.get("start_date_local", ""))
    if not future_races:
        return {"race": None, "taper_days": 14, "race_date": None}
    next_race = future_races[0]
    name_lower = next_race.get("name", "").lower()
    race_date_obj = datetime.strptime(next_race["start_date_local"][:10], "%Y-%m-%d").date()
    taper_days = 3 if "c:" in name_lower else (7 if "b:" in name_lower else 14)
    return {"race": next_race, "taper_days": taper_days, "race_date": race_date_obj}

def fetch_yesterday_actual(activities):
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    return [a for a in activities if a.get("start_date_local","")[:10] == yesterday]

# ══════════════════════════════════════════════════════════════════════════════
# INTERVALS.ICU – SPARNING
# ══════════════════════════════════════════════════════════════════════════════

def is_ai_generated(w):
    return AI_TAG in (w.get("description") or "")

def _parse_local_event_datetime(start_date_local: str) -> Optional[datetime]:
    if not start_date_local:
        return None
    try:
        return datetime.fromisoformat(start_date_local)
    except ValueError:
        pass
    try:
        return datetime.strptime(start_date_local[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        pass
    try:
        return datetime.strptime(start_date_local[:10], "%Y-%m-%d")
    except ValueError:
        return None

def _stockholm_now_naive() -> datetime:
    return datetime.now(ZoneInfo("Europe/Stockholm")).replace(tzinfo=None)

def event_has_started(event: dict, now: Optional[datetime] = None) -> bool:
    start_dt = _parse_local_event_datetime(event.get("start_date_local", ""))
    if start_dt is None:
        return False
    return start_dt <= (now or _stockholm_now_naive())

def plan_day_has_started(day: PlanDay, now: Optional[datetime] = None) -> bool:
    start_dt = _parse_local_event_datetime(day.date + _slot_time(day.slot))
    if start_dt is None:
        return False
    return start_dt <= (now or _stockholm_now_naive())

def delete_ai_workouts(workouts, now: Optional[datetime] = None):
    n = 0
    for w in workouts:
        if is_ai_generated(w) and not event_has_started(w, now):
            try:
                requests.put(
                    f"{BASE}/athlete/{ATHLETE_ID}/events/bulk-delete",
                    auth=AUTH, timeout=15,
                    json=[{"id": w["id"]}],
                ).raise_for_status()
                n += 1
            except Exception as e:
                log.warning(f"Kunde inte ta bort {w.get('id')}: {e}")
    return n

def update_manual_nutrition(workout, nutrition):
    desc  = workout.get("description") or ""
    lines = [l for l in desc.split("\n") if not l.startswith(NUTRITION_TAG)]
    new   = "\n".join(lines).strip() + f"\n\n{NUTRITION_TAG} {nutrition}"
    try:
        requests.put(f"{BASE}/athlete/{ATHLETE_ID}/events/{workout['id']}",
                     auth=AUTH, json={"description": new.strip()}, timeout=10).raise_for_status()
    except Exception as e:
        log.warning(f"Kunde inte uppdatera nutrition: {e}")

def _slot_time(slot: str) -> str:
    """AM→07:00, PM→17:00, MAIN→16:00 (eftermiddag som default)."""
    return {"AM": "T07:00:00", "PM": "T17:00:00"}.get(slot, "T16:00:00")

def save_event(day: PlanDay):
    name = day.title if day.title and day.title != "Vila" else "🛌 Vila"
    requests.post(f"{BASE}/athlete/{ATHLETE_ID}/events", auth=AUTH, timeout=10, json={
        "category": "NOTE",
        "start_date_local": day.date + _slot_time(day.slot),
        "name": name,
        "description": day.description + f"\n\n{AI_TAG} ({get_used_model()})",
        "color": "#95A5A6",  # grå för vilodagar
    }).raise_for_status()

# Zon → % av tröskeleffekt (cykling) / % av tröskelpuls (löpning, rullskidor, m.fl.)
_ZONE_POWER_PCT   = {"Z1": 55, "Z2": 68, "Z3": 83, "Z4": 100, "Z5": 112, "Z6": 130, "Z7": 150}
_ZONE_STEP_LABELS = {"Z1": "Recovery", "Z2": "Aerobic", "Z3": "Sweet spot",
                     "Z4": "Threshold", "Z5": "VO2max", "Z6": "Anaerobic", "Z7": "Sprint"}

# Sporter med effektmätare – använder %ftp i steg-text (övriga använder %lthr)
# Konfigurerbart via POWER_SPORTS i .env, t.ex.: VirtualRide,MountainBikeRide
_POWER_SPORTS = {
    s.strip() for s in os.getenv("POWER_SPORTS", "VirtualRide").split(",") if s.strip()
}

def _step_type(desc: str) -> str:
    d = desc.lower()
    if "uppvärmning" in d or "warm" in d:
        return "Warmup"
    if "nedvarvning" in d or "cool" in d or "varv ner" in d:
        return "Cooldown"
    return "SteadyState"


def build_workout_step_text(steps: list[WorkoutStep], sport: str) -> str:
    """Bygger intervals.icu parsningsbar step-text för description-fältet.

    Format som intervals.icu förstår:
      - Xm Y% Warmup
      Nx
      - Xm Y%
      - Xm Y%
      - Xm Y% Cooldown
    """
    use_power = sport in _POWER_SPORTS

    def pct(zone: str) -> str:
        z = zone.upper()
        if use_power:
            return f"{_ZONE_POWER_PCT.get(z, 68)}%"
        # HR-sporter: använd intervals.icu hr_zone-format (t.ex. "Z2 HR")
        return f"{z} HR"

    lines: list[str] = []
    start = 0
    end = len(steps)

    # Ledande uppvärmningssteg
    while start < end and _step_type(steps[start].description) == "Warmup":
        s = steps[start]
        lines.append(f"- {s.duration_min}m {pct(s.zone)} Warmup")
        start += 1

    # Avslutande nedvarvningssteg (buffras, läggs till sist)
    cooldown_lines: list[str] = []
    while end > start and _step_type(steps[end - 1].description) == "Cooldown":
        end -= 1
        s = steps[end]
        cooldown_lines.insert(0, f"- {s.duration_min}m {pct(s.zone)} Cooldown")

    # Mittensteg – lista varje steg individuellt (Nx-syntax stöds ej av intervals.icu)
    for s in steps[start:end]:
        label = _ZONE_STEP_LABELS.get(s.zone.upper(), "")
        lines.append(f"- {s.duration_min}m {pct(s.zone)} {label}".rstrip())

    lines.extend(cooldown_lines)
    return "\n".join(lines)

_ZONE_HR_NUM = {"Z1": 1, "Z2": 2, "Z3": 3, "Z4": 4, "Z5": 5, "Z6": 6, "Z7": 7}

def build_hr_workout_doc(steps: list[WorkoutStep]) -> dict:
    """Bygger workout_doc med hr_zone-format för icke-power-sporter."""
    return {"steps": [
        {"duration": s.duration_min * 60,
         "hr": {"value": _ZONE_HR_NUM.get(s.zone.upper(), 2), "units": "hr_zone"}}
        for s in steps
    ]}

def _workout_color(day: PlanDay) -> str:
    """Returnerar hex-färg baserat på passintensitet."""
    if day.intervals_type == "WeightTraining":
        return "#8E44AD"   # Lila
    if not day.workout_steps:
        return "#3498DB"   # Blå standard
    zones = {s.zone.upper() for s in day.workout_steps}
    if zones & {"Z6", "Z7"}:
        return "#C0392B"   # Mörkröd – anaerob
    if zones & {"Z5"}:
        return "#E74C3C"   # Röd – VO2max
    if zones & {"Z4"}:
        return "#E67E22"   # Orange – tröskel
    if zones & {"Z3"}:
        return "#F1C40F"   # Gul – tempo
    return "#27AE60"       # Grön – Z1/Z2

def save_workout(day: PlanDay, athlete: dict | None = None):
    if day.strength_steps:
        step_text = "\n".join(
            f"{s.exercise}: {s.sets}x{s.reps}" + (f", vila {s.rest_sec}s" if s.rest_sec else "") + (f" - {s.notes}" if s.notes else "")
            for s in day.strength_steps)
    elif day.workout_steps and day.intervals_type not in ("WeightTraining", "Rest"):
        step_text = build_workout_step_text(day.workout_steps, day.intervals_type)
        log.debug(f"step_text {day.date}: {len(day.workout_steps)} steg")
    else:
        step_text = ""
    nutr_block = f"{NUTRITION_TAG} {day.nutrition}" if day.nutrition else ""
    # Steg-rader FÖRST så intervals.icu hittar och parsar dem
    full_desc  = "\n\n".join(filter(None, [step_text, day.description, nutr_block]))

    slot_suffix = f" ({day.slot})" if day.slot != "MAIN" else ""

    payload: dict = {
        "category":          "WORKOUT",
        "start_date_local":  day.date + _slot_time(day.slot),
        "type":              day.intervals_type,
        "name":              day.title + slot_suffix,
        "description":       full_desc + f"\n\n{AI_TAG} ({get_used_model()})",
        "moving_time":       day.duration_min * 60,
        "planned_distance":  day.distance_km * 1000,
        "color":             _workout_color(day),
    }
    if athlete and day.intervals_type != "Rest":
        tss = estimate_tss_coggan(day, athlete)
        if tss > 0:
            payload["planned_load"] = tss
    if day.workout_steps and day.intervals_type not in _POWER_SPORTS | {"WeightTraining", "Rest"}:
        payload["workout_doc"] = build_hr_workout_doc(day.workout_steps)

    resp = requests.post(f"{BASE}/athlete/{ATHLETE_ID}/events", auth=AUTH, timeout=10, json=payload)
    resp.raise_for_status()
    log.debug(f"Sparat {day.date} – event id: {resp.json().get('id')}")

# ══════════════════════════════════════════════════════════════════════════════
# VÄDER (utökade WMO-koder, timdata för eftermiddag)
# ══════════════════════════════════════════════════════════════════════════════

def fetch_weather(days):
    try:
       # Yr kräver en User-Agent för att tillåta anrop
        headers = {"User-Agent": f"AI-Traningsplanerare ({CONTACT_EMAIL})"}
        resp = requests.get(
            f"https://api.met.no/weatherapi/locationforecast/2.0/compact?lat={LAT}&lon={LON}",
            headers=headers, timeout=10
        )
        resp.raise_for_status()
        data = resp.json()

        timeseries = data.get("properties", {}).get("timeseries", [])

        hourly_by_date = {}
        for item in timeseries:
            # Korrekt tidszonskonvertering från UTC till Stockholm
            utc_dt = datetime.fromisoformat(item["time"])
            local_dt = utc_dt.astimezone(ZoneInfo("Europe/Stockholm"))
            d_str = local_dt.date().isoformat()
            hour = local_dt.hour
            if d_str not in hourly_by_date:
                hourly_by_date[d_str] = {
                    "all_temps": [], "all_precip": 0.0,
                    "am_temps": [], "am_precip": [], "am_codes": [],
                    "pm_temps": [], "pm_precip": [], "pm_codes": []
                }
            details = item.get("data", {}).get("instant", {}).get("details", {})
            temp = details.get("air_temperature")
            # Yr har 1h-intervaller de närmsta dagarna, sedan 6h-intervaller. Vi hanterar båda!
            next_data = item.get("data", {}).get("next_1_hours") or item.get("data", {}).get("next_6_hours") or {}
            precip = next_data.get("details", {}).get("precipitation_amount", 0.0)
            code = next_data.get("summary", {}).get("symbol_code", "")
            # Tvätta Yr-koden (ta bort _day, _night)
            clean_code = code.split("_")[0] if code else ""

            if temp is not None:
                hourly_by_date[d_str]["all_temps"].append(temp)
            if precip is not None:
                hourly_by_date[d_str]["all_precip"] += precip

            if 6 <= hour <= 11:
                if temp is not None: hourly_by_date[d_str]["am_temps"].append(temp)
                if precip is not None: hourly_by_date[d_str]["am_precip"].append(precip)
                if clean_code: hourly_by_date[d_str]["am_codes"].append(clean_code)
            elif 13 <= hour <= 18:
                if temp is not None: hourly_by_date[d_str]["pm_temps"].append(temp)
                if precip is not None: hourly_by_date[d_str]["pm_precip"].append(precip)
                if clean_code: hourly_by_date[d_str]["pm_codes"].append(clean_code)

        result = []
        target_dates = [(date.today() + timedelta(days=i)).isoformat() for i in range(days)]
        for dt in target_dates:
            day_data = hourly_by_date.get(dt, {})
            if not day_data or not day_data["all_temps"]:
                continue
            temp_max = round(max(day_data["all_temps"]), 1)
            temp_min = round(min(day_data["all_temps"]), 1)
            total_rain = round(day_data["all_precip"], 1)

            am_temps = day_data.get("am_temps", [])
            am_temp = round(sum(am_temps) / len(am_temps), 1) if am_temps else temp_min
            am_precip = day_data.get("am_precip", [])
            am_rain = round(sum(am_precip), 1) if am_precip else 0
            am_codes = day_data.get("am_codes", [])
            if am_codes:
                from collections import Counter
                am_code = Counter(am_codes).most_common(1)[0][0]
            else:
                am_code = "unknown"
            am_desc = YR_CODES.get(am_code, am_code.capitalize() or "Okänt")
            if am_temp > 3 and "snow" in am_code and "sleet" not in am_code:
                am_desc = "Regn"

            pm_temps = day_data.get("pm_temps", [])
            pm_temp = round(sum(pm_temps) / len(pm_temps), 1) if pm_temps else temp_max
            pm_precip = day_data.get("pm_precip", [])
            pm_rain = round(sum(pm_precip), 1) if pm_precip else 0
            pm_codes = day_data.get("pm_codes", [])
            if pm_codes:
                from collections import Counter
                pm_code = Counter(pm_codes).most_common(1)[0][0]
            else: 
                pm_code = "unknown"
            pm_desc = YR_CODES.get(pm_code, pm_code.capitalize() or "Okänt")
            if pm_temp > 3 and "snow" in pm_code and "sleet" not in pm_code:
                pm_desc = "Regn"

            if pm_temp > 3 and "snow" in pm_desc.lower():
                # Fel i prognosen – temp för hög för snö, tolka som regn istället
                pm_desc = "Regn"

            result.append({
                "date": dt,
                "temp_max": temp_max,
                "temp_min": temp_min,
                "temp_morning": am_temp,
                "rain_morning_mm": am_rain,
                "desc_morning": am_desc,
                "weathercode_morning": am_code,
                "temp_afternoon": pm_temp,
                "rain_afternoon_mm": pm_rain,
                "desc": pm_desc,
                "weathercode": pm_code,
                "rain_mm": total_rain,
            })
        CACHE_FILE.write_text(json.dumps({"fetched": date.today().isoformat(), "data": result}))
        return result
    except Exception as e:
        log.warning(f"Väder-API (Yr) misslyckades: {e}. Försöker cache...")
        if CACHE_FILE.exists():
            cached = json.loads(CACHE_FILE.read_text())
            log.info(f"Använder väder-cache från {cached.get('fetched','?')}")
            return cached.get("data", [])
        log.warning("Ingen väder-cache. Fortsätter utan väderdata.")
        return []

# ══════════════════════════════════════════════════════════════════════════════
# ANALYS
# ══════════════════════════════════════════════════════════════════════════════

def sanitize(text, max_len=300):
    if not text: return ""
    text = text[:max_len]
    for pat in [r"ignore\s+(all\s+)?instructions?", r"ignorera\s+restriktioner",
                r"act\s+as", r"jailbreak", r"<[^>]+>", r"system\s*:"]:
        text = re.sub(pat, "[REDACTED]", text, flags=re.IGNORECASE)
    text = re.sub(r"[^\w\s,.!?:;()/\-]", "", text)
    return text.strip()

def fmt(val, suffix=""):
    if val is None: return "N/A"
    if isinstance(val, float): return f"{round(val,1)}{suffix}"
    return f"{val}{suffix}"

# ══════════════════════════════════════════════════════════════════════════════
# DATAKVALITETSVALIDERING
# ══════════════════════════════════════════════════════════════════════════════


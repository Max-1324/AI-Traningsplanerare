from training_plan.core.common import *
from training_plan.engine.planning import is_ai_generated
from training_plan.engine.postprocess import estimate_tss_coggan

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
                log.warning(f"Could not delete {w.get('id')}: {e}")
    return n

def update_manual_nutrition(workout, nutrition):
    desc  = workout.get("description") or ""
    lines = [l for l in desc.split("\n") if not l.startswith(NUTRITION_TAG)]
    new   = "\n".join(lines).strip() + f"\n\n{NUTRITION_TAG} {nutrition}"
    try:
        requests.put(f"{BASE}/athlete/{ATHLETE_ID}/events/{workout['id']}",
                     auth=AUTH, json={"description": new.strip()}, timeout=10).raise_for_status()
    except Exception as e:
        log.warning(f"Could not update nutrition: {e}")

def _slot_time(slot: str) -> str:
    """AM→07:00, PM→17:00, MAIN→16:00 (eftermiddag som default)."""
    return {"AM": "T07:00:00", "PM": "T17:00:00"}.get(slot, "T16:00:00")

def save_event(day: PlanDay):
    name = day.title if day.title and day.title != "Rest" else "🛌 Rest"
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
            f"{s.exercise}: {s.sets}x{s.reps}" + (f", rest {s.rest_sec}s" if s.rest_sec else "") + (f" - {s.notes}" if s.notes else "")
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
    log.debug(f"Saved {day.date} – event id: {resp.json().get('id')}")


from training_plan.core.common import *
from training_plan.engine.analysis import env_nutrition
from training_plan.engine.postprocess.load import estimate_tss_coggan

_CHO_PER_HOUR_RE = re.compile(r"(\d{1,3})\s*[-–]\s*(\d{1,3})\s*g\s*CHO/h", re.IGNORECASE)


def _extract_cho_per_hour_target(text: str) -> str | None:
    if not text:
        return None
    m = _CHO_PER_HOUR_RE.search(text)
    if not m:
        return None
    try:
        lo = int(m.group(1))
        hi = int(m.group(2))
    except Exception:
        return None
    if lo <= 0 or hi <= 0:
        return None
    if lo > hi:
        lo, hi = hi, lo
    return f"{lo}-{hi}g CHO/h"


def calculate_nutrition_periodization(phase_name: str, days_to_race: Optional[int],
                                       workout_day, tss_estimate: float,
                                       weight_kg: float | None = None) -> str:
    """
    Returns nutrition strategy based on training phase, race proximity and session load.
    Complements environment-based nutrition with periodized recommendations.
    """
    dur = workout_day.duration_min
    sport = workout_day.intervals_type
    manual_cho_target = _extract_cho_per_hour_target(getattr(workout_day, "nutrition", "") or "")

    if sport in ("Rest", "WeightTraining") or dur < 30:
        return ""

    def cho_range(low_g_kg: float, high_g_kg: float) -> str:
        if weight_kg:
            lo = round(low_g_kg * weight_kg)
            hi = round(high_g_kg * weight_kg)
            return f"{lo}–{hi}g CHO ({low_g_kg}–{high_g_kg}g/kg × {round(weight_kg)}kg)"
        return f"{low_g_kg}–{high_g_kg}g CHO/kg kroppsvikt"

    # Tävlingsdag
    if days_to_race == 0:
        cho = manual_cho_target or "60-90g CHO/h"
        return (f"RACE DAY: Start 300ml sports drink. {cho} during the race (gels + bars). "
                "500mg Na/h. Caffeine 200mg at t-1h. Drink 500ml at finish.")

    # Kolhydratladning 3 dagar före
    if days_to_race is not None and 1 <= days_to_race <= 3:
        dag = 4 - days_to_race
        return (f"CARB LOADING day {dag}/3: {cho_range(8, 10)} today. "
                f"Rice, pasta, oatmeal, bread. Avoid fiber and fat. Drink 2-3L.")

    # Hög TSS-dag
    if tss_estimate > 100:
        during = "" if manual_cho_target else " During: 60-90g CHO/h."
        return (f"HIGH-CARB: {round(tss_estimate)} TSS planned – {cho_range(6, 8)} today. "
                f"Breakfast: oatmeal + banana + honey.{during}")

    # Basfas + Z2-pass (fasted training OK)
    is_z2_only = all(s.zone in ("Z1", "Z2") for s in workout_day.workout_steps) if workout_day.workout_steps else False
    if phase_name in ("Base", "Grundtraning") and is_z2_only and 60 <= dur <= 90 and not manual_cho_target:
        return ("FASTED OK: Morning session 60-90min Z2 can be done fasted for fat adaptation. "
                "Max 30g CHO/h if you are hungry. Have a gel ready.")

    # Standard baserat på duration
    if manual_cho_target:
        return ""
    if dur < 60:
        return ""
    elif dur <= 90:
        return f"30-60g CHO/h during the session ({dur}min). Sports drink or 1 gel/45min."
    else:
        return f"60-90g CHO/h during the session ({dur}min). Test race day nutrition."


def add_env_nutrition(days, weather, phase=None, races=None, athlete=None, wellness=None):
    weight_kg: float | None = None
    if wellness:
        for w in reversed(wellness):
            if w.get("weight"):
                weight_kg = float(w["weight"])
                break
    wmap = {w["date"]: w for w in weather}
    for i, day in enumerate(days):
        if day.duration_min < 60 or day.intervals_type in ("Rest","WeightTraining"): continue
        w = wmap.get(day.date, {})
        fz = day.workout_steps[0].zone if day.workout_steps else "Z2"
        all_zones = [s.zone for s in day.workout_steps] if day.workout_steps else []

        if day.slot == "AM":
            temp = w.get("temp_morning", w.get("temp_min", 10))
        else:
            temp = w.get("temp_afternoon", w.get("temp_max", 15))

        extra = env_nutrition(temp, day.duration_min, fz, all_zones=all_zones)
        nutr_parts = [day.nutrition] if day.nutrition else []
        if extra:
            nutr_parts.append(" ".join(extra))

        # Periodiserad nutrition
        if phase and races is not None:
            d2r = None
            future_races = [r for r in races if r.get("start_date_local","")[:10] >= day.date]
            if future_races:
                try:
                    rd = datetime.strptime(future_races[0]["start_date_local"][:10], "%Y-%m-%d").date()
                    day_date = datetime.strptime(day.date, "%Y-%m-%d").date()
                    d2r = (rd - day_date).days
                except Exception:
                    pass
            tss_est = 0.0
            if athlete:
                try:
                    tss_est = estimate_tss_coggan(day, athlete)
                except Exception:
                    pass
            phase_name = phase.get("phase", "Base") if isinstance(phase, dict) else str(phase)
            perio = calculate_nutrition_periodization(phase_name, d2r, day, tss_est, weight_kg)
            if perio:
                nutr_parts.append(perio)

        new_nutr = "\n".join(p for p in nutr_parts if p).strip()
        if new_nutr != day.nutrition:
            days[i] = day.model_copy(update={"nutrition": new_nutr})
    return days

_HIGH_ZONES = {"Z3", "Z4", "Z5", "Z6", "Z7", "Zone 3", "Zone 4", "Zone 5", "Zone 6", "Zone 7"}

def strip_train_low_contradiction(days: list) -> tuple[list, list]:
    """Remove TRAIN LOW lines from sessions that contain Z3+ steps — prevents contradictory advice."""
    changes = []
    for i, day in enumerate(days):
        if not day.workout_steps:
            continue
        if not any(s.zone in _HIGH_ZONES for s in day.workout_steps):
            continue

        def _strip(text: str) -> str:
            if not text or "TRAIN LOW" not in text:
                return text
            return "\n".join(l for l in text.splitlines() if not l.strip().startswith("TRAIN LOW")).strip()

        new_desc = _strip(day.description)
        new_nutr = _strip(day.nutrition)
        if new_desc != day.description or new_nutr != day.nutrition:
            days[i] = day.model_copy(update={"description": new_desc, "nutrition": new_nutr})
            changes.append(f"TRAIN-LOW-STRIP: {day.date} – removed contradictory TRAIN LOW from intensity session")
    return days, changes



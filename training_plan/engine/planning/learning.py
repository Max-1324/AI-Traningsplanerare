from training_plan.core.common import *
from training_plan.core.models import AppState
from training_plan.engine.libraries import *
from training_plan.engine.utils import safe_date_str

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



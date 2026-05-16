from training_plan.core.common import *
from training_plan.engine.planning import classify_session_category
from training_plan.engine.utils import time_available_minutes

_MIN_DURATION = MIN_DURATION_BY_SPORT
_MAX_ROLLSKI_PER_WEEK = int(os.getenv("MAX_ROLLSKI_PER_WEEK", "1"))
_MAX_STRENGTH_PER_PLAN = int(os.getenv("MAX_STRENGTH_PER_PLAN", "2"))
_MIN_STRENGTH_GAP_DAYS = int(os.getenv("MIN_STRENGTH_GAP_DAYS", "2"))

INJURY_PROFILES: dict[str, dict] = {
    "knee": {
        "label": "Knee",
        "avoid_sports": {"Run"},
        "safe_sports":  {"VirtualRide", "RollerSki"},   # RollerSki double-poling is knee-friendly
        "primary_replacement": "VirtualRide",
        "duration_cap_moderate": 60,
        "rehab_exercises": [
            {"name": "Terminal knee extension (band)", "sets": "3x20", "rest": "30s",
             "description": "Band around back of knee. Start at 30° flex, extend fully. Slow and controlled."},
            {"name": "Straight leg raise", "sets": "3x15", "rest": "30s",
             "description": "Lie flat, tighten quad, lift leg to 45°. Hold 2s at top."},
            {"name": "Wall sit (isometric)", "sets": "3x30s", "rest": "45s",
             "description": "Back against wall, 90° knee angle. Stop immediately if pain >3/10."},
            {"name": "Clamshell (side-lying)", "sets": "3x20/side", "rest": "30s",
             "description": "Hip external rotation, keep feet together. Targets hip abductors to offload knee."},
            {"name": "Step-down (eccentric)", "sets": "3x10/leg", "rest": "45s",
             "description": "Stand on step, slowly lower opposite foot to floor. Control the descent (3s down)."},
        ],
    },
    "hip": {
        "label": "Hip/Glute",
        "avoid_sports": {"Run"},
        "safe_sports":  {"VirtualRide", "RollerSki"},
        "primary_replacement": "VirtualRide",
        "duration_cap_moderate": 60,
        "rehab_exercises": [
            {"name": "Glute bridge (isometric hold)", "sets": "3x10x5s", "rest": "30s",
             "description": "Lie on back, press hips up and hold 5s. Keep core tight."},
            {"name": "Clamshell", "sets": "3x20/side", "rest": "30s",
             "description": "Side-lying hip abduction. Avoid rotating the pelvis."},
            {"name": "Hip flexor stretch (half-kneeling)", "sets": "3x45s/side", "rest": "20s",
             "description": "Half-kneeling, tuck pelvis, lean forward gently. Hold without pain."},
            {"name": "Side-lying hip abduction", "sets": "3x15/side", "rest": "30s",
             "description": "Slow raise to 30°. Do not allow pelvis to tilt."},
        ],
    },
    "back": {
        "label": "Back/Lower back",
        "avoid_sports": {"Run", "Ride"},
        "safe_sports":  {"VirtualRide"},
        "primary_replacement": "VirtualRide",
        "duration_cap_moderate": 45,
        "rehab_exercises": [
            {"name": "Cat-cow (mobility)", "sets": "3x10", "rest": "20s",
             "description": "On all fours. Arch up (cat) then dip (cow). Slow and rhythmic."},
            {"name": "Dead bug", "sets": "3x8/side", "rest": "30s",
             "description": "On back, arms up, legs at 90°. Lower opposite arm+leg keeping lower back flat."},
            {"name": "Bird dog", "sets": "3x10/side", "rest": "30s",
             "description": "On all fours. Extend opposite arm and leg simultaneously. Hold 3s."},
            {"name": "Prone press-up (McKenzie)", "sets": "3x10", "rest": "20s",
             "description": "Lie face down, press up on hands only. Hips stay on floor. Stop if pain radiates to leg."},
        ],
    },
    "shoulder": {
        "label": "Shoulder/Arm",
        "avoid_sports": {"Ride", "VirtualRide"},
        "safe_sports":  {"Run"},
        "primary_replacement": "Run",
        "duration_cap_moderate": 45,
        "rehab_exercises": [
            {"name": "Pendulum swing", "sets": "3x30s/side", "rest": "20s",
             "description": "Lean forward, let arm hang and make small circles. Gravity traction."},
            {"name": "External rotation (band)", "sets": "3x15/side", "rest": "30s",
             "description": "Elbow at 90°, rotate outward against band. Slow return."},
            {"name": "Scapular retraction", "sets": "3x15", "rest": "20s",
             "description": "Squeeze shoulder blades together, hold 3s. Keep shoulders away from ears."},
            {"name": "Wall angel", "sets": "3x10", "rest": "30s",
             "description": "Stand against wall, arms at 90°, slide up and down. Keep entire back flat."},
        ],
    },
    "calf_achilles": {
        "label": "Calf/Achilles",
        "avoid_sports": {"Run", "RollerSki"},
        "safe_sports":  {"VirtualRide", "Ride"},
        "primary_replacement": "VirtualRide",
        "duration_cap_moderate": 45,
        "rehab_exercises": [
            {"name": "Eccentric calf raise (Alfredson)", "sets": "3x15/leg", "rest": "45s",
             "description": "Rise on two legs, lower slowly on one (3s down). Use step edge for full range. Skip if acute pain."},
            {"name": "Soleus calf raise (seated)", "sets": "3x20", "rest": "30s",
             "description": "Seated, knee at 90°. Rise on ball of foot slowly. Targets deeper soleus."},
            {"name": "Ankle alphabet", "sets": "2x/side", "rest": "20s",
             "description": "Draw the alphabet in the air with your foot. Improves proprioception."},
        ],
    },
    "shin": {
        "label": "Shin splints",
        "avoid_sports": {"Run"},
        "safe_sports":  {"VirtualRide", "Ride", "RollerSki"},
        "primary_replacement": "VirtualRide",
        "duration_cap_moderate": 60,
        "rehab_exercises": [
            {"name": "Toe raises (tibialis raise)", "sets": "3x20", "rest": "30s",
             "description": "Stand, lift toes off floor keeping heels down. Slow and controlled."},
            {"name": "Calf raises (to balance shin load)", "sets": "3x20", "rest": "30s",
             "description": "Standard calf raise. Balances anterior/posterior lower leg load."},
            {"name": "Arch strengthening (towel scrunch)", "sets": "3x30s/foot", "rest": "20s",
             "description": "Scrunch a towel with toes. Strengthens foot intrinsics."},
        ],
    },
    "generic": {
        "label": "General discomfort",
        "avoid_sports": {"Run"},
        "safe_sports":  {"VirtualRide"},
        "primary_replacement": "VirtualRide",
        "duration_cap_moderate": 45,
        "rehab_exercises": [
            {"name": "Foam roll (affected area)", "sets": "2x60s", "rest": "20s",
             "description": "Slow rolling, pause on tender spots for 10s. Avoid rolling directly on joints."},
            {"name": "Light mobility (full body)", "sets": "2x10/movement", "rest": "15s",
             "description": "Leg swings, arm circles, hip circles. Keep within pain-free range."},
        ],
    },
}


def _parse_sets_reps(raw: str) -> tuple[int, str]:
    """Parse '3x20' or '3×20/leg' into (sets=3, reps='20') or (sets=3, reps='20/leg')."""
    m = re.match(r"(\d+)\s*[x×]\s*(.+)", str(raw).strip())
    if m:
        return int(m.group(1)), m.group(2).strip()
    return 3, str(raw)


def _parse_rest_sec(raw: str) -> int:
    """Parse '30s' or '45' into an integer number of seconds."""
    m = re.match(r"(\d+)", str(raw or "").strip())
    return int(m.group(1)) if m else 60


def _inject_rehab_session(days: list, profile: dict, injury_note: str) -> list:
    """Insert a rehab WeightTraining session on the first available rest day."""
    exercises = profile.get("rehab_exercises", [])
    if not exercises:
        return days
    rehab_steps = []
    for ex in exercises:
        sets, reps = _parse_sets_reps(ex.get("sets", "3x10"))
        rehab_steps.append(StrengthStep(
            exercise=ex["name"],
            sets=sets,
            reps=reps,
            rest_sec=_parse_rest_sec(ex.get("rest", "30s")),
            notes=ex.get("description", ""),
        ))
    for i, day in enumerate(days):
        if day.intervals_type == "Rest" or day.duration_min == 0:
            rehab_day = day.model_copy(update={
                "title": f"Injury rehab – {profile['label']}",
                "intervals_type": "WeightTraining",
                "duration_min": 20,
                "description": (
                    f"⚕️ Rehab session for: {injury_note}\n"
                    f"Keep all movements pain-free. Stop any exercise that causes >3/10 pain.\n"
                    f"Consult a physio if symptoms worsen or do not improve within 5–7 days."
                ),
                "workout_steps": [],
                "strength_steps": rehab_steps,
                "vetoed": False,
            })
            days[i] = rehab_day
            return days
    return days


def apply_injury_rules(days, injury_note: str, injury_profile: dict | None = None):
    if not injury_note or injury_note.lower() in ("", "nej", "n", "inga"):
        return days, []

    # Use AI-classified profile if available, otherwise fall back to keyword matching
    if injury_profile and injury_profile.get("profile_key") in INJURY_PROFILES:
        profile = INJURY_PROFILES[injury_profile["profile_key"]]
        severity = injury_profile.get("severity", "MILD")
        avoid_sports = profile["avoid_sports"]
        replacement = profile["primary_replacement"]
    else:
        # Keyword fallback (original logic)
        inj = injury_note.lower()
        avoid_map = [
            (["knä", "höft", "lår", "knee", "hip", "thigh"],     {"Run"},             "VirtualRide"),
            (["vad", "fot", "ankel", "calf", "foot", "ankle"],   {"Run", "RollerSki"},"VirtualRide"),
            (["axel", "armbåge", "handled", "shoulder", "elbow", "wrist"], {"Ride", "VirtualRide"}, "Run"),
            (["rygg", "nacke", "back", "neck"],                   {"Run", "Ride"},     "VirtualRide"),
            (["skena", "shin", "splints"],                        {"Run"},             "VirtualRide"),
        ]
        avoid_sports, replacement, severity = set(), "VirtualRide", "MILD"
        for keywords, sports, repl in avoid_map:
            if any(k in inj for k in keywords):
                avoid_sports |= sports
                replacement = repl
        if not avoid_sports:
            avoid_sports = {"Run"}
        profile = INJURY_PROFILES.get("generic", {})

    # Cap duration based on severity
    dur_cap = profile.get("duration_cap_moderate", 60) if severity in ("MODERATE", "SEVERE") else 90

    changes = []
    for i, day in enumerate(days):
        if day.intervals_type in avoid_sports:
            new_dur = min(day.duration_min, dur_cap)
            days[i] = day.model_copy(update={
                "title":          f"{day.title} [→ {replacement}, injury]",
                "intervals_type": replacement,
                "duration_min":   new_dur,
                "description":    day.description + f"\n\n⚠️ Adapted due to injury: '{injury_note}'",
            })
            changes.append(f"INJURY: {day.date} '{day.intervals_type}' → '{replacement}' ({new_dur}min, {severity})")

    if changes:
        days = _inject_rehab_session(days, profile, injury_note)
        label = profile.get("label", "injury")
        safe = ", ".join(profile.get("safe_sports", set()))
        changes.insert(0,
            f"INJURY-PROFILE ({label}, {severity}): avoiding {avoid_sports} | safe: {safe} | "
            f"rehab session injected on first rest day"
        )
    return days, changes


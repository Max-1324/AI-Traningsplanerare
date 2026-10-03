import os

CONSTRAINT_PREFIXES = ("bara:", "bara ", "ej:", "ej ", "only:", "only ", "not:", "not ")

SPORT_NAME_MAP = {
    "cykling": ["Ride"],
    "cykel": ["Ride"],
    "ride": ["Ride"],
    "utomhuscykling": ["Ride"],
    "utomhuscykel": ["Ride"],
    "utecykling": ["Ride"],
    "utecykel": ["Ride"],
    "landsväg": ["Ride"],
    "outdoor": ["Ride"],
    "zwift": ["VirtualRide"],
    "inomhuscykling": ["VirtualRide"],
    "inomhuscykel": ["VirtualRide"],
    "innecykling": ["VirtualRide"],
    "innecykel": ["VirtualRide"],
    "trainer": ["VirtualRide"],
    "wahoo": ["VirtualRide"],
    "kickr": ["VirtualRide"],
    "indoor": ["VirtualRide"],
    "virtualride": ["VirtualRide"],
    "löpning": ["Run"],
    "löp": ["Run"],
    "run": ["Run"],
    "jogg": ["Run"],
    "jogga": ["Run"],
    "jogging": ["Run"],
    "lapning": ["Run"],
    "rullskidor": ["RollerSki"],
    "rullskid": ["RollerSki"],
    "rollerski": ["RollerSki"],
    "skidor": ["NordicSki"],
    "längdskidor": ["NordicSki"],
    "längdåkning": ["NordicSki"],
    "nordicski": ["NordicSki"],
    "styrka": ["WeightTraining"],
    "styrketräning": ["WeightTraining"],
    "weighttraining": ["WeightTraining"],
    "vila": ["Rest"],
    "rest": ["Rest"],
}

# ── Master sport catalog ──────────────────────────────────────────────────────
# Add new sports here. They become available via AVAILABLE_SPORTS in .env.
# WeightTraining and Rest are always included regardless of AVAILABLE_SPORTS.
ALL_SPORTS_CATALOG = [
    {
        "name": "Cycling (outdoors)",
        "intervals_type": "Ride",
        "injury_risk": "low",
        "comment": "PRIO 1. Main sport – prioritize long outdoor sessions in good weather.",
    },
    {
        "name": "Indoor cycling (Zwift)",
        "intervals_type": "VirtualRide",
        "injury_risk": "low",
        "comment": "PRIO 1 (bad weather). Perfect for controlled intervals and tempo sessions indoors.",
    },
    {
        "name": "Roller skiing",
        "intervals_type": "RollerSki",
        "injury_risk": "medium",
        "comment": "PRIO 2. Complement to maintain ski-specific muscles. Max 1 session/week. Avoid when fatigued/low HRV.",
    },
    {
        "name": "Running",
        "intervals_type": "Run",
        "injury_risk": "high",
        "comment": "PRIO 3. Complement. Limit volume – max 10% increase/week.",
    },
    {
        "name": "Swimming",
        "intervals_type": "Swim",
        "injury_risk": "low",
        "comment": "PRIO 3. Low-impact cross-training. Good for active recovery or when injury limits run/bike.",
    },
    {
        "name": "Nordic skiing",
        "intervals_type": "NordicSki",
        "injury_risk": "medium",
        "comment": "PRIO 2 (winter). Full-body endurance. Max 1-2 sessions/week alongside cycling.",
    },
    {
        "name": "Strength training",
        "intervals_type": "WeightTraining",
        "injury_risk": "low",
        "comment": "PRIO 3. Bodyweight ONLY. Max 2 sessions/10 days. Never two days in a row.",
    },
]

# Sports planned only when the annual plan or a goal race asks for them (never as filler,
# substitute or alternative). Swimming by default.
ON_DEMAND_SPORTS = {
    s.strip() for s in os.getenv("ON_DEMAND_SPORTS", "Swim").split(",") if s.strip()
}

# Where load goes when a sport cannot take it (no access, weather, budget). Cycling last:
# lowest injury risk, so it is the safe place for whatever is left.
SUBSTITUTES = {
    "NordicSki": ["RollerSki", "Run", "Ride", "VirtualRide"],
    "RollerSki": ["NordicSki", "Run", "Ride", "VirtualRide"],
    "Run": ["Ride", "VirtualRide"],
    "Ride": ["VirtualRide"],
    "VirtualRide": ["Ride"],
    "Swim": ["Ride", "VirtualRide"],
}

# ── Active sports (filtered by AVAILABLE_SPORTS env var) ─────────────────────
# Example .env: AVAILABLE_SPORTS=Run,RollerSki,Ride,VirtualRide
# If not set: all catalog sports are available.
_available_env = os.getenv("AVAILABLE_SPORTS", "").strip()
_available_set = (
    {s.strip() for s in _available_env.split(",") if s.strip()}
    if _available_env
    else {s["intervals_type"] for s in ALL_SPORTS_CATALOG}
)
# WeightTraining and Rest are always included
_available_set |= {"WeightTraining", "Rest"}

SPORTS = [s for s in ALL_SPORTS_CATALOG if s["intervals_type"] in _available_set]

VALID_TYPES = {sport["intervals_type"] for sport in SPORTS} | {"Rest"}

INTENSE = {"Z4", "Z5", "Zon 4", "Zon 5", "Zone 4", "Zone 5", "Z4+", "Z5+", "Z6", "Z7"}

WARMUP_BY_SPORT = {
    "VirtualRide": "🔥 Warm-up (5-10 min before): Leg swings front/back, hip circles, deep lunges x10/side. Then roll out easily the first few minutes.",
    "Ride": "🔥 Warm-up (5-10 min before): Leg swings front/back, hip circles, deep lunges x10/side. Then roll out easily the first few minutes.",
    "RollerSki": "🔥 Warm-up (5-10 min before): Leg swings, hip circles, shoulder rotations, light jog in place.",
    "Run": "🔥 Warm-up (5-10 min before): Hip circles, leg swings front/back, high knees, butt kicks. Start at walking pace.",
}

WARMUP_DEFAULT = "🔥 Warm-up (5-10 min before): Dynamic movements – hip circles, leg swings, light activation."

MIN_DURATION_BY_SPORT = {
    "Ride": 75,
    "VirtualRide": 45,
    "RollerSki": 60,
    "Run": 30,
    "WeightTraining": 30,
}

EMOJIS = {
    "NordicSki": "⛷️",
    "RollerSki": "🎿",
    "Ride": "🚴",
    "VirtualRide": "🖥️",
    "Run": "🏃",
    "Swim": "🏊",
    "WeightTraining": "💪",
    "Rest": "😴",
}

# ── Zone constants — canonical source for pipeline, validation, postprocess ──
# Zone intensity (IF proxy) used in weighted-intensity and TSS estimation.
ZONE_INTENSITY: dict[str, float] = {
    "Z1": 0.55,
    "Z2": 0.70,
    "Z3": 0.83,
    "Z4": 0.95,
    "Z5": 1.05,
    "Z6": 1.15,
    "Z7": 1.25,
}

# Maps all accepted zone spellings → canonical short form (Z1-Z7).
ZONE_CANONICAL: dict[str, str] = {
    "Z1": "Z1", "ZONE 1": "Z1", "ZON 1": "Z1",
    "Z2": "Z2", "ZONE 2": "Z2", "ZON 2": "Z2",
    "Z3": "Z3", "ZONE 3": "Z3", "ZON 3": "Z3",
    "Z4": "Z4", "ZONE 4": "Z4", "ZON 4": "Z4", "Z4+": "Z4",
    "Z5": "Z5", "ZONE 5": "Z5", "ZON 5": "Z5", "Z5+": "Z5",
    "Z6": "Z6", "ZONE 6": "Z6", "ZON 6": "Z6",
    "Z7": "Z7", "ZONE 7": "Z7", "ZON 7": "Z7",
}

# Numeric ordering for zone comparisons.
ZONE_ORDER: dict[str, int] = {
    "Z1": 1, "ZONE 1": 1, "ZON 1": 1,
    "Z2": 2, "ZONE 2": 2, "ZON 2": 2,
    "Z3": 3, "ZONE 3": 3, "ZON 3": 3,
    "Z4": 4, "ZONE 4": 4, "ZON 4": 4, "Z4+": 4,
    "Z5": 5, "ZONE 5": 5, "ZON 5": 5, "Z5+": 5,
    "Z6": 6, "ZONE 6": 6, "ZON 6": 6,
    "Z7": 7, "ZONE 7": 7, "ZON 7": 7,
}

# Full set of valid zone strings accepted from AI output.
VALID_ZONES: frozenset[str] = frozenset({
    "Z1", "Z2", "Z3", "Z4", "Z5", "Z6", "Z7",
    "ZONE 1", "ZONE 2", "ZONE 3", "ZONE 4", "ZONE 5", "ZONE 6", "ZONE 7",
    "ZON 1", "ZON 2", "ZON 3", "ZON 4", "ZON 5", "ZON 6", "ZON 7",
    "Z4+", "Z5+",
})

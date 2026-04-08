CONSTRAINT_PREFIXES = ("bara:", "bara ", "ej:", "ej ", "only:", "only ", "not:", "not ")

SPORT_NAME_MAP = {
    "cykling": ["Ride"],
    "cykel": ["Ride"],
    "ride": ["Ride"],
    "utomhuscykling": ["Ride"],
    "zwift": ["VirtualRide"],
    "inomhuscykling": ["VirtualRide"],
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
    "styrka": ["WeightTraining"],
    "styrketräning": ["WeightTraining"],
    "weighttraining": ["WeightTraining"],
    "vila": ["Rest"],
    "rest": ["Rest"],
}

SPORTS = [
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
        "name": "Strength training",
        "intervals_type": "WeightTraining",
        "injury_risk": "low",
        "comment": "PRIO 3. Bodyweight ONLY. Max 2 sessions/10 days. Never two days in a row.",
    },
]

VALID_TYPES = {sport["intervals_type"] for sport in SPORTS} | {"Rest"}

YR_CODES = {
    "clearsky": "Clear sky",
    "fair": "Fair",
    "partlycloudy": "Partly cloudy",
    "cloudy": "Cloudy",
    "lightrainshowers": "Light rain showers",
    "rainshowers": "Rain showers",
    "heavyrainshowers": "Heavy rain showers",
    "lightrainshowersandthunder": "Light rain showers and thunder",
    "rainshowersandthunder": "Rain showers and thunder",
    "heavyrainshowersandthunder": "Heavy rain showers and thunder",
    "lightrain": "Light rain",
    "rain": "Rain",
    "heavyrain": "Heavy rain",
    "lightrainandthunder": "Light rain and thunder",
    "rainandthunder": "Rain and thunder",
    "heavyrainandthunder": "Heavy rain and thunder",
    "lightsleetshowers": "Light sleet showers",
    "sleetshowers": "Sleet showers",
    "heavysleetshowers": "Heavy sleet showers",
    "lightsleet": "Light sleet",
    "sleet": "Sleet",
    "heavysleet": "Heavy sleet",
    "lightsnowshowers": "Light snow showers",
    "snowshowers": "Snow showers",
    "heavysnowshowers": "Heavy snow showers",
    "lightsnow": "Light snow",
    "snow": "Snow",
    "heavysnow": "Heavy snow",
    "fog": "Fog",
}

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
    "WeightTraining": "💪",
    "Rest": "😴",
}

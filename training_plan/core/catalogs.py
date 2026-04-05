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
        "namn": "Cykling (utomhus)",
        "intervals_type": "Ride",
        "skaderisk": "lag",
        "kommentar": "PRIO 1. Huvudsport – Vätternrundan är målet. Prioritera långa utomhuspass vid bra väder.",
    },
    {
        "namn": "Inomhuscykling (Zwift)",
        "intervals_type": "VirtualRide",
        "skaderisk": "lag",
        "kommentar": "PRIO 1 (dåligt väder). Perfekt för kontrollerade intervaller och tempopass inomhus.",
    },
    {
        "namn": "Rullskidor",
        "intervals_type": "RollerSki",
        "skaderisk": "medel",
        "kommentar": "PRIO 2. Komplement för att bibehålla skidspecifik muskulatur. Max 1 pass/vecka. Undvik vid trötthet/låg HRV.",
    },
    {
        "namn": "Löpning",
        "intervals_type": "Run",
        "skaderisk": "hog",
        "kommentar": "PRIO 3. Komplement. Begränsa volym – max 10% ökning/vecka.",
    },
    {
        "namn": "Styrketräning",
        "intervals_type": "WeightTraining",
        "skaderisk": "lag",
        "kommentar": "PRIO 3. Kroppsvikt ENDAST. Max 2 pass/10 dagar. Aldrig två dagar i rad.",
    },
]

VALID_TYPES = {sport["intervals_type"] for sport in SPORTS} | {"Rest"}

YR_CODES = {
    "clearsky": "Klart",
    "fair": "Halvklart",
    "partlycloudy": "Växlande moln",
    "cloudy": "Mulet",
    "lightrainshowers": "Lätta regnskurar",
    "rainshowers": "Regnskurar",
    "heavyrainshowers": "Kraftiga regnskurar",
    "lightrainshowersandthunder": "Åskskurar",
    "rainshowersandthunder": "Åskskurar",
    "heavyrainshowersandthunder": "Kraftiga åskskurar",
    "lightrain": "Lätt regn",
    "rain": "Regn",
    "heavyrain": "Kraftigt regn",
    "lightrainandthunder": "Lätt regn/åska",
    "rainandthunder": "Regn och åska",
    "heavyrainandthunder": "Kraftigt regn/åska",
    "lightsleetshowers": "Lätta byar snöbl. regn",
    "sleetshowers": "Byar snöbl. regn",
    "heavysleetshowers": "Kraftiga byar snöbl. regn",
    "lightsleet": "Lätt snöblandat regn",
    "sleet": "Snöblandat regn",
    "heavysleet": "Kraft. snöbl. regn",
    "lightsnowshowers": "Lätta snöbyar",
    "snowshowers": "Snöbyar",
    "heavysnowshowers": "Kraftiga snöbyar",
    "lightsnow": "Lätt snöfall",
    "snow": "Snöfall",
    "heavysnow": "Kraftigt snöfall",
    "fog": "Dimma",
}

INTENSE = {"Z4", "Z5", "Zon 4", "Zon 5", "Z4+", "Z5+", "Z6", "Z7"}

WARMUP_BY_SPORT = {
    "VirtualRide": "🔥 Uppvärmning (5-10 min innan): Bensvingningar fram/bak, höftcirklar, djupa utfall x10/sida. Rulla sedan ut lätt de första minuterna.",
    "Ride": "🔥 Uppvärmning (5-10 min innan): Bensvingningar fram/bak, höftcirklar, djupa utfall x10/sida. Rulla sedan ut lätt de första minuterna.",
    "RollerSki": "🔥 Uppvärmning (5-10 min innan): Bensvingningar, höftcirklar, axelrotationer, lätt jogg på stället.",
    "Run": "🔥 Uppvärmning (5-10 min innan): Höftcirklar, bensvingningar fram/bak, knälyft, hälspark. Börja med promenadtempo.",
}

WARMUP_DEFAULT = "🔥 Uppvärmning (5-10 min innan): Dynamiska rörelser – höftcirklar, bensvingningar, lätt aktivering."

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

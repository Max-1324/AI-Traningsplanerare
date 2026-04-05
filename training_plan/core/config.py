import os
from pathlib import Path


ATHLETE_ID = os.getenv("INTERVALS_ATHLETE_ID", "")
INTERVALS_KEY = os.getenv("INTERVALS_API_KEY", "")
BASE = "https://intervals.icu/api/v1"
AUTH = ("API_KEY", INTERVALS_KEY)
LAT = float(os.getenv("ATHLETE_LAT", "59.3793"))
LON = float(os.getenv("ATHLETE_LON", "13.5036"))
LOCATION = os.getenv("ATHLETE_LOCATION", "Karlstad")
CONTACT_EMAIL = os.getenv("CONTACT_EMAIL", "din.epost@exempel.se")
RISK = os.getenv("RISK_TOLERANCE", "NORMAL").upper()
TARGET_CTL = int(os.getenv("TARGET_CTL", "85"))
AI_TAG = "ai-generated"
NUTRITION_TAG = "Nutritionsrad (AI):"
REPORT_TAG = "veckorapport-ai"
STATE_FILE = Path(".coach_state.json")
CACHE_FILE = Path(".weather_cache.json")


def get_used_model() -> str:
    return os.environ.get("_USED_MODEL", os.getenv("GEMINI_MODEL", "gemini"))

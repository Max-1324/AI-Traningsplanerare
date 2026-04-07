import json
import logging
import math
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import requests
from dotenv import load_dotenv
from pydantic import ValidationError

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

load_dotenv()

from training_plan.core.catalogs import (  # noqa: E402
    CONSTRAINT_PREFIXES,
    EMOJIS,
    INTENSE,
    MIN_DURATION_BY_SPORT,
    SPORT_NAME_MAP,
    SPORTS,
    VALID_TYPES,
    WARMUP_BY_SPORT,
    WARMUP_DEFAULT,
    YR_CODES,
)
from training_plan.core.config import (  # noqa: E402
    AI_TAG,
    ATHLETE_ID,
    AUTH,
    BASE,
    CACHE_FILE,
    CONTACT_EMAIL,
    INTERVALS_KEY,
    LAT,
    LOCATION,
    LON,
    NUTRITION_TAG,
    REPORT_TAG,
    RISK,
    STATE_FILE,
    TARGET_CTL,
    get_used_model,
)
from training_plan.core.models import AIPlan, PlanDay, WorkoutStep  # noqa: E402

logging.basicConfig(
    level=logging.DEBUG if os.getenv("LOG_LEVEL", "INFO").upper() == "DEBUG" else logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

args = None


def ensure_required_config():
    if not ATHLETE_ID or not INTERVALS_KEY:
        sys.exit("Set INTERVALS_ATHLETE_ID and INTERVALS_API_KEY in your .env.")

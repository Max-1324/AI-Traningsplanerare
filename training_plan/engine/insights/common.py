from training_plan.core.common import *
from training_plan.engine.planning import classify_session_category, session_duration_min
from training_plan.engine.utils import time_available_minutes

def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def _activity_date(item: dict) -> str:
    return (item.get("start_date_local") or item.get("date") or "")[:10]


def _recent_items(items: list[dict], days: int) -> list[dict]:
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    return [item for item in items if _activity_date(item) and _activity_date(item) >= cutoff]


def _avg(values: list[float], default: float = 0.0) -> float:
    return sum(values) / len(values) if values else default


def _score_bucket(score: float) -> str:
    if score >= 80:
        return "STRONG"
    if score >= 65:
        return "SOLID"
    if score >= 50:
        return "DEVELOPING"
    return "LIMITER"


def _dedupe_keep_order(items: list[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result





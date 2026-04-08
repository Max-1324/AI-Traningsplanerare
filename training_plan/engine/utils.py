"""Shared utility functions used across the training plan engine."""

import re
from datetime import datetime


def safe_date_str(activity) -> str:
    """Extract date string from activity dict, returns empty string if invalid."""
    try:
        return activity["start_date_local"][:10]
    except Exception:
        return ""


def safe_date(activity) -> datetime:
    """Parse datetime from activity dict."""
    try:
        return datetime.fromisoformat(activity["start_date_local"].replace("Z", "+00:00"))
    except Exception:
        return None


def time_available_minutes(value: str) -> int | None:
    """Parse time available string (e.g. '1h30m', '90m', '1.5h') to minutes."""
    if not value:
        return None
    text = value.strip().lower()
    hours_match = re.search(r"(\d+(?:[.,]\d+)?)\s*h", text)
    mins_match = re.search(r"(\d+)\s*m", text)
    if hours_match:
        return round(float(hours_match.group(1).replace(",", ".")) * 60)
    if mins_match:
        return int(mins_match.group(1))
    if text.isdigit():
        return int(text)
    return None


def strip_planner_comment_block(comments, comment_start="[AI_MORNING]", comment_end="[/AI_MORNING]"):
    """Remove planner comment block from comments string."""
    if not comments:
        return ""
    cleaned = re.sub(
        rf"{re.escape(comment_start)}.*?{re.escape(comment_end)}",
        "",
        comments,
        flags=re.DOTALL | re.IGNORECASE,
    )
    return cleaned.strip()


def read_wellness_score(today_wellness, keys, default=1, minimum=1, maximum=4):
    """Read wellness score from dict, clamping to min/max bounds."""
    if not today_wellness:
        return default
    for key in keys:
        value = today_wellness.get(key)
        if value in (None, ""):
            continue
        try:
            return max(minimum, min(maximum, int(float(value))))
        except (TypeError, ValueError):
            continue
    return default

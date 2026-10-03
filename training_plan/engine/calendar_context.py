"""Read planning context from intervals.icu calendar events.

Two kinds of events steer the deterministic planner:

- The Annual Training Plan (ATP) from intervals.icu's plan builder:
  ``TARGET`` events (Monday-anchored weekly ``load_target``/``time_target``/
  ``distance_target``), ``PLAN`` events (phase blocks, phase name as first tag)
  and ``NOTE`` events with ``plan_applied`` (week notes, e.g. recovery weeks).
- Availability events: ``HOLIDAY``, ``SICK`` and ``INJURED`` with
  ``training_availability`` = NORMAL / LIMITED / UNAVAILABLE.

Weekly targets written by this planner itself (see
integrations/intervals_events.py:sync_week_targets) carry the AI tag and are
ignored here, so they never feed back as if the athlete had set them.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from training_plan.core.catalogs import CONSTRAINT_PREFIXES
from training_plan.core.config import AI_TAG

AVAILABILITY_CATEGORIES = {"HOLIDAY", "SICK", "INJURED"}
CONTEXT_CATEGORIES = {"PLAN", "TARGET", "NOTE"} | AVAILABILITY_CATEGORIES
# Used when an availability event has no explicit training_availability. An injury
# without one only blocks the sports it affects (see injuries_by_date).
_DEFAULT_AVAILABILITY = {"SICK": "UNAVAILABLE", "INJURED": "NORMAL", "HOLIDAY": "NORMAL"}
# Plan-builder types planned as strength sessions rather than endurance load ("Other" is
# what the plan builder offers when strength training is not in its list).
STRENGTH_TYPES = {"WeightTraining", "Other"}
STRENGTH_SESSION_MIN = 30
MAX_STRENGTH_PER_WEEK = 3
_RECOVERY_WORDS = ("recovery", "rest week", "deload", "återhämtning", "vila", "erholung", "récupération")


def _day(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value[:19]).date()
    except ValueError:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None


def is_own_event(event: dict) -> bool:
    return AI_TAG in (event.get("description") or "")


def is_constraint_event(event: dict) -> bool:
    """A "Bara: …" / "Ej: …" (Only/Not) event that limits sports over its dates."""
    return (event.get("name") or "").strip().lower().startswith(CONSTRAINT_PREFIXES)


def is_context_event(event: dict) -> bool:
    if is_constraint_event(event):
        return True
    category = (event.get("category") or "").upper()
    if category == "NOTE":
        return bool(event.get("plan_applied"))
    return category in CONTEXT_CATEGORIES


def atp_weeks(events: list[dict]) -> dict[str, dict]:
    """Athlete-set weekly targets keyed by Monday (ISO date), with phase and recovery flag.

    intervals.icu stores weekly targets per sport (one TARGET event per sport and week,
    ``type`` = Ride/Run/…) and optionally one for all activities (no ``type``). Each week
    keeps both: ``total`` (the all-activities target, if any) and ``sports``.
    """
    phases = []
    for e in events:
        if (e.get("category") or "").upper() != "PLAN":
            continue
        start, end = _day(e.get("start_date_local")), _day(e.get("end_date_local"))
        if start:
            tags = e.get("tags") or []
            phases.append((start, end or start, (tags[0] if tags else None) or e.get("name") or "PLAN"))
    notes = [e for e in events if (e.get("category") or "").upper() == "NOTE" and e.get("plan_applied")]

    weeks: dict[str, dict] = {}
    for e in events:
        if (e.get("category") or "").upper() != "TARGET" or is_own_event(e):
            continue
        start = _day(e.get("start_date_local"))
        if start is None:
            continue
        entry = {"load": e.get("load_target"), "time": e.get("time_target"), "distance": e.get("distance_target")}
        if not any(entry.values()):
            continue  # a goal/milestone TARGET (e.g. "FTP 300 W"), not a weekly volume target
        monday = start - timedelta(days=start.weekday())
        week = weeks.get(monday.isoformat())
        if week is None:
            sunday = monday + timedelta(days=6)
            # When phase blocks share a boundary, the week belongs to the phase starting latest.
            matching = [p for p in phases if p[0] <= monday <= p[1]]
            phase = max(matching, key=lambda p: p[0])[2] if matching else None
            recovery = "recovery" in (phase or "").lower()
            for note in notes:
                n_start = _day(note.get("start_date_local"))
                n_end = _day(note.get("end_date_local")) or n_start
                if n_start and n_start <= sunday and n_end >= monday:
                    text = f"{note.get('name') or ''} {note.get('description') or ''}".lower()
                    recovery = recovery or any(word in text for word in _RECOVERY_WORDS)
            week = weeks[monday.isoformat()] = {
                "total": None, "sports": {}, "phase": phase, "recovery": recovery, "name": e.get("name") or "",
            }
        sport = e.get("type")
        if sport:
            week["sports"][sport] = entry
        else:
            week["total"] = entry
    return weeks


def _entry_tss(entry: dict | None, tss_per_hour: float) -> float | None:
    if not entry:
        return None
    if entry.get("load"):
        return float(entry["load"])
    if entry.get("time"):
        return float(entry["time"]) / 3600 * tss_per_hour
    return None  # distance-only target


def _strength_entries(week: dict) -> list[dict]:
    return [e for sport, e in (week.get("sports") or {}).items() if sport in STRENGTH_TYPES]


def week_tss(week: dict, tss_per_hour: float) -> float | None:
    """The week's endurance load target: the all-activities target if set, otherwise the sum per sport.

    Strength targets are planned as strength sessions (see strength_sessions), not as
    endurance load, so they are left out (and taken off an all-activities target).
    """
    strength = sum(t for t in (_entry_tss(e, tss_per_hour) for e in _strength_entries(week)) if t)
    total = _entry_tss(week.get("total"), tss_per_hour)
    if total is not None:
        return max(total - strength, 0.0)
    parts = [t for sport, e in week.get("sports", {}).items()
             if sport not in STRENGTH_TYPES and (t := _entry_tss(e, tss_per_hour)) is not None]
    return sum(parts) if parts else None


def strength_sessions(week: dict, tss_per_hour: float) -> int | None:
    """Strength sessions for the week from its strength target (None when the plan has none).

    Time targets are used directly; a load-only target is turned back into time with the
    athlete's typical load per hour, as the plan builder turned hours into load.
    """
    entries = _strength_entries(week)
    if not entries:
        return None
    minutes = 0.0
    for entry in entries:
        if entry.get("time"):
            minutes += float(entry["time"]) / 60
        elif entry.get("load"):
            minutes += float(entry["load"]) / tss_per_hour * 60
    return max(0, min(round(minutes / STRENGTH_SESSION_MIN), MAX_STRENGTH_PER_WEEK))


def sport_group(sport_type: str | None) -> str:
    """Planner sport group for an intervals.icu activity type.

    All bike types are 'cycling'; skiing on snow and roller skiing are one group, 'ski',
    since they train the same thing and access decides which one is possible.
    """
    t = sport_type or ""
    if "Ride" in t:
        return "cycling"
    if "Run" in t:
        return "Run"
    if t in ("NordicSki", "RollerSki"):
        return "ski"
    return t


def sport_split(week: dict, tss_per_hour: float) -> dict[str, float]:
    """Share of the week's load per sport group from per-sport ATP targets ({} when there are none).

    Strength targets are left out: strength sessions are planned separately.
    """
    loads: dict[str, float] = {}
    for sport, entry in (week.get("sports") or {}).items():
        tss = _entry_tss(entry, tss_per_hour)
        group = sport_group(sport)
        if tss and group and sport not in STRENGTH_TYPES:
            loads[group] = loads.get(group, 0.0) + tss
    total = sum(loads.values())
    return {g: round(v / total, 3) for g, v in loads.items()} if total > 0 else {}


def _event_days(event: dict) -> tuple[date, date] | None:
    start = _day(event.get("start_date_local"))
    if start is None:
        return None
    end = _day(event.get("end_date_local")) or start
    # Calendar ranges ending at midnight belong to the day before.
    if (event.get("end_date_local") or "").endswith("T00:00:00") and end > start:
        end -= timedelta(days=1)
    return start, end


def _availability(event: dict, category: str) -> str:
    return (event.get("training_availability") or _DEFAULT_AVAILABILITY[category]).upper()


def availability_by_date(events: list[dict], dates: list[str]) -> dict[str, tuple[str, str]]:
    """{date: (availability, reason)} for dates covered by HOLIDAY/SICK/INJURED events.

    UNAVAILABLE wins over LIMITED when events overlap. NORMAL is left out.
    """
    wanted = {d: None for d in dates}
    rank = {"UNAVAILABLE": 2, "LIMITED": 1, "NORMAL": 0}
    for e in events:
        category = (e.get("category") or "").upper()
        if category not in AVAILABILITY_CATEGORIES:
            continue
        availability = _availability(e, category)
        span = _event_days(e)
        if availability not in ("UNAVAILABLE", "LIMITED") or span is None:
            continue
        reason = f"{category.lower()}: {e.get('name') or category.title()}"
        for d in dates:
            if span[0] <= date.fromisoformat(d) <= span[1]:
                current = wanted[d]
                if current is None or rank[availability] > rank[current[0]]:
                    wanted[d] = (availability, reason)
    return {d: v for d, v in wanted.items() if v is not None}


def injuries_by_date(events: list[dict], dates: list[str]) -> dict[str, str]:
    """{date: injury text} for INJURED events you can still train around (not UNAVAILABLE).

    The text (name + description, e.g. "Knee" or "Runner's knee, cycling is fine") is
    classified like the morning injury note, so only the affected sports are blocked.
    """
    result: dict[str, str] = {}
    for e in events:
        if (e.get("category") or "").upper() != "INJURED" or _availability(e, "INJURED") == "UNAVAILABLE":
            continue
        span = _event_days(e)
        if span is None:
            continue
        text = " ".join(x for x in (e.get("name"), e.get("description")) if x) or "injury"
        for d in dates:
            if span[0] <= date.fromisoformat(d) <= span[1]:
                result[d] = f"{result[d]}; {text}" if d in result and text not in result[d] else text
    return result


def tss_per_hour(activities: list[dict], default: float = 55.0) -> float:
    """The athlete's typical load per hour, to turn time targets into TSS targets."""
    load = sum(a.get("icu_training_load") or 0 for a in activities[-60:])
    hours = sum((a.get("moving_time") or 0) for a in activities[-60:]) / 3600
    if hours < 5 or load <= 0:
        return default
    return max(30.0, min(load / hours, 90.0))

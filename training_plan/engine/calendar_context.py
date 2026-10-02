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

from training_plan.core.config import AI_TAG

AVAILABILITY_CATEGORIES = {"HOLIDAY", "SICK", "INJURED"}
CONTEXT_CATEGORIES = {"PLAN", "TARGET", "NOTE"} | AVAILABILITY_CATEGORIES
# Used when an availability event has no explicit training_availability.
_DEFAULT_AVAILABILITY = {"SICK": "UNAVAILABLE", "INJURED": "LIMITED", "HOLIDAY": "NORMAL"}
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


def is_context_event(event: dict) -> bool:
    category = (event.get("category") or "").upper()
    if category == "NOTE":
        return bool(event.get("plan_applied"))
    return category in CONTEXT_CATEGORIES


def atp_weeks(events: list[dict]) -> dict[str, dict]:
    """Athlete-set weekly targets keyed by Monday (ISO date), with phase and recovery flag."""
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
        monday = start - timedelta(days=start.weekday())
        sunday = monday + timedelta(days=6)
        load, seconds, meters = e.get("load_target"), e.get("time_target"), e.get("distance_target")
        if not any(v for v in (load, seconds, meters)):
            continue  # a goal/milestone TARGET (e.g. "FTP 300 W"), not a weekly volume target
        # When phase blocks share a boundary, the week belongs to the phase starting latest.
        matching = [p for p in phases if p[0] <= monday <= p[1]]
        phase = max(matching, key=lambda p: p[0])[2] if matching else None
        recovery = False
        for note in notes:
            n_start = _day(note.get("start_date_local"))
            n_end = _day(note.get("end_date_local")) or n_start
            if n_start and n_start <= sunday and n_end >= monday:
                text = f"{note.get('name') or ''} {note.get('description') or ''}".lower()
                recovery = recovery or any(word in text for word in _RECOVERY_WORDS)
        weeks[monday.isoformat()] = {
            "load_target": load,
            "time_target": seconds,
            "distance_target": meters,
            "phase": phase,
            "recovery": recovery or "recovery" in (phase or "").lower(),
            "name": e.get("name") or "",
        }
    return weeks


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
        availability = (e.get("training_availability") or _DEFAULT_AVAILABILITY[category]).upper()
        if availability not in ("UNAVAILABLE", "LIMITED"):
            continue
        start = _day(e.get("start_date_local"))
        if start is None:
            continue
        end = _day(e.get("end_date_local")) or start
        # Calendar ranges ending at midnight belong to the day before.
        if (e.get("end_date_local") or "").endswith("T00:00:00") and end > start:
            end -= timedelta(days=1)
        reason = f"{category.lower()}: {e.get('name') or category.title()}"
        for d in dates:
            day = date.fromisoformat(d)
            if start <= day <= end:
                current = wanted[d]
                if current is None or rank[availability] > rank[current[0]]:
                    wanted[d] = (availability, reason)
    return {d: v for d, v in wanted.items() if v is not None}


def tss_per_hour(activities: list[dict], default: float = 55.0) -> float:
    """The athlete's typical load per hour, to turn time targets into TSS targets."""
    load = sum(a.get("icu_training_load") or 0 for a in activities[-60:])
    hours = sum((a.get("moving_time") or 0) for a in activities[-60:]) / 3600
    if hours < 5 or load <= 0:
        return default
    return max(30.0, min(load / hours, 90.0))

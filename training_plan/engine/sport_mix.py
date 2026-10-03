"""Sport mix: how a week's load is shared between sports when the annual plan only sets total time.

The athlete sets total time per week and the races in intervals.icu; this module decides the
share per sport group ("cycling", "Run", "ski", …):

- Without a goal race the base mix applies (default cycling 60 %, running 25 %, skiing 15 %):
  cycling carries the volume at the lowest injury risk, running and skiing stay in the plan.
- With an A race (else a B race) the race's sport takes over gradually: its share rises from
  the base mix to ``FOCUS_SHARE`` between ``FOCUS_RAMP_START`` and ``FOCUS_RAMP_END`` weeks out
  and stays there until the race. The other sports keep their minimum sessions (planner.py).
- On-demand sports (swimming by default) only appear when a goal race in that sport is close.

The numbers are heuristics: research supports training specifically for the goal and keeping
other sports with about two sessions a week (Spiering et al. 2021), not these exact shares.
Per-sport targets in the annual plan always win over this module.
"""
from __future__ import annotations

import os
from datetime import date, timedelta

from training_plan.core.catalogs import ON_DEMAND_SPORTS
from training_plan.engine.calendar_context import sport_group
from training_plan.engine.utils import race_priority, race_sport


def _parse_mix(text: str) -> dict[str, float]:
    mix = {}
    for part in (text or "").split(","):
        name, _, value = part.partition(":")
        try:
            if name.strip() and float(value) > 0:
                mix[name.strip()] = float(value)
        except ValueError:
            continue
    return mix


BASE_MIX = _parse_mix(os.getenv("BASE_MIX", "cycling:0.6,Run:0.25,ski:0.15"))
FOCUS_SHARE = float(os.getenv("FOCUS_SHARE", "0.65"))
ON_DEMAND_MAX_SHARE = float(os.getenv("ON_DEMAND_MAX_SHARE", "0.35"))
FOCUS_RAMP_START = int(os.getenv("FOCUS_RAMP_START", "20"))   # weeks before the race
FOCUS_RAMP_END = int(os.getenv("FOCUS_RAMP_END", "8"))
ON_DEMAND_GROUPS = {sport_group(s) for s in ON_DEMAND_SPORTS}


def available_groups(sports: list[dict]) -> set[str]:
    """Endurance sport groups the athlete can train (strength and rest excluded)."""
    return {sport_group(s["intervals_type"]) for s in sports
            if s["intervals_type"] not in ("WeightTraining", "Rest")}


def goal_race(races: list, week_start: date) -> tuple[str, date, str] | None:
    """(sport group, race date, name) of the next A race on or after the week, else the next B race."""
    upcoming = []
    for race in races or []:
        try:
            day = date.fromisoformat((race.get("start_date_local") or "")[:10])
        except ValueError:
            continue
        sport = race_sport(race)
        if day < week_start or not sport or race_priority(race) not in ("A", "B"):
            continue
        upcoming.append((race_priority(race), day, sport_group(sport), race.get("name") or "Race"))
    if not upcoming:
        return None
    _, day, group, name = min(upcoming)
    return group, day, name


def focus_weight(weeks_out: float) -> float:
    """0 → base mix, 1 → full focus; linear between FOCUS_RAMP_START and FOCUS_RAMP_END weeks out."""
    if weeks_out <= FOCUS_RAMP_END:
        return 1.0
    if weeks_out >= FOCUS_RAMP_START:
        return 0.0
    return (FOCUS_RAMP_START - weeks_out) / (FOCUS_RAMP_START - FOCUS_RAMP_END)


def week_sport_split(week_start: str, races: list, groups: set[str]) -> tuple[dict[str, float], str]:
    """(share per sport group, focus group or "") for one week."""
    start = date.fromisoformat(week_start)
    base = {g: share for g, share in BASE_MIX.items() if g in groups and g not in ON_DEMAND_GROUPS}
    goal = goal_race(races, start)
    focus, weight = "", 0.0
    if goal and (goal[0] in groups):
        weight = focus_weight((goal[1] - (start + timedelta(days=3))).days / 7)
        if weight > 0:
            focus = goal[0]
    if not focus:
        total = sum(base.values())
        return ({g: round(v / total, 3) for g, v in base.items()} if total else {}), ""

    target = min(FOCUS_SHARE, ON_DEMAND_MAX_SHARE) if focus in ON_DEMAND_GROUPS else FOCUS_SHARE
    others = {g: v for g, v in base.items() if g != focus}
    others_total = sum(others.values()) or 1.0
    base_total = sum(base.values()) or 1.0
    start_share = base.get(focus, 0.0) / base_total
    share = start_share + weight * (target - start_share)
    split = {g: (1 - share) * v / others_total for g, v in others.items()}
    split[focus] = share
    # Key sessions move to the goal sport once the build towards it is well under way.
    return {g: round(v, 3) for g, v in split.items() if v > 0}, (focus if weight >= 0.5 else "")

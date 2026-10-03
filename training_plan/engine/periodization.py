"""Periodization: one load target per calendar week in the planning horizon.

This is the macro layer of the deterministic planner (see docs/TRAINING_MODEL.md).
Every week gets its own target derived from:

- current CTL and a configurable ramp (CTL/week), capped by TARGET_CTL,
- its own position in the 3:1 mesocycle, so a deload week shows up where it
  belongs instead of the current week's factor being applied to the whole horizon,
- taper and recovery around upcoming races (A/B/C priority).

Daily readiness (HRV, sleep) is deliberately *not* an input: it only adjusts
today/tomorrow in the planner. That keeps the week stable and the day flexible.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta

from training_plan.core.config import TARGET_CTL
from training_plan.engine.calendar_context import sport_split, strength_sessions, week_tss
from training_plan.engine.intensity import DEFAULT_HARD_SESSIONS
from training_plan.engine.utils import race_priority

RAMP_CTL_PER_WEEK = float(os.getenv("RAMP_CTL_PER_WEEK", "4.0"))
RAMP_CTL_MAX = float(os.getenv("RAMP_CTL_MAX", "6.0"))
DELOAD_LOAD_FACTOR = float(os.getenv("DELOAD_LOAD_FACTOR", "0.70"))
# Baseline hard sessions per build week; engine/intensity.py adapts it per athlete and week.
KEY_SESSIONS_PER_WEEK = DEFAULT_HARD_SESSIONS

# Ramp multiplier per build week in the block: easier start, harder third week.
_RAMP_BY_WEEK_IN_BLOCK = {1: 0.85, 2: 1.0, 3: 1.15}
# CTL is a 42-day exponentially weighted average of daily load.
_CTL_WEEK_DECAY = (41 / 42) ** 7
# Load factor per day relative to race day (0 = race day, positive = days before,
# negative = days after). Days not listed keep factor 1.0.
_TAPER_FACTORS = {
    "A": {**{d: 0.75 for d in range(8, 15)}, **{d: 0.5 for d in range(1, 8)}, 0: 0.0, -1: 0.4, -2: 0.7},
    "B": {**{d: 0.7 for d in range(1, 5)}, 0: 0.0, -1: 0.6},
    "C": {1: 0.8, 2: 0.9, 0: 0.0},
}


@dataclass
class WeekTarget:
    week_start: str            # Monday, ISO date
    week_in_block: int         # 1-4, where 4 is the deload week
    block_number: int
    kind: str                  # "build" | "deload" | "taper" | "race"
    ctl_start: float
    ramp: float                # planned CTL change for the week (build weeks)
    tss_target: int            # full calendar week
    done_tss: int = 0          # already completed this week (current week only)
    max_key_sessions: int = KEY_SESSIONS_PER_WEEK
    note: str = ""
    source: str = "planner"    # "planner" or "intervals.icu" (the athlete's annual training plan)
    phase: str = ""            # phase name from the annual training plan, if any
    emphasis: str = ""         # "base" or "build": session formats (engine/intensity.py)
    # Share of the load per sport group ("cycling", "Run", …) from the annual plan; {} = planner decides.
    sport_split: dict = field(default_factory=dict)
    strength_sessions: int | None = None   # from the annual plan's strength target; None = default
    focus: str = ""            # sport group the week builds towards (goal race), "" = no goal

    @property
    def remaining_tss(self) -> int:
        return max(self.tss_target - self.done_tss, 0)

    @property
    def is_deload(self) -> bool:
        return self.kind == "deload"

    def contains(self, day: str) -> bool:
        start = date.fromisoformat(self.week_start)
        return start <= date.fromisoformat(day[:10]) < start + timedelta(days=7)

    def summary(self) -> str:
        build = f"build {self.week_in_block}/3" if self.source == "planner" else "build"
        label = {"build": build, "deload": "deload", "taper": "taper", "race": "race week"}
        text = (
            f"Week {self.week_start}: {label.get(self.kind, self.kind)} | target {self.tss_target} TSS"
            f" | key sessions max {self.max_key_sessions}"
        )
        if self.done_tss:
            text += f" | done {self.done_tss}, remaining {self.remaining_tss}"
        if self.sport_split:
            text += " | split " + ", ".join(f"{g} {share:.0%}" for g, share in
                                             sorted(self.sport_split.items(), key=lambda kv: -kv[1]))
        if self.focus:
            text += f" | focus {self.focus}"
        if self.strength_sessions is not None:
            text += f" | strength {self.strength_sessions}"
        if self.note:
            text += f" | {self.note}"
        if self.source != "planner":
            text += f" | from {self.source}"
        return text


def monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def done_tss_this_week(activities: list, today: date) -> int:
    """TSS already completed from Monday up to and including today."""
    start = monday_of(today).isoformat()
    end = today.isoformat()
    return round(sum(
        a.get("icu_training_load") or 0
        for a in activities
        if start <= (a.get("start_date_local") or "")[:10] <= end
    ))


def _race_days(races: list, today: date) -> list[tuple[date, str, str]]:
    result = []
    for race in races or []:
        raw = (race.get("start_date_local") or "")[:10]
        try:
            race_date = datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            continue
        if race_date >= today - timedelta(days=2):
            result.append((race_date, race_priority(race), race.get("name") or "Race"))
    return result


def _day_factor(day: date, race_days: list[tuple[date, str, str]]) -> tuple[float, str]:
    """Lowest load factor any race imposes on this day, plus a short reason."""
    factor, reason = 1.0, ""
    for race_date, priority, name in race_days:
        offset = (race_date - day).days
        f = _TAPER_FACTORS[priority].get(offset)
        if f is not None and f < factor:
            factor = f
            if offset > 0:
                reason = f"taper before {name} ({priority})"
            elif offset == 0:
                reason = f"race {name} ({priority})"
            else:
                reason = f"recovery after {name}"
    return factor, reason


def _base_ramp(ctl: float, trajectory: dict | None) -> float:
    ramp = RAMP_CTL_PER_WEEK
    if trajectory and trajectory.get("has_target") and trajectory.get("ramp_per_week") is not None:
        ramp = float(trajectory["ramp_per_week"])
    return max(0.0, min(ramp, RAMP_CTL_MAX))


def build_week_targets(
    ctl: float,
    mesocycle: dict,
    today: date,
    *,
    weeks: int = 4,
    races: list | None = None,
    trajectory: dict | None = None,
    tsb: float | None = None,
    done_tss: int = 0,
    target_ctl: float | None = None,
) -> list[WeekTarget]:
    """Weekly TSS targets for `weeks` calendar weeks starting with the current one."""
    target_ctl = TARGET_CTL if target_ctl is None else target_ctl
    ctl = max(float(ctl or 0), 1.0)
    week_in_block = int(mesocycle.get("week_in_block", 1))
    block_number = int(mesocycle.get("block_number", 1))
    base_ramp = _base_ramp(ctl, trajectory)
    race_days = _race_days(races, today)
    # Very negative TSB (beyond -30% of CTL) means the athlete is already overreaching:
    # hold the current week at maintenance instead of ramping further.
    fatigued = tsb is not None and tsb < -0.30 * ctl

    targets: list[WeekTarget] = []
    for index in range(weeks):
        week_start = monday_of(today) + timedelta(days=7 * index)
        if week_in_block == 4:
            kind, ramp = "deload", 0.0
            daily = ctl * DELOAD_LOAD_FACTOR
            max_key = 0
            note = mesocycle.get("deload_reason", "") if index == 0 else "planned deload"
        else:
            kind = "build"
            ramp = base_ramp * _RAMP_BY_WEEK_IN_BLOCK.get(week_in_block, 1.0)
            ramp = min(ramp, max(target_ctl - ctl, 0.0))  # never plan past TARGET_CTL
            if index == 0 and fatigued:
                ramp = 0.0
            daily = ctl + ramp * 6.0  # ΔCTL/week ≈ (daily load − CTL) / 6
            max_key = KEY_SESSIONS_PER_WEEK
            note = "maintenance (high fatigue)" if index == 0 and fatigued else ""

        factors = [_day_factor(week_start + timedelta(days=d), race_days) for d in range(7)]
        reasons = [reason for _, reason in factors if reason]
        if reasons:
            race_in_week = any(r.startswith("race") for r in reasons)
            if race_in_week:
                kind, max_key = "race", 0
            elif len(reasons) >= 3 and kind != "deload":
                kind, max_key = "taper", min(max_key, 1)
            note = "; ".join(dict.fromkeys(reasons))
        tss = round(daily * sum(f for f, _ in factors))

        targets.append(WeekTarget(
            week_start=week_start.isoformat(),
            week_in_block=week_in_block,
            block_number=block_number,
            kind=kind,
            ctl_start=round(ctl, 1),
            ramp=round(ramp, 1),
            tss_target=tss,
            done_tss=done_tss if index == 0 else 0,
            max_key_sessions=max_key,
            note=note,
        ))

        avg_daily = tss / 7
        ctl = avg_daily + (ctl - avg_daily) * _CTL_WEEK_DECAY
        block_number += 1 if week_in_block == 4 else 0
        week_in_block = (week_in_block % 4) + 1
    return targets


def apply_calendar_targets(targets: list[WeekTarget], atp: dict[str, dict], tss_per_hour: float) -> list[WeekTarget]:
    """Let the athlete's annual training plan in intervals.icu set the weekly targets.

    Weeks with an ATP target take its load (or time × typical TSS/hour); our own mesocycle is
    then ignored for those weeks. Two safety rules still apply:

    - a recovery week is recognised from the ATP week note or from the target dropping at least
      20% below the previous ATP week;
    - the target never asks for more than ``RAMP_CTL_MAX`` CTL/week of ramp from the current
      fitness, so a plan built for more hours than the body is used to is phased in instead.

    Race and taper handling from the calendar's races is kept. Weeks without an ATP target are
    unchanged.
    """
    result = []
    ctl = targets[0].ctl_start if targets else 0.0
    for target in targets:
        week = atp.get(target.week_start)
        weekly = week_tss(week, tss_per_hour) if week else None
        strength = strength_sessions(week, tss_per_hour) if week else None
        if weekly is None:
            # No ATP week, or only distance/strength targets: keep our own load target.
            result.append(replace(target, strength_sessions=strength) if strength is not None else target)
            ctl = _advance_ctl(ctl, target.tss_target)
            continue
        previous = atp.get((date.fromisoformat(target.week_start) - timedelta(days=7)).isoformat())
        previous_tss = week_tss(previous, tss_per_hour) if previous else None
        recovery = bool(week.get("recovery")) or bool(previous_tss and weekly <= 0.8 * previous_tss)

        notes = [f"ATP phase: {week['phase']}"] if week.get("phase") else []
        tss = weekly
        cap = (ctl + RAMP_CTL_MAX * 6.0) * 7
        if tss > cap:
            notes.append(f"capped from {round(tss)} TSS (max +{RAMP_CTL_MAX:g} CTL/week)")
            tss = cap
        kind, max_key = target.kind, target.max_key_sessions
        if kind in ("race", "taper"):
            notes.append(target.note)
        else:
            kind = "deload" if recovery else "build"
            max_key = 0 if kind == "deload" else KEY_SESSIONS_PER_WEEK
        result.append(WeekTarget(
            week_start=target.week_start,
            week_in_block=target.week_in_block,
            block_number=target.block_number,
            kind=kind,
            ctl_start=round(ctl, 1),
            ramp=0.0,
            tss_target=round(tss),
            done_tss=target.done_tss,
            max_key_sessions=max_key,
            note="; ".join(n for n in notes if n),
            source="intervals.icu",
            phase=week.get("phase") or "",
            sport_split=sport_split(week, tss_per_hour),
            strength_sessions=strength,
        ))
        ctl = _advance_ctl(ctl, tss)
    return result


def _advance_ctl(ctl: float, week_tss_value: float) -> float:
    avg_daily = week_tss_value / 7
    return avg_daily + (ctl - avg_daily) * _CTL_WEEK_DECAY


def format_week_targets(targets: list[WeekTarget]) -> str:
    return "\n".join(f"  {t.summary()}" for t in targets)

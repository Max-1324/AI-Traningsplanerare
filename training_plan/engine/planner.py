"""Deterministic planner: weekly targets → week skeleton → concrete sessions.

This is the core of the "deterministic core, AI at the edge" design described in
docs/TRAINING_MODEL.md. It produces a complete plan that respects every safety
rule *without any AI call*:

1. ``periodization.build_week_targets`` gives each calendar week a TSS target,
   a key-session cap and its deload/taper/race status.
2. ``skeleton.build_week_skeleton`` assigns day roles (key, long, easy, rest).
3. This module turns roles into sessions: key sessions from the workout library
   at the athlete's progression level, a long session, strength, and aerobic
   volume sized to hit the weekly target. Weather, injuries, ACWR warnings,
   sport budgets, calendar constraints, locked days, race week and
   Return-to-Play are handled here, before anything is saved.

Every session also gets a few equivalent alternatives (same day, similar load).
The optional AI step may only choose between those and write the texts.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import lru_cache

from training_plan.core.catalogs import MIN_DURATION_BY_SPORT, SPORTS
from training_plan.core.models import AIPlan, PlanDay, StrengthStep, WorkoutStep
from training_plan.engine.libraries import STRENGTH_LIBRARY
from training_plan.engine.periodization import WeekTarget, format_week_targets, monday_of
from training_plan.engine.planning.learning import WORKOUT_LIBRARY
from training_plan.engine.planning.workouts import get_strength_workout_for_phase
from training_plan.engine.postprocess.injury import rehab_session
from training_plan.engine.postprocess.load import estimate_tss_coggan
from training_plan.engine.skeleton import build_week_skeleton
from training_plan.engine.validation.structure import _is_hard_day

STRENGTH_PER_WEEK = int(os.getenv("STRENGTH_PER_WEEK", "1"))
MAX_STRENGTH_PER_PLAN = int(os.getenv("MAX_STRENGTH_PER_PLAN", "2"))
MIN_STRENGTH_GAP_DAYS = int(os.getenv("MIN_STRENGTH_GAP_DAYS", "2"))
MAX_ROLLSKI_PER_WEEK = int(os.getenv("MAX_ROLLSKI_PER_WEEK", "1"))
SECONDARY_SESSIONS_PER_WEEK = int(os.getenv("SECONDARY_SESSIONS_PER_WEEK", "1"))
LONG_SESSION_SHARE = float(os.getenv("LONG_SESSION_SHARE", "0.35"))
# The rest of the current week may carry at most this much more than its pro-rata
# share of the weekly target: missed sessions are never "caught up".
CATCH_UP_CAP = float(os.getenv("CATCH_UP_CAP", "1.15"))
# Longest aerobic session on a weekday (Mon-Fri); weekends allow longer sessions.
WEEKDAY_MAX_MIN = int(os.getenv("WEEKDAY_MAX_MIN", "120"))

_CYCLING = ("Ride", "VirtualRide")
_MAX_FILL_MIN = {"Ride": 150, "VirtualRide": 120, "Run": 75, "RollerSki": 90, "NordicSki": 120, "Swim": 60}
_MAX_LONG_MIN = {"Ride": 300, "VirtualRide": 150, "Run": 120, "RollerSki": 150, "NordicSki": 180, "Swim": 75}
_MIN_LONG_MIN = 90
_EASY_DAY_MAX_MIN = 90        # the day after a key session
_LONG_EXTENSION_MIN = 30      # how far past the library level a long session may grow
_RESTRICTED_MAX_MIN = 60      # today/tomorrow with low readiness
_STRENGTH_MIN = 30
_SLOT_RANK = {"AM": 0, "MAIN": 1, "PM": 2}

_FOCUS_TO_KEY = {
    "threshold": "threshold_intervals",
    "polarization": "threshold_intervals",
    "vo2": "vo2max_intervals",
    "pacing": "tempo_sustained",
    "durability": "tempo_sustained",
    "fueling": "tempo_sustained",
}
_PHASE_ROTATION = {
    "Base": ["threshold_intervals", "tempo_sustained"],
    "Build": ["threshold_intervals", "vo2max_intervals"],
    "Taper": ["threshold_intervals"],
    "Race Week": ["threshold_intervals"],
}
# Interval formats for sports without power-based library sessions (run, roller ski …).
_GENERIC_KEY = {
    "threshold_intervals": ("Threshold intervals – 4×6min Z4", [(15, "Z2", "Warm-up")] + [
        step for _ in range(4) for step in ((6, "Z4", "Threshold rep – even effort"), (2, "Z1", "Easy"))
    ][:-1] + [(10, "Z1", "Cool-down")]),
    "vo2max_intervals": ("VO2max intervals – 5×3min Z5", [(15, "Z2", "Warm-up")] + [
        step for _ in range(5) for step in ((3, "Z5", "VO2max rep – hard but controlled"), (3, "Z1", "Easy"))
    ][:-1] + [(10, "Z1", "Cool-down")]),
    "tempo_sustained": ("Tempo session – 2×15min Z3", [
        (15, "Z2", "Warm-up"), (15, "Z3", "Tempo block 1"), (5, "Z1", "Easy"),
        (15, "Z3", "Tempo block 2"), (10, "Z1", "Cool-down"),
    ]),
}


def session_key(day: PlanDay) -> str:
    return f"{day.date}|{day.slot}"


def _sort_key(day: PlanDay) -> tuple:
    return day.date, _SLOT_RANK.get(day.slot, 1)


def _steps(parts) -> list[WorkoutStep]:
    return [WorkoutStep(duration_min=d, zone=z, description=desc) for d, z, desc in parts if d > 0]


def _round5(minutes: float) -> int:
    return int(5 * round(minutes / 5))


def tss_of(day: PlanDay) -> float:
    return estimate_tss_coggan(day, {})


# ── Session factories ──────────────────────────────────────────────────────────

def endurance_session(day_str: str, sport: str, minutes: int, *, slot: str = "MAIN",
                      zone: str = "Z2", title: str | None = None, description: str = "") -> PlanDay:
    minutes = int(minutes)
    edge = 10 if minutes >= 40 else 5
    main = max(minutes - 2 * edge, 1)
    main_text = "Steady aerobic – conversational pace" if zone == "Z2" else "Very easy – keep it relaxed"
    steps = _steps([(edge, "Z1", "Warm-up – easy"), (main, zone, main_text), (edge, "Z1", "Cool-down")])
    total = sum(s.duration_min for s in steps)
    if title is None:
        title = f"Endurance {zone} – {total} min" if zone == "Z2" else f"Recovery – {total} min"
    if not description:
        description = (
            "Easy aerobic volume in Z2 – you should be able to talk in full sentences. "
            "Builds the aerobic base without adding much fatigue."
        ) if zone == "Z2" else "Truly easy session to promote recovery. Stop early if you feel worse."
    return PlanDay(date=day_str, title=title, intervals_type=sport, duration_min=total,
                   workout_steps=steps, slot=slot, description=description)


def library_session(day_str: str, wk_key: str, level: int, sport: str, *, slot: str = "MAIN",
                    focus: str = "") -> PlanDay:
    wk = WORKOUT_LIBRARY[wk_key]
    lvl = wk["levels"][max(1, min(level, len(wk["levels"]))) - 1]
    parts = [(s["d"], s["z"], s["desc"]) for s in lvl["steps"]]
    # Make sure every session ends with an easy cool-down.
    last_d, last_z, last_desc = parts[-1]
    if last_z != "Z1" and last_d > 20:
        parts[-1] = (last_d - 10, last_z, last_desc)
        parts.append((10, "Z1", "Cool-down"))
    steps = _steps(parts)
    purpose = f" Focus: {focus}." if focus else ""
    return PlanDay(
        date=day_str,
        title=f"{wk['name']} – {lvl['label']}",
        intervals_type=sport,
        duration_min=sum(s.duration_min for s in steps),
        workout_steps=steps,
        slot=slot,
        description=(
            f"KEY SESSION (level {lvl['level']}/{len(wk['levels'])}).{purpose} "
            "Keep the effort even across all reps; the last one should feel hard but controlled (RPE 7-8). "
            "If you complete it at RPE ≤ 7 the next level unlocks."
        ),
    )


def generic_key_session(day_str: str, wk_key: str, sport: str, *, slot: str = "MAIN", focus: str = "") -> PlanDay:
    title, parts = _GENERIC_KEY[wk_key]
    steps = _steps(parts)
    purpose = f" Focus: {focus}." if focus else ""
    return PlanDay(date=day_str, title=title, intervals_type=sport,
                   duration_min=sum(s.duration_min for s in steps), workout_steps=steps, slot=slot,
                   description=f"KEY SESSION.{purpose} Even effort across all reps, RPE 7-8 on the last one.")


def ftp_test_session(day_str: str, sport: str, *, slot: str = "MAIN") -> PlanDay:
    steps = _steps([
        (10, "Z1", "Warm-up – easy"), (5, "Z2", "Build gradually"), (2, "Z4", "Opener"),
        (3, "Z1", "Easy"), (15, "Z5", "Ramp: +20 W/min from ~50% FTP until exhaustion"),
        (10, "Z1", "Cool-down"),
    ])
    return PlanDay(date=day_str, title="FTP ramp test", intervals_type=sport, duration_min=45,
                   workout_steps=steps, slot=slot,
                   description=("Benchmark: ramp test to recalibrate zones. Do it rested. "
                                "FTP ≈ 75% of the average power of the last full minute."))


def strength_session(day_str: str, program: dict, *, slot: str = "AM", minutes: int = _STRENGTH_MIN) -> PlanDay:
    # PlanDay coerces strength_steps from dicts, so pass validated dicts.
    steps = [
        StrengthStep(exercise=e["exercise"], sets=e["sets"], reps=str(e["reps"]),
                     rest_sec=e.get("rest_sec"), notes=e.get("notes")).model_dump()
        for e in program["exercises"]
    ]
    return PlanDay(date=day_str, title=f"Strength – {program['name']}", intervals_type="WeightTraining",
                   duration_min=minutes, strength_steps=steps, slot=slot,
                   description="Bodyweight strength for economy and injury resilience. Controlled tempo, no failure.")


def rest_day(day_str: str, title: str = "Rest", description: str = "") -> PlanDay:
    return PlanDay(date=day_str, title=title, intervals_type="Rest", duration_min=0,
                   description=description or "Rest day – adaptation happens during recovery. Light walk or mobility is fine.")


def protocol_day(entry: dict, sport_ok) -> PlanDay:
    """Race-week protocol template (engine/analysis/strategy.py) → PlanDay."""
    sport = entry.get("type") or "Rest"
    if sport == "Rest" or not entry.get("dur") or not entry.get("steps") or not sport_ok(sport):
        return rest_day(entry["date"], title=entry.get("title", "Rest"), description=entry.get("desc", ""))
    steps = _steps((s["d"], s["z"], s["desc"]) for s in entry["steps"])
    return PlanDay(date=entry["date"], title=entry["title"], intervals_type=sport,
                   duration_min=sum(s.duration_min for s in steps), workout_steps=steps,
                   slot=entry.get("slot", "MAIN"), description=entry.get("desc", ""))


@lru_cache(maxsize=None)
def _endurance_tss(sport: str, minutes: int) -> float:
    return tss_of(endurance_session("2000-01-03", sport, minutes))


def minutes_for_tss(sport: str, tss: float, lo: int, hi: int) -> int:
    """Shortest endurance duration (5-min steps within [lo, hi]) reaching `tss`."""
    lo, hi = _round5(lo), max(_round5(hi), _round5(lo))
    for minutes in range(lo, hi + 1, 5):
        if _endurance_tss(sport, minutes) >= tss:
            return minutes
    return hi


# ── Planner ────────────────────────────────────────────────────────────────────

@dataclass
class PlannerInputs:
    today: date
    horizon_dates: list[str]
    week_targets: list[WeekTarget]
    phase: str = "Base"
    mesocycle: dict = field(default_factory=dict)
    workout_levels: dict = field(default_factory=dict)
    primary_sport: str = "VirtualRide"
    weather: list = field(default_factory=list)
    constraints: list = field(default_factory=list)
    locked_dates: set = field(default_factory=set)
    base_tss_by_date: dict = field(default_factory=dict)
    sport_budgets: dict = field(default_factory=dict)
    avoid_sports: set = field(default_factory=set)
    restricted_dates: set = field(default_factory=set)
    restriction_reason: str = ""
    time_available_today: int | None = None
    race_week: dict | None = None
    rtp_status: dict | None = None
    focus_areas: list = field(default_factory=list)
    ftp_test_due: bool = False
    burnout: bool = False
    done_today: bool = False
    intensity_done_this_week: int = 0
    yesterday_was_hard: bool = False
    injury: dict | None = None
    injury_note: str = ""
    available_sports: list | None = None


@dataclass
class PlannerResult:
    plan: AIPlan
    options: dict[str, list[PlanDay]]
    roles: dict[str, str]
    horizon_tss_target: float
    max_hard_days: int
    week_targets: list[WeekTarget]
    notes: list[str] = field(default_factory=list)


class _Planner:
    def __init__(self, inp: PlannerInputs):
        self.inp = inp
        catalog = inp.available_sports if inp.available_sports is not None else SPORTS
        self.catalog = [s for s in catalog if s["intervals_type"] not in ("Rest",)]
        self.sports = {s["intervals_type"] for s in self.catalog} - set(inp.avoid_sports)
        self.weather = {w.get("date"): w for w in inp.weather or []}
        self.constraints: dict[str, list[dict]] = {}
        for c in inp.constraints or []:
            self.constraints.setdefault(c.get("date", ""), []).append(c)
        self.horizon = set(inp.horizon_dates)
        self.budget_used: dict[str, int] = {}
        self.rollski_by_week: dict[str, int] = {}
        self.strength_in_horizon = 0
        self.notes: list[str] = []
        self.roles: dict[str, str] = {}
        self.options: dict[str, list[PlanDay]] = {}
        self._long_cap: dict[str, int] = {}
        self._ftp_planned = False
        injury = inp.injury or {}
        self.injury_cap = injury.get("duration_cap") if injury.get("severity") in ("MODERATE", "SEVERE") else None
        self.primary = self._effective_primary()

    # ── sport helpers ─────────────────────────────────────────────────────────
    def allowed(self, sport: str, day: str) -> bool:
        if sport not in self.sports:
            return False
        for c in self.constraints.get(day, []):
            if c.get("allowed_types") and sport not in c["allowed_types"]:
                return False
            if sport in (c.get("blocked_types") or []):
                return False
        return True

    def outdoor_ok(self, day: str, slot: str = "MAIN") -> bool:
        w = self.weather.get(day)
        if not w:
            return True
        if slot == "AM":
            rain, temp = w.get("rain_morning_mm", 0), w.get("temp_morning", w.get("temp_min"))
        else:
            rain, temp = w.get("rain_afternoon_mm", w.get("rain_mm", 0)), w.get("temp_afternoon", w.get("temp_max"))
        return (rain or 0) < 5 and (temp is None or temp >= 2)

    def outdoor_slot(self, day: str) -> str | None:
        if self.outdoor_ok(day, "MAIN"):
            return "MAIN"
        if self.outdoor_ok(day, "AM"):
            return "AM"
        return None

    def budget_left(self, sport: str) -> float:
        b = self.inp.sport_budgets.get(sport)
        if not b:
            return float("inf")
        return b.get("remaining", 0) - self.budget_used.get(sport, 0)

    def min_minutes(self, sport: str) -> int:
        return max(MIN_DURATION_BY_SPORT.get(sport, 30), 30)

    def _effective_primary(self) -> str:
        """The athlete's main sport, or a safe replacement when it is avoided (injury, ACWR)."""
        if self.inp.primary_sport in self.sports:
            return self.inp.primary_sport
        replacement = (self.inp.injury or {}).get("replacement")
        for sport in (replacement, "VirtualRide", "Ride"):
            if sport and sport in self.sports:
                return sport
        endurance = [s["intervals_type"] for s in self.catalog
                     if s["intervals_type"] in self.sports and s["intervals_type"] != "WeightTraining"]
        return endurance[0] if endurance else self.inp.primary_sport

    def cycling_primary(self) -> bool:
        return self.primary in _CYCLING or not (self.sports - set(_CYCLING) - {"WeightTraining"})

    def endurance_choice(self, day: str, minutes: int, max_min: int,
                         week_start: str | None = None) -> tuple[str, int, str] | None:
        """(sport, minutes, slot) for an aerobic session on `day`, or None if nothing fits."""
        if self.cycling_primary():
            slot = self.outdoor_slot(day)
            candidates = []
            if slot and self.allowed("Ride", day):
                candidates.append(("Ride", slot))
            if self.allowed("VirtualRide", day):
                candidates.append(("VirtualRide", "MAIN"))
            if not slot and self.allowed("Ride", day) and not self.allowed("VirtualRide", day):
                candidates.append(("Ride", "MAIN"))
        else:
            candidates = [(self.primary, "MAIN")] if self.allowed(self.primary, day) else []
        for sport in (s["intervals_type"] for s in self.catalog):
            if sport not in _CYCLING and sport not in ("WeightTraining",) and all(sport != c[0] for c in candidates):
                if self.allowed(sport, day):
                    candidates.append((sport, "MAIN"))
        for sport, slot in candidates:
            if sport == "RollerSki" and week_start and self.rollski_by_week.get(week_start, 0) >= MAX_ROLLSKI_PER_WEEK:
                continue
            lo = self.min_minutes(sport)
            hi = min(max_min, _MAX_FILL_MIN.get(sport, 90), self.budget_left(sport))
            if hi >= lo:
                return sport, max(lo, min(minutes, hi)), slot
        return None

    def key_sport(self, day: str, wk_key: str | None) -> str | None:
        if self.cycling_primary():
            allowed_by_lib = WORKOUT_LIBRARY.get(wk_key, {}).get("sport", list(_CYCLING)) if wk_key else list(_CYCLING)
            for sport in ("VirtualRide", "Ride"):
                if sport in allowed_by_lib and self.allowed(sport, day):
                    if sport == "Ride" and not self.outdoor_slot(day):
                        continue
                    return sport
            return None
        sport = self.primary
        return sport if self.allowed(sport, day) else None

    # ── session builders ──────────────────────────────────────────────────────
    def key_sequence(self) -> list[str]:
        seq = []
        for area in self.inp.focus_areas:
            k = _FOCUS_TO_KEY.get(area)
            if k and k not in seq:
                seq.append(k)
        for k in _PHASE_ROTATION.get(self.inp.phase, _PHASE_ROTATION["Base"]):
            if k not in seq:
                seq.append(k)
        return seq

    def make_key(self, day: str, wk_key: str, focus: str = "") -> PlanDay | None:
        sport = self.key_sport(day, wk_key)
        if not sport:
            return None
        if sport in _CYCLING:
            level = int(self.inp.workout_levels.get(wk_key, 1))
            session = library_session(day, wk_key, level, sport, focus=focus)
        else:
            session = generic_key_session(day, wk_key, sport, focus=focus)
        if sport == "Ride":
            session = session.model_copy(update={"slot": self.outdoor_slot(day) or "MAIN"})
        return session

    def make_long(self, day: str, week_target: float, budget: float, cap: int | None = None) -> PlanDay | None:
        if self.cycling_primary():
            outdoor = self.outdoor_slot(day)
            sport = "Ride" if outdoor and self.allowed("Ride", day) else (
                "VirtualRide" if self.allowed("VirtualRide", day) else None)
            slot = outdoor if sport == "Ride" else "MAIN"
        else:
            sport, slot = (self.primary, "MAIN") if self.allowed(self.primary, day) else (None, "MAIN")
        if not sport:
            return None
        hi = min(self._long_level_minutes(sport), _MAX_LONG_MIN.get(sport, 150), self.budget_left(sport), cap or 10_000)
        if budget < 0.8 * _endurance_tss(sport, _MIN_LONG_MIN):
            return None  # the week's load is already covered
        share_tss = min(LONG_SESSION_SHARE * week_target, budget)
        minutes = minutes_for_tss(sport, share_tss, _MIN_LONG_MIN, hi)
        if minutes < max(_MIN_LONG_MIN, self.min_minutes(sport)) or hi < _MIN_LONG_MIN:
            return None
        hours, mins = divmod(minutes, 60)
        length = f"{hours}h{mins:02d}" if mins else f"{hours}h"
        return endurance_session(
            day, sport, minutes, slot=slot, title=f"Long endurance – {length}",
            description=("LONG SESSION: steady Z2 the whole way. Practise race nutrition "
                         "(60-90 g carbohydrate per hour) and stay relaxed on the climbs."),
        )

    def strength_program(self, week_target: WeekTarget) -> dict:
        meso = {"week_in_block": week_target.week_in_block, "is_deload": week_target.is_deload,
                "phase_name": "Taper" if week_target.kind in ("taper", "race") else self.inp.phase}
        return get_strength_workout_for_phase(meso)

    # ── alternatives ──────────────────────────────────────────────────────────
    def alternatives(self, chosen: PlanDay, role: str, week_start: str) -> list[PlanDay]:
        alts: list[PlanDay] = []
        target_tss = tss_of(chosen)
        if role == "key" and chosen.intervals_type in _CYCLING:
            for wk_key in self.key_sequence():
                if wk_key in chosen.title.lower() or WORKOUT_LIBRARY[wk_key]["name"] in chosen.title:
                    continue
                alt = self.make_key(chosen.date, wk_key)
                if alt is not None and alt.slot == chosen.slot:
                    alts.append(alt)
                    break
        elif role in ("fill", "long"):
            for sport in [s["intervals_type"] for s in self.catalog]:
                if sport in ("WeightTraining", chosen.intervals_type) or not self.allowed(sport, chosen.date):
                    continue
                if sport == "Ride" and not self.outdoor_ok(chosen.date, chosen.slot):
                    continue
                if sport == "RollerSki" and self.rollski_by_week.get(week_start, 0) >= MAX_ROLLSKI_PER_WEEK:
                    continue
                lo = self.min_minutes(sport)
                hi = min(_MAX_LONG_MIN.get(sport, 120) if role == "long" else _MAX_FILL_MIN.get(sport, 90),
                         self.budget_left(sport), self.injury_cap or 10_000)
                if hi < lo:
                    continue
                minutes = minutes_for_tss(sport, target_tss, lo, hi)
                alt = endurance_session(chosen.date, sport, minutes, slot=chosen.slot,
                                        title=chosen.title.replace(chosen.intervals_type, sport) if role == "long" else None,
                                        description=chosen.description)
                if abs(tss_of(alt) - target_tss) <= 0.25 * max(target_tss, 1):
                    alts.append(alt)
                if len(alts) >= 2:
                    break
        elif role == "strength":
            for key in ("cycling_strength", "runner_strength", "general_strength"):
                program = STRENGTH_LIBRARY[key]
                if program["name"] not in chosen.title:
                    alts.append(strength_session(chosen.date, program, slot=chosen.slot,
                                                 minutes=chosen.duration_min))
                if len(alts) >= 2:
                    break
        return alts

    # ── week planning ─────────────────────────────────────────────────────────
    def plan_week(self, target: WeekTarget, slots: list) -> list[tuple[PlanDay, str]]:
        inp = self.inp
        week_dates = [s.date for s in slots]
        planned: dict[str, list[tuple[PlanDay, str]]] = {d: [] for d in week_dates}
        role_of = {s.date: (s.suggested_type or "open") for s in slots}
        locked = {d for d in week_dates if d in inp.locked_dates}
        if inp.done_today and inp.today.isoformat() in week_dates:
            locked.add(inp.today.isoformat())

        budget = float(target.remaining_tss)
        if target.week_start == monday_of(inp.today).isoformat():
            budget = min(budget, target.tss_target * len(week_dates) / 7 * CATCH_UP_CAP)
        budget -= sum(inp.base_tss_by_date.get(d, 0) for d in week_dates)
        if inp.burnout:
            budget *= 0.8
            self.notes.append("Burnout risk: volume lowered 20%.")

        def add(day: PlanDay, role: str, reserved: bool = False):
            planned[day.date].append((day, role))
            if not reserved:  # _fill books its own usage while choosing sessions
                if day.intervals_type in inp.sport_budgets:
                    self.budget_used[day.intervals_type] = self.budget_used.get(day.intervals_type, 0) + day.duration_min
                if day.intervals_type == "RollerSki":
                    self.rollski_by_week[target.week_start] = self.rollski_by_week.get(target.week_start, 0) + 1
            if day.intervals_type == "WeightTraining" and day.date in self.horizon:
                self.strength_in_horizon += 1

        def cap_for(day: str, default: int) -> int:
            cap = default
            if date.fromisoformat(day).weekday() < 5:
                cap = min(cap, WEEKDAY_MAX_MIN)
            if day in inp.restricted_dates:
                cap = min(cap, _RESTRICTED_MAX_MIN)
            if self.injury_cap:
                cap = min(cap, self.injury_cap)
            if day == inp.today.isoformat() and inp.time_available_today is not None:
                cap = min(cap, inp.time_available_today)
            return cap

        free = [d for d in week_dates if d not in locked]

        # 1) Fixed days: race-week protocol and Return-to-Play.
        protocol = {p["date"]: p for p in (inp.race_week or {}).get("protocol", [])} if (inp.race_week or {}).get("is_active") else {}
        for d in list(free):
            if d in protocol:
                day = protocol_day(protocol[d], lambda s, _d=d: self.allowed(s, _d))
                if day.duration_min > cap_for(d, 10_000):
                    cap = cap_for(d, 10_000)
                    day = (endurance_session(d, day.intervals_type, cap, slot=day.slot)
                           if cap >= self.min_minutes(day.intervals_type) else rest_day(d, title=day.title))
                add(day, "fixed")
                budget -= tss_of(day)
                free.remove(d)
        rtp_days = self._rtp_days(free)
        for d, day in rtp_days.items():
            add(day, "fixed")
            budget -= tss_of(day)
            free.remove(d)

        # 2) Key sessions on intensity slots.
        sequence = self.key_sequence()
        key_count = 0
        for d in [d for d in free if role_of[d] == "intensity"]:
            session = None
            if inp.ftp_test_due and not self._ftp_planned and self.cycling_primary():
                sport = self.key_sport(d, None)
                if sport:
                    session = ftp_test_session(d, sport, slot=self.outdoor_slot(d) or "MAIN" if sport == "Ride" else "MAIN")
                    self._ftp_planned = True
            if session is None:
                wk_key = sequence[key_count % len(sequence)] if sequence else None
                focus = next((a for a in inp.focus_areas if _FOCUS_TO_KEY.get(a) == wk_key), "")
                session = self.make_key(d, wk_key, focus=focus) if wk_key else None
            if (session is None or tss_of(session) > budget * 1.25 or session.duration_min > cap_for(d, 10_000)
                    or session.duration_min > self.budget_left(session.intervals_type)):
                role_of[d] = "easy"   # no suitable key session: treat as an easy day
                continue
            add(session, "key")
            budget -= tss_of(session)
            key_count += 1
            free.remove(d)

        # 3) Long session.
        for d in [d for d in free if role_of[d] == "long_endurance"]:
            long_day = self.make_long(d, target.tss_target, budget, cap=cap_for(d, 10_000))
            if long_day is None:
                role_of[d] = "open"
                continue
            add(long_day, "long")
            budget -= tss_of(long_day)
            free.remove(d)
            self._long_cap[long_day.date] = min(
                self._long_level_minutes(long_day.intervals_type) + _LONG_EXTENSION_MIN,
                _MAX_LONG_MIN.get(long_day.intervals_type, 150),
                cap_for(d, 10_000),
            )

        # 4) Strength (AM, on easy days that are not right before a key or long day).
        n_strength = 0 if target.kind == "race" else (min(1, STRENGTH_PER_WEEK) if target.is_deload else STRENGTH_PER_WEEK)
        if "WeightTraining" in self.sports and n_strength > 0:
            program = self.strength_program(target)
            heavy = {day.date for items in planned.values() for day, role in items if role in ("key", "long")}
            strength_dates: list[date] = []
            preference = {"easy": 0, "open": 1, "rest_or_easy": 2}
            candidates = sorted(
                (d for d in free if role_of[d] in preference and d not in inp.restricted_dates
                 and (date.fromisoformat(d) + timedelta(days=1)).isoformat() not in heavy
                 and self.allowed("WeightTraining", d)
                 and not (d == inp.today.isoformat() and inp.time_available_today is not None)),
                key=lambda d: (preference[role_of[d]], d),
            )
            for d in candidates:
                if n_strength <= 0 or self.strength_in_horizon >= MAX_STRENGTH_PER_PLAN:
                    break
                dd = date.fromisoformat(d)
                if any(abs((dd - other).days) < MIN_STRENGTH_GAP_DAYS for other in strength_dates):
                    continue
                session = strength_session(d, program)
                add(session, "strength")
                budget -= tss_of(session)
                strength_dates.append(dd)
                n_strength -= 1

        # 5) One full rest day (the first rest_or_easy day), then aerobic fill.
        rest_dates = set()
        rest_candidate = next((d for d in free if role_of[d] == "rest_or_easy"), None)
        if rest_candidate and len(week_dates) >= 4:
            rest_dates.add(rest_candidate)
        fill_order = {"open": 0, "long_endurance": 0, "easy": 1, "rest_or_easy": 2, "intensity": 1}
        fill_days = sorted((d for d in free if d not in rest_dates), key=lambda d: (fill_order.get(role_of[d], 1), d))
        default_cap = {"easy": _EASY_DAY_MAX_MIN, "rest_or_easy": _RESTRICTED_MAX_MIN, "intensity": _EASY_DAY_MAX_MIN}
        if target.is_deload and fill_days:
            default_cap["long_endurance"] = 120
        caps = {d: cap_for(d, default_cap.get(role_of[d], 10_000)) for d in fill_days}
        budget = self._fill(fill_days, caps, budget, add, target, role_of)
        self._extend_long(planned, budget)

        # 6) Whatever is still empty becomes a rest day (a strength-only day stays as it is).
        for d in free:
            if not planned[d]:
                add(rest_day(d), "rest")
        return [item for d in week_dates for item in planned[d]]

    def _long_level_minutes(self, sport: str) -> int:
        if sport not in _CYCLING:
            return 120
        level = int(self.inp.workout_levels.get("long_ride_progression", 1))
        levels = WORKOUT_LIBRARY["long_ride_progression"]["levels"]
        return levels[max(1, min(level, len(levels))) - 1]["total_min"]

    def _extend_long(self, planned: dict, budget: float) -> None:
        """Grow the long session (within its cap) when the fill days could not absorb the week's load."""
        for d, items in planned.items():
            for i, (day, role) in enumerate(items):
                if role != "long":
                    continue
                cap = min(self._long_cap.get(d, day.duration_min), day.duration_min + self.budget_left(day.intervals_type))
                minutes = day.duration_min
                while budget > 5 and minutes + 15 <= cap:
                    before = _endurance_tss(day.intervals_type, minutes)
                    minutes += 15
                    budget -= _endurance_tss(day.intervals_type, minutes) - before
                if minutes != day.duration_min:
                    if day.intervals_type in self.inp.sport_budgets:
                        self.budget_used[day.intervals_type] = (
                            self.budget_used.get(day.intervals_type, 0) + minutes - day.duration_min)
                    hours, mins = divmod(minutes, 60)
                    length = f"{hours}h{mins:02d}" if mins else f"{hours}h"
                    items[i] = (endurance_session(d, day.intervals_type, minutes, slot=day.slot,
                                                  title=f"Long endurance – {length}",
                                                  description=day.description), role)

    def _fill(self, fill_days: list[str], caps: dict, budget: float, add, target: WeekTarget,
              role_of: dict) -> float:
        """Spread the remaining weekly TSS over the fill days. Returns the TSS left over."""
        if budget <= 0 or not fill_days:
            return budget
        # Pick how many days to use: each needs at least a minimum-length session.
        min_tss = _endurance_tss("VirtualRide", 45)
        n = min(len(fill_days), max(int(budget // min_tss), 0))
        chosen_days = fill_days[:n]
        if not chosen_days:
            return budget
        per_day = budget / len(chosen_days)
        plan: dict[str, tuple[str, int, str]] = {}
        secondary_left = SECONDARY_SESSIONS_PER_WEEK if not target.kind == "race" else 0
        # The secondary sport goes on an easy day, so the open days keep the main volume.
        secondary_order = sorted(chosen_days, key=lambda d: (role_of.get(d) != "easy", d))
        secondary_day = None
        for d in secondary_order:
            if secondary_left > 0 and self._secondary_choice(d, per_day, caps[d], target):
                secondary_day = d
                break
        def reserve(sport: str, minutes: int):
            # Book sport-budget minutes and roller-ski sessions as soon as a session is chosen,
            # so later fill days see what is actually left.
            if sport in self.inp.sport_budgets:
                self.budget_used[sport] = self.budget_used.get(sport, 0) + minutes

        for d in chosen_days:
            choice = None
            if d == secondary_day:
                choice = self._secondary_choice(d, per_day, caps[d], target)
                if choice:
                    secondary_left -= 1
            if choice is None:
                choice = self.endurance_choice(d, 0, caps[d], week_start=target.week_start)
                if choice:
                    sport, _, slot = choice
                    lo = self.min_minutes(sport)
                    hi = min(caps[d], _MAX_FILL_MIN.get(sport, 90), self.budget_left(sport))
                    choice = (sport, minutes_for_tss(sport, per_day, lo, hi), slot) if hi >= lo else None
            if choice:
                plan[d] = choice
                reserve(choice[0], choice[1])
                if choice[0] == "RollerSki":
                    self.rollski_by_week[target.week_start] = self.rollski_by_week.get(target.week_start, 0) + 1
        # Distribute leftover TSS in 15-minute steps to days that still have room.
        def total():
            return sum(_endurance_tss(s, m) for s, m, _ in plan.values())
        for _ in range(40):
            leftover = budget - total()
            if leftover < _endurance_tss("VirtualRide", 30) - _endurance_tss("VirtualRide", 15):
                break
            grown = False
            for d, (sport, minutes, slot) in sorted(plan.items(), key=lambda kv: kv[1][1]):
                hi = min(caps[d], _MAX_FILL_MIN.get(sport, 90), minutes + self.budget_left(sport))
                if minutes + 15 <= hi:
                    plan[d] = (sport, minutes + 15, slot)
                    reserve(sport, 15)
                    grown = True
                    break
            if not grown:
                break
        for d, (sport, minutes, slot) in plan.items():
            add(endurance_session(d, sport, minutes, slot=slot), "fill", reserved=True)
        return budget - total()

    def _secondary_choice(self, d: str, per_day: float, cap: int, target: WeekTarget):
        for s in self.catalog:
            sport = s["intervals_type"]
            if sport in _CYCLING or sport in ("WeightTraining", self.primary):
                continue
            if s.get("injury_risk") == "high" or not self.allowed(sport, d):
                continue
            if sport == "RollerSki" and self.rollski_by_week.get(target.week_start, 0) >= MAX_ROLLSKI_PER_WEEK:
                continue
            lo = self.min_minutes(sport)
            hi = min(cap, _MAX_FILL_MIN.get(sport, 90), self.budget_left(sport))
            if hi >= lo:
                return sport, minutes_for_tss(sport, per_day, lo, hi), "MAIN"
        return None

    def _rtp_days(self, free: list[str]) -> dict[str, PlanDay]:
        rtp = self.inp.rtp_status or {}
        if not rtp.get("is_active"):
            return {}
        protocol = [(30, "Z1", "RTP Day 1: Test"), (45, "Z2", "RTP Day 2: Confirm"), (60, "Z2", "RTP Day 3: Open up")]
        days = {}
        for d, (minutes, zone, title) in zip(sorted(d for d in free if d >= self.inp.today.isoformat()), protocol):
            choice = self.endurance_choice(d, minutes, minutes)
            if not choice:
                continue
            sport, _, slot = choice
            day = endurance_session(d, sport, minutes, slot=slot, zone=zone, title=title,
                                    description=f"Return-to-Play after {rtp.get('days_off', '?')} rest days. Easy, check how the body responds.")
            days[d] = day
        return days

    # ── whole plan ────────────────────────────────────────────────────────────
    def build(self) -> PlannerResult:
        inp = self.inp
        if not inp.horizon_dates:
            raise ValueError("horizon_dates is empty")
        last = date.fromisoformat(max(inp.horizon_dates))
        week_end = monday_of(last) + timedelta(days=6)
        span = []
        d = inp.today
        while d <= week_end:
            span.append(d.isoformat())
            d += timedelta(days=1)

        intensity_done = {inp.today.isocalendar()[1]: inp.intensity_done_this_week} if inp.intensity_done_this_week else {}
        slots = build_week_skeleton(
            dates=span,
            mesocycle=inp.mesocycle,
            readiness={},
            race_week=inp.race_week,
            locked_dates=set(inp.locked_dates),
            rtp_status=None,
            week_targets=inp.week_targets,
            restricted_dates=set(inp.restricted_dates),
            intensity_done_by_week=intensity_done,
            yesterday_was_hard=inp.yesterday_was_hard,
        )
        planned: list[tuple[PlanDay, str]] = []
        for target in inp.week_targets:
            week_slots = [s for s in slots if target.contains(s.date)]
            if week_slots:
                planned.extend(self.plan_week(target, week_slots))

        in_horizon = [(day, role) for day, role in planned if day.date in self.horizon]
        days = sorted((day for day, _ in in_horizon), key=_sort_key)
        days = self._add_rehab(days)
        roles = {session_key(day): role for day, role in in_horizon}
        week_start_of = {d: monday_of(date.fromisoformat(d)).isoformat() for d in inp.horizon_dates}
        for day in days:
            role = roles.get(session_key(day), "rest")
            self.options[session_key(day)] = [day] + self.alternatives(day, role, week_start_of[day.date])
        self.roles = roles

        planned_tss = sum(tss_of(day) for day in days)
        manual_tss = sum(v for d, v in inp.base_tss_by_date.items() if d in self.horizon)
        hard_days = len({day.date for day in days if _is_hard_day(day)})
        weeks_in_horizon = [t for t in inp.week_targets if any(t.contains(d) for d in inp.horizon_dates)]
        summary = (
            f"Deterministic plan ({inp.phase} phase): {len(days)} entries over {len(inp.horizon_dates)} days, "
            f"~{round(planned_tss)} TSS planned"
            + (f" + {round(manual_tss)} TSS in your own sessions" if manual_tss else "")
            + f". {hard_days} key session(s)."
        )
        if inp.restricted_dates and inp.restriction_reason:
            summary += f" Easy today/tomorrow: {inp.restriction_reason}."
        stress_audit = "Weekly targets:\n" + format_week_targets(weeks_in_horizon)
        plan = AIPlan(stress_audit=stress_audit, summary=summary, days=days)
        return PlannerResult(
            plan=plan,
            options=self.options,
            roles=roles,
            horizon_tss_target=round(planned_tss + manual_tss, 1),
            max_hard_days=hard_days,
            week_targets=inp.week_targets,
            notes=self.notes,
        )

    def _add_rehab(self, days: list[PlanDay]) -> list[PlanDay]:
        """Add one rehab session for an injury, keeping the strength limits intact."""
        injury = self.inp.injury
        if not injury or "WeightTraining" not in self.sports:
            return days
        strength_idx = [i for i, d in enumerate(days) if d.intervals_type == "WeightTraining"]
        strength_dates = [date.fromisoformat(days[i].date) for i in strength_idx]

        def far_from_strength(day_str: str) -> bool:
            dd = date.fromisoformat(day_str)
            return all(abs((dd - other).days) >= MIN_STRENGTH_GAP_DAYS for other in strength_dates)

        target = None
        if len(strength_idx) < MAX_STRENGTH_PER_PLAN:
            target = next((i for i, d in enumerate(days) if d.intervals_type == "Rest" and far_from_strength(d.date)), None)
        if target is None and strength_idx:
            target = strength_idx[0]  # swap a planned strength session for rehab
        if target is None:
            return days
        rehab = rehab_session(days[target].date, injury.get("profile", {}), self.inp.injury_note, slot=days[target].slot)
        if rehab:
            days[target] = rehab
        return days


def build_deterministic_plan(inputs: PlannerInputs) -> PlannerResult:
    return _Planner(inputs).build()

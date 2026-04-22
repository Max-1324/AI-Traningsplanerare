"""PromptContext — single typed container for all build_prompt() inputs.

Replaces the 38-parameter signature of build_prompt() with a single object.
All fields map 1-to-1 to the old positional/keyword parameters, so the
internal body of build_prompt() only gains a short destructuring block at the
top — no logic changes needed there.

Adding a new analytical signal now requires:
  1. Add a field here (with a sensible default).
  2. Populate it in main.py.
  3. Use it inside build_prompt().
Previously this required touching both the call site and the signature
independently, with no structural enforcement that both were updated.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from training_plan.engine.skeleton import DaySlot


@dataclass
class PromptContext:
    # ── Required inputs (no defaults) ────────────────────────────────────────
    activities: list
    wellness: list
    fitness: list
    races: list
    weather: list
    morning: dict
    horizon: int
    manual_workouts: list
    athlete: dict | None
    hrv: dict
    budgets: dict
    tss_budget: float       # was tsb_bgt in the old signature
    vetos: list
    phase: dict

    # ── Optional inputs (match old keyword defaults exactly) ─────────────────
    existing_plan_summary: str = "  No existing plan."
    mesocycle: dict | None = None
    trajectory: dict | None = None
    compliance: dict | None = None
    workout_lib_text: str = ""
    progression_directive: str = ""
    ftp_check: dict | None = None
    yesterday_analysis: str = ""
    constraints_text: str = ""
    acwr_trend: dict | None = None
    race_week: dict | None = None
    taper_score: dict | None = None
    rtp_status: dict | None = None
    data_quality: dict | None = None
    per_sport_acwr: dict | None = None
    motivation: dict | None = None
    prehab: dict | None = None
    pre_race_info: str | None = None
    autoregulation_signals: list | None = None
    mesocycle_for_strength: dict | None = None
    readiness: dict | None = None
    np_if_analysis: dict | None = None
    learned_patterns: str = ""
    exclude_dates: set | None = None
    development_needs: dict | None = None
    block_objective: dict | None = None
    race_demands: dict | None = None
    session_quality: dict | None = None
    coach_confidence: dict | None = None
    polarization: dict | None = None
    historical_validation: dict | None = None
    outcome_tracking: dict | None = None
    planner_insights: dict | None = None
    failure_memory: str = ""

    # ── Slot skeleton (improvement #1) ───────────────────────────────────────
    # Computed by build_week_skeleton() in main.py and injected here.
    # None means the skeleton section is omitted from the prompt (safe default).
    week_skeleton: list | None = None  # list[DaySlot]

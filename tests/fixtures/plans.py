"""
Reusable plan fixtures for regression tests.

Each function returns a list[PlanDay] representing a specific training scenario.
Dates are generated relative to a base date so tests don't depend on today's date.
"""
from datetime import date, timedelta

from training_plan.core.models import PlanDay, WorkoutStep, StrengthStep


def _date(base: date, offset: int) -> str:
    return (base + timedelta(days=offset)).isoformat()


def easy_day(base: date, offset: int, sport: str = "VirtualRide", duration: int = 60) -> PlanDay:
    return PlanDay(
        date=_date(base, offset),
        title=f"Easy {sport}",
        intervals_type=sport,
        duration_min=duration,
        workout_steps=[
            WorkoutStep(duration_min=10, zone="Z1", description="Warmup"),
            WorkoutStep(duration_min=duration - 20, zone="Z2", description="Aerobic"),
            WorkoutStep(duration_min=10, zone="Z1", description="Cooldown"),
        ],
    )


def hard_day(base: date, offset: int, sport: str = "VirtualRide", duration: int = 75) -> PlanDay:
    return PlanDay(
        date=_date(base, offset),
        title=f"Threshold {sport}",
        intervals_type=sport,
        duration_min=duration,
        workout_steps=[
            WorkoutStep(duration_min=15, zone="Z2", description="Warmup"),
            WorkoutStep(duration_min=45, zone="Z4", description="Threshold intervals"),
            WorkoutStep(duration_min=15, zone="Z1", description="Cooldown"),
        ],
    )


def rest_day(base: date, offset: int) -> PlanDay:
    return PlanDay(
        date=_date(base, offset),
        title="Rest",
        intervals_type="Rest",
        duration_min=0,
    )


def strength_day(base: date, offset: int, duration: int = 45) -> PlanDay:
    return PlanDay(
        date=_date(base, offset),
        title="Strength",
        intervals_type="WeightTraining",
        duration_min=duration,
        strength_steps=[
            StrengthStep(exercise="Split squat", sets=3, reps="8-10/leg", rest_sec=60),
            StrengthStep(exercise="Romanian deadlift", sets=3, reps="8-10", rest_sec=60),
        ],
    )


def run_day(base: date, offset: int, duration: int = 50) -> PlanDay:
    return PlanDay(
        date=_date(base, offset),
        title="Easy run",
        intervals_type="Run",
        duration_min=duration,
        workout_steps=[
            WorkoutStep(duration_min=10, zone="Z1", description="Warmup"),
            WorkoutStep(duration_min=duration - 20, zone="Z2", description="Aerobic"),
            WorkoutStep(duration_min=10, zone="Z1", description="Cooldown"),
        ],
    )


# ── Named scenario builders ────────────────────────────────────────────────────

BASE = date(2025, 6, 2)  # Fixed Monday – tests are date-independent


def mixed_week() -> list[PlanDay]:
    """7-day plan with a realistic mix of session types."""
    return [
        hard_day(BASE, 0),
        easy_day(BASE, 1),
        strength_day(BASE, 2),
        hard_day(BASE, 3),
        easy_day(BASE, 4),
        rest_day(BASE, 5),
        easy_day(BASE, 6, duration=120),
    ]


def three_consecutive_hard() -> list[PlanDay]:
    """Three Z4-heavy sessions on consecutive days – triggers hard-easy rule."""
    return [hard_day(BASE, i) for i in range(3)]


def four_consecutive_rest() -> list[PlanDay]:
    """Four rest days in a row – triggers max-consecutive-rest rule."""
    return [rest_day(BASE, i) for i in range(4)]


def run_heavy_week() -> list[PlanDay]:
    """Week with several Run sessions – used for injury-rule testing."""
    return [
        run_day(BASE, 0),
        easy_day(BASE, 1, sport="VirtualRide"),
        run_day(BASE, 2),
        rest_day(BASE, 3),
        run_day(BASE, 4),
        easy_day(BASE, 5, sport="VirtualRide"),
        rest_day(BASE, 6),
    ]


def heavy_tss_week(ftp: int = 260) -> list[PlanDay]:
    """Week dominated by Z4/Z5 sessions – used for TSS ceiling testing."""
    return [
        PlanDay(
            date=_date(BASE, i),
            title=f"Z4 session {i+1}",
            intervals_type="VirtualRide",
            duration_min=90,
            workout_steps=[
                WorkoutStep(duration_min=15, zone="Z2", description="Warmup"),
                WorkoutStep(duration_min=60, zone="Z4", description="Threshold block"),
                WorkoutStep(duration_min=15, zone="Z1", description="Cooldown"),
            ],
        )
        for i in range(5)
    ] + [rest_day(BASE, 5), rest_day(BASE, 6)]

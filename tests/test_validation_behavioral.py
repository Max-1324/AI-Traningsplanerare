"""
Behavioral regression tests for the validation and repair layer.

These tests assert coaching invariants that must hold after repair_postprocessed_plan
runs, regardless of what the AI produced.
"""
import unittest

from training_plan.core.models import AIPlan, PlanDay, StrengthStep, WorkoutStep
from training_plan.engine.validation import repair_postprocessed_plan, validate_postprocessed_plan
from tests.fixtures.plans import BASE, easy_day, hard_day, rest_day, strength_day


def _make_plan(*days: PlanDay) -> AIPlan:
    return AIPlan(stress_audit="ok", summary="test", days=list(days))


def _repair(plan: AIPlan):
    repaired, actions = repair_postprocessed_plan(plan)
    return repaired, actions


class TestWorkoutStepStructure(unittest.TestCase):
    """After repair, every active non-strength session must have workout_steps."""

    def test_steps_added_when_missing(self):
        day = PlanDay(
            date=BASE.isoformat(),
            title="Easy ride",
            intervals_type="VirtualRide",
            duration_min=60,
            workout_steps=[],
        )
        repaired, actions = _repair(_make_plan(day))
        active = [d for d in repaired.days if d.intervals_type != "Rest"]
        for d in active:
            self.assertTrue(
                len(d.workout_steps) > 0,
                f"Session '{d.title}' must have workout_steps after repair",
            )

    def test_step_durations_sum_to_session_duration(self):
        day = PlanDay(
            date=BASE.isoformat(),
            title="Threshold session",
            intervals_type="VirtualRide",
            duration_min=75,
            workout_steps=[
                WorkoutStep(duration_min=15, zone="Z2", description="Warmup"),
                WorkoutStep(duration_min=45, zone="Z4", description="Main"),
                WorkoutStep(duration_min=15, zone="Z1", description="Cooldown"),
            ],
        )
        repaired, _ = _repair(_make_plan(day))
        for d in repaired.days:
            if d.intervals_type in ("Rest", "WeightTraining") or d.duration_min == 0:
                continue
            total_step_min = sum(s.duration_min for s in d.workout_steps)
            self.assertEqual(
                total_step_min,
                d.duration_min,
                f"Step durations ({total_step_min}) must equal session duration ({d.duration_min}) for '{d.title}'",
            )

    def test_warmup_inserted_when_starts_at_z4(self):
        day = PlanDay(
            date=BASE.isoformat(),
            title="Raw threshold",
            intervals_type="VirtualRide",
            duration_min=60,
            workout_steps=[
                WorkoutStep(duration_min=50, zone="Z4", description="Hard block"),
                WorkoutStep(duration_min=10, zone="Z1", description="Cooldown"),
            ],
        )
        repaired, actions = _repair(_make_plan(day))
        repaired_day = repaired.days[0]
        first_zone = repaired_day.workout_steps[0].zone
        self.assertIn(
            first_zone,
            {"Z1", "Z2"},
            f"First step zone must be Z1 or Z2 after repair, got {first_zone}",
        )

    def test_cooldown_inserted_when_ends_at_z4(self):
        day = PlanDay(
            date=BASE.isoformat(),
            title="Missing cooldown",
            intervals_type="VirtualRide",
            duration_min=60,
            workout_steps=[
                WorkoutStep(duration_min=10, zone="Z1", description="Warmup"),
                WorkoutStep(duration_min=50, zone="Z4", description="Hard block"),
            ],
        )
        repaired, _ = _repair(_make_plan(day))
        repaired_day = repaired.days[0]
        last_zone = repaired_day.workout_steps[-1].zone
        self.assertEqual(last_zone, "Z1", "Last step must be Z1 after cooldown is inserted")


class TestRestDayStructure(unittest.TestCase):
    """Rest days must have zero duration and no steps after repair."""

    def test_rest_day_has_zero_duration(self):
        day = PlanDay(
            date=BASE.isoformat(),
            title="Rest",
            intervals_type="Rest",
            duration_min=30,  # Incorrect – should be 0
        )
        repaired, _ = _repair(_make_plan(day))
        self.assertEqual(repaired.days[0].duration_min, 0)

    def test_rest_day_has_no_steps(self):
        day = PlanDay(
            date=BASE.isoformat(),
            title="Rest",
            intervals_type="Rest",
            duration_min=0,
            workout_steps=[WorkoutStep(duration_min=30, zone="Z1", description="Stray step")],
        )
        repaired, _ = _repair(_make_plan(day))
        self.assertEqual(repaired.days[0].workout_steps, [])


class TestStrengthSessionStructure(unittest.TestCase):
    """WeightTraining sessions must have strength_steps and no workout_steps after repair."""

    def test_strength_steps_added_when_missing(self):
        day = PlanDay(
            date=BASE.isoformat(),
            title="Strength",
            intervals_type="WeightTraining",
            duration_min=45,
            strength_steps=[],
        )
        repaired, actions = _repair(_make_plan(day))
        repaired_day = repaired.days[0]
        self.assertTrue(
            len(repaired_day.strength_steps) > 0,
            "Strength session without exercises must have default steps injected",
        )

    def test_endurance_steps_cleared_from_strength_session(self):
        day = PlanDay(
            date=BASE.isoformat(),
            title="Strength",
            intervals_type="WeightTraining",
            duration_min=45,
            workout_steps=[WorkoutStep(duration_min=45, zone="Z2", description="Stray endurance step")],
            strength_steps=[StrengthStep(exercise="Split squat", sets=3, reps="8-10/leg", rest_sec=60)],
        )
        repaired, _ = _repair(_make_plan(day))
        self.assertEqual(
            repaired.days[0].workout_steps,
            [],
            "Endurance workout_steps must be cleared from a WeightTraining session",
        )


class TestZoneNormalization(unittest.TestCase):
    """Non-canonical zone strings must be normalized to Zn format after repair."""

    def test_zone_1_variants_normalized(self):
        variants = ["ZONE 1", "ZON 1", "Zone 1"]
        for variant in variants:
            day = PlanDay(
                date=BASE.isoformat(),
                title="Easy ride",
                intervals_type="VirtualRide",
                duration_min=60,
                workout_steps=[
                    WorkoutStep(duration_min=60, zone=variant, description="Easy"),
                ],
            )
            repaired, _ = _repair(_make_plan(day))
            canonical = repaired.days[0].workout_steps[0].zone
            self.assertEqual(canonical, "Z1", f"Zone '{variant}' must normalize to 'Z1', got '{canonical}'")


class TestValidationInvariants(unittest.TestCase):
    """validate_postprocessed_plan must detect known coaching problems."""

    def test_valid_plan_passes(self):
        plan = _make_plan(easy_day(BASE, 0), rest_day(BASE, 1), easy_day(BASE, 2))
        result = validate_postprocessed_plan(plan)
        self.assertTrue(
            result.passed,
            f"A clean plan should pass validation; hard_failures: {result.hard_failures}",
        )

    def test_plan_with_no_days_has_failures(self):
        plan = AIPlan(stress_audit="ok", summary="empty", days=[])
        result = validate_postprocessed_plan(plan)
        self.assertFalse(result.passed, "An empty plan must fail validation")
        self.assertTrue(
            len(result.hard_failures) > 0,
            "An empty plan must have at least one hard failure",
        )

    def test_repair_actions_reported_for_missing_steps(self):
        day = PlanDay(
            date=BASE.isoformat(),
            title="No steps",
            intervals_type="VirtualRide",
            duration_min=60,
            workout_steps=[],
        )
        _, actions = _repair(_make_plan(day))
        self.assertTrue(
            any("AUTO-REPAIR" in a for a in actions),
            "Repair must report at least one AUTO-REPAIR action for a session missing steps",
        )


if __name__ == "__main__":
    unittest.main()

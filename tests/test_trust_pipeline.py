import unittest
from datetime import date
from unittest.mock import patch

from pydantic import ValidationError

from training_plan.core.models import AIPlan, PlanDay, WorkoutStep
from training_plan.engine.ai import parse_plan
from training_plan.engine.pipeline import _apply_tss_gap_revision
from training_plan.engine.validation import repair_postprocessed_plan, validate_postprocessed_plan


class TrustPipelineTests(unittest.TestCase):
    def _valid_plan(self) -> AIPlan:
        today = date.today().isoformat()
        return AIPlan(
            stress_audit="ok",
            summary="ok",
            days=[
                PlanDay(
                    date=today,
                    title="Easy ride",
                    intervals_type="Ride",
                    duration_min=60,
                    description="Aerobic endurance",
                    workout_steps=[
                        WorkoutStep(duration_min=10, zone="Z1", description="Warmup"),
                        WorkoutStep(duration_min=40, zone="Z2", description="Main set"),
                        WorkoutStep(duration_min=10, zone="Z1", description="Cooldown"),
                    ],
                )
            ],
        )

    def _hard_plan(self) -> AIPlan:
        today = date.today()
        days = []
        for offset in range(3):
            days.append(
                PlanDay(
                    date=(today.fromordinal(today.toordinal() + offset)).isoformat(),
                    title=f"Threshold day {offset + 1}",
                    intervals_type="Ride",
                    duration_min=60,
                    description="Hard ride",
                    workout_steps=[
                        WorkoutStep(duration_min=15, zone="Z2", description="Warmup"),
                        WorkoutStep(duration_min=30, zone="Z4", description="Main set"),
                        WorkoutStep(duration_min=15, zone="Z1", description="Cooldown"),
                    ],
                )
            )
        return AIPlan(stress_audit="ok", summary="hard", days=days)

    def test_parse_plan_raises_on_invalid_response(self):
        with self.assertRaises(ValueError):
            parse_plan("this is not valid json")

    def test_invalid_intervals_type_is_rejected(self):
        with self.assertRaises(ValidationError):
            PlanDay(
                date=date.today().isoformat(),
                title="Mystery sport",
                intervals_type="Teleport",
                duration_min=30,
            )

    def test_validator_accepts_clean_plan(self):
        result = validate_postprocessed_plan(
            self._valid_plan(),
            review_context={"today": date.today().isoformat(), "time_available_min": 90},
            postprocess_changes=[],
        )
        self.assertTrue(result.passed)
        self.assertEqual(result.hard_failures, [])

    def test_validator_rejects_hard_veto_rewrites(self):
        result = validate_postprocessed_plan(
            self._valid_plan(),
            review_context={"today": date.today().isoformat(), "time_available_min": 90},
            postprocess_changes=["TIME BUDGET: 2026-01-01 shortened session to fit within 45min today"],
        )
        self.assertFalse(result.passed)
        self.assertTrue(any("hard rule violation" in item for item in result.hard_failures))

    def test_repair_inserts_missing_must_hit_session(self):
        today = date.today()
        plan = AIPlan(
            stress_audit="ok",
            summary="missing threshold",
            days=[
                PlanDay(
                    date=today.isoformat(),
                    title="Easy ride",
                    intervals_type="Ride",
                    duration_min=60,
                    description="Aerobic endurance",
                    workout_steps=[
                        WorkoutStep(duration_min=10, zone="Z1", description="Warmup"),
                        WorkoutStep(duration_min=40, zone="Z2", description="Main set"),
                        WorkoutStep(duration_min=10, zone="Z1", description="Cooldown"),
                    ],
                ),
                PlanDay(
                    date=today.fromordinal(today.toordinal() + 1).isoformat(),
                    title="Open slot",
                    intervals_type="Rest",
                    duration_min=0,
                ),
            ],
        )
        review_context = {
            "today": today.isoformat(),
            "block_objective": {"must_hit_sessions": ["1 threshold session"]},
            "training_frequency_target": {"min_training_days": 1, "max_training_days": 2, "max_double_days": 0},
            "time_available_min": 90,
        }
        initial = validate_postprocessed_plan(plan, review_context=review_context, postprocess_changes=[])
        self.assertFalse(initial.passed)
        self.assertTrue(any("threshold" in item.lower() for item in initial.hard_failures))

        repaired_plan, repair_actions = repair_postprocessed_plan(plan, review_context=review_context, validation=initial)
        self.assertTrue(any("threshold" in item.lower() for item in repair_actions))
        repaired = validate_postprocessed_plan(repaired_plan, review_context=review_context, postprocess_changes=repair_actions)
        self.assertTrue(repaired.passed)

    def test_repair_reduces_excess_hard_days(self):
        plan = self._hard_plan()
        review_context = {
            "today": date.today().isoformat(),
            "readiness": {"score": 52},
            "minimum_effective_dose": {"mode": "ACTIVE", "scope": "GLOBAL"},
            "training_frequency_target": {"min_training_days": 1, "max_training_days": 3, "max_double_days": 0},
        }
        initial = validate_postprocessed_plan(plan, review_context=review_context, postprocess_changes=[])
        self.assertFalse(initial.passed)
        self.assertTrue(any("hard day" in item.lower() for item in initial.hard_failures))

        repaired_plan, repair_actions = repair_postprocessed_plan(plan, review_context=review_context, validation=initial)
        self.assertTrue(any("downgraded" in item.lower() for item in repair_actions))
        repaired = validate_postprocessed_plan(repaired_plan, review_context=review_context, postprocess_changes=repair_actions)
        self.assertTrue(repaired.passed)

    def test_conditional_vo2_requirement_is_not_treated_as_mandatory(self):
        today = date.today()
        plan = AIPlan(
            stress_audit="ok",
            summary="ftp only",
            days=[
                PlanDay(
                    date=today.isoformat(),
                    title="FTP Ramp Test",
                    intervals_type="VirtualRide",
                    duration_min=60,
                    description="Benchmark day",
                    workout_steps=[
                        WorkoutStep(duration_min=15, zone="Z2", description="Warmup"),
                        WorkoutStep(duration_min=30, zone="Z4", description="Ramp/benchmark"),
                        WorkoutStep(duration_min=15, zone="Z1", description="Cooldown"),
                    ],
                ),
                PlanDay(
                    date=today.fromordinal(today.toordinal() + 1).isoformat(),
                    title="Rest Day",
                    intervals_type="Rest",
                    duration_min=0,
                ),
            ],
        )
        review_context = {
            "today": today.isoformat(),
            "readiness": {"score": 46},
            "minimum_effective_dose": {"mode": "ACTIVE", "scope": "GLOBAL"},
            "race_demands": {"must_have_sessions": ["1 short VO2 stimuli if recovery allows."]},
        }
        result = validate_postprocessed_plan(plan, review_context=review_context, postprocess_changes=[])
        self.assertTrue(
            result.passed,
            f"Conditional recovery-dependent VO2 should not become a hard validation requirement: {result.hard_failures}",
        )

    def test_repair_does_not_insert_hard_session_beside_existing_hard_day(self):
        today = date.today()
        plan = AIPlan(
            stress_audit="ok",
            summary="missing threshold but no safe slot",
            days=[
                PlanDay(
                    date=today.isoformat(),
                    title="FTP Ramp Test",
                    intervals_type="VirtualRide",
                    duration_min=60,
                    description="Benchmark day",
                    workout_steps=[
                        WorkoutStep(duration_min=15, zone="Z2", description="Warmup"),
                        WorkoutStep(duration_min=30, zone="Z4", description="Ramp/benchmark"),
                        WorkoutStep(duration_min=15, zone="Z1", description="Cooldown"),
                    ],
                ),
                PlanDay(
                    date=today.fromordinal(today.toordinal() + 1).isoformat(),
                    title="Open slot",
                    intervals_type="Rest",
                    duration_min=0,
                ),
            ],
        )
        review_context = {
            "today": today.isoformat(),
            "block_objective": {"must_hit_sessions": ["1 threshold session"]},
            "readiness": {"score": 52},
            "minimum_effective_dose": {"mode": "ACTIVE", "scope": "GLOBAL"},
        }
        initial = validate_postprocessed_plan(plan, review_context=review_context, postprocess_changes=[])
        self.assertFalse(initial.passed)
        self.assertTrue(any("threshold" in item.lower() for item in initial.hard_failures))

        repaired_plan, repair_actions = repair_postprocessed_plan(plan, review_context=review_context, validation=initial)
        self.assertFalse(
            any("deterministic threshold" in item.lower() for item in repair_actions),
            f"Repair should not insert a hard session that creates an illegal back-to-back pattern: {repair_actions}",
        )
        self.assertEqual(repaired_plan.days[1].intervals_type, "Rest")

    def test_long_horizon_hard_day_limit_scales_with_plan_length(self):
        today = date.today()
        days = []
        hard_offsets = {3, 10, 17, 24}
        for offset in range(29):
            current = today.fromordinal(today.toordinal() + offset)
            if offset in hard_offsets:
                days.append(
                    PlanDay(
                        date=current.isoformat(),
                        title=f"Threshold day {offset}",
                        intervals_type="VirtualRide",
                        duration_min=60,
                        description="Hard day",
                        workout_steps=[
                            WorkoutStep(duration_min=15, zone="Z2", description="Warmup"),
                            WorkoutStep(duration_min=30, zone="Z4", description="Main set"),
                            WorkoutStep(duration_min=15, zone="Z1", description="Cooldown"),
                        ],
                    )
                )
            else:
                days.append(
                    PlanDay(
                        date=current.isoformat(),
                        title=f"Easy day {offset}",
                        intervals_type="Ride",
                        duration_min=75,
                        description="Aerobic support",
                        workout_steps=[
                            WorkoutStep(duration_min=15, zone="Z1", description="Warmup"),
                            WorkoutStep(duration_min=45, zone="Z2", description="Main set"),
                            WorkoutStep(duration_min=15, zone="Z1", description="Cooldown"),
                        ],
                    )
                )

        result = validate_postprocessed_plan(
            AIPlan(stress_audit="ok", summary="long horizon", days=days),
            review_context={
                "today": today.isoformat(),
                "readiness": {"score": 46},
                "minimum_effective_dose": {"mode": "ACTIVE", "scope": "GLOBAL"},
            },
            postprocess_changes=[],
        )
        self.assertTrue(
            result.passed,
            f"Four protected hard days across a 29-day horizon should not fail the global hard-day cap: {result.hard_failures}",
        )

    @patch("training_plan.engine.pipeline.generate_plan")
    def test_tss_gap_revision_skips_when_plan_is_above_deficit_floor(self, mock_generate):
        plan = self._valid_plan()
        original_changes = ["TSS-AUDIT v1: 198 TSS"]

        revised_plan, revised_changes = _apply_tss_gap_revision(
            plan,
            original_changes,
            gen_provider="gemini",
            generation_prompt="prompt",
            postprocess_candidate=lambda candidate: (candidate, original_changes),
            athlete=None,
            base_tss_by_date={date.today().isoformat(): 198.0},
            tss_budget=220.0,
            review_context={},
            attempt=1,
        )

        mock_generate.assert_not_called()
        self.assertIs(revised_plan, plan)
        self.assertEqual(revised_changes, original_changes)

    @patch("training_plan.engine.pipeline.generate_plan")
    def test_tss_gap_revision_discards_revision_that_hits_weekly_cap(self, mock_generate):
        today = date.today().isoformat()
        original_plan = self._valid_plan()
        mock_generate.return_value = AIPlan(
            stress_audit="ok",
            summary="revised",
            days=[
                PlanDay(
                    date=today,
                    title="Extended ride",
                    intervals_type="Ride",
                    duration_min=90,
                    description="Aerobic endurance",
                    workout_steps=[
                        WorkoutStep(duration_min=10, zone="Z1", description="Warmup"),
                        WorkoutStep(duration_min=70, zone="Z2", description="Main set"),
                        WorkoutStep(duration_min=10, zone="Z1", description="Cooldown"),
                    ],
                )
            ],
        )

        revised_candidate, revised_changes = _apply_tss_gap_revision(
            original_plan,
            ["TSS-AUDIT v1: 150 TSS"],
            gen_provider="gemini",
            generation_prompt="prompt",
            postprocess_candidate=lambda candidate: (
                candidate,
                ["  2026-01-01: -30min -> TAK v1", "TSS-AUDIT v1: 220 TSS"],
            ),
            athlete=None,
            base_tss_by_date={today: 150.0},
            tss_budget=220.0,
            review_context={},
            attempt=1,
        )

        self.assertIs(revised_candidate, original_plan)
        self.assertTrue(any("TSS-DEFICIT VETO" in item for item in revised_changes))
        self.assertFalse(any("TAK v1" in item for item in revised_changes))


if __name__ == "__main__":
    unittest.main()

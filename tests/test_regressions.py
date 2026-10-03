"""Regression tests for bugs found in the 2026-10 code review (see docs/ROADMAP.md)."""

import math
import os
import unittest
from datetime import date, timedelta
from unittest import mock

from training_plan.engine.ai import resolve_update_mode
from training_plan.engine.ai.client import _model_queue
from training_plan.engine.analysis import clean_wellness, validate_data_quality
from training_plan.engine.planning import determine_mesocycle

_HRV_NORMAL = {"state": "NORMAL", "today": 60, "deviation_pct": 0.0}


def _state(week: int, block: int, saved: date) -> dict:
    return {
        "mesocycle_block": block,
        "mesocycle_week": week,
        "mesocycle_last_update": saved.isoformat(),
    }


class TestMesocycleAdvance(unittest.TestCase):
    # 2026-10-04 is a Sunday, 2026-10-05 a Monday.
    SUNDAY = date(2026, 10, 4)
    MONDAY = date(2026, 10, 5)

    def test_daily_runs_advance_week_on_monday(self):
        state = _state(week=1, block=1, saved=self.SUNDAY)
        result = determine_mesocycle([], [], state, today=self.MONDAY)
        self.assertEqual(result["week_in_block"], 2)
        self.assertEqual(result["block_number"], 1)

    def test_second_run_same_day_does_not_advance(self):
        state = _state(week=2, block=1, saved=self.MONDAY)
        result = determine_mesocycle([], [], state, today=self.MONDAY)
        self.assertEqual(result["week_in_block"], 2)

    def test_deload_week_rolls_over_to_new_block(self):
        friday = self.MONDAY - timedelta(days=3)
        state = _state(week=4, block=3, saved=friday)
        result = determine_mesocycle([], [], state, today=self.MONDAY)
        self.assertEqual(result["week_in_block"], 1)
        self.assertEqual(result["block_number"], 4)
        self.assertFalse(result["is_deload"])

    def test_two_week_gap_advances_two_weeks(self):
        state = _state(week=1, block=1, saved=self.SUNDAY - timedelta(days=7))
        result = determine_mesocycle([], [], state, today=self.MONDAY)
        self.assertEqual(result["week_in_block"], 3)

    def test_ten_daily_runs_advance_once_per_monday(self):
        state = _state(week=1, block=1, saved=self.SUNDAY - timedelta(days=1))
        weeks_seen = []
        for offset in range(10):  # Sunday 2026-10-04 .. Tuesday 2026-10-13
            today = self.SUNDAY + timedelta(days=offset)
            weeks_seen.append(determine_mesocycle([], [], state, today=today)["week_in_block"])
        self.assertEqual(weeks_seen, [1, 2, 2, 2, 2, 2, 2, 2, 3, 3])


class TestResolveUpdateMode(unittest.TestCase):
    def _ai_workouts(self, horizon: int, load: int) -> list[dict]:
        return [
            {
                "start_date_local": (date.today() + timedelta(days=i)).isoformat() + "T16:00:00",
                "category": "WORKOUT",
                "planned_load": load,
                "description": "ai-generated",
            }
            for i in range(horizon + 1)
        ]

    def _resolve(self, ai_workouts, tss_budget, horizon=3):
        return resolve_update_mode(
            ai_workouts, [], None, _HRV_NORMAL, [], [], horizon,
            base_tss_by_date={}, tss_budget=tss_budget,
        )

    def test_complete_plan_covering_budget_needs_no_ai(self):
        mode, _ = self._resolve(self._ai_workouts(3, load=100), tss_budget=400)
        self.assertEqual(mode, "none")

    def test_complete_plan_under_budget_forces_full(self):
        mode, reason = self._resolve(self._ai_workouts(3, load=50), tss_budget=400)
        self.assertEqual(mode, "full")
        self.assertIn("75%", reason)

    def test_no_existing_plan_is_full(self):
        mode, _ = self._resolve([], tss_budget=400)
        self.assertEqual(mode, "full")


class TestWellnessCleaning(unittest.TestCase):
    def test_missing_hrv_keeps_sleep_and_resting_hr(self):
        wellness = [{"id": "2026-10-01", "hrv": 0, "sleepSecs": 8 * 3600, "restingHR": 48}]
        cleaned = clean_wellness(wellness, validate_data_quality([], wellness))
        self.assertEqual(len(cleaned), 1)
        self.assertEqual(cleaned[0]["sleepSecs"], 8 * 3600)
        self.assertEqual(cleaned[0]["restingHR"], 48)
        self.assertIsNone(cleaned[0]["hrv"])

    def test_implausible_hrv_is_blanked(self):
        wellness = [{"id": "2026-10-01", "hrv": 250, "sleepSecs": 7 * 3600}]
        cleaned = clean_wellness(wellness, validate_data_quality([], wellness))
        self.assertIsNone(cleaned[0]["hrv"])
        self.assertEqual(cleaned[0]["sleepSecs"], 7 * 3600)

    def test_implausible_sleep_is_blanked_but_hrv_kept(self):
        wellness = [{"id": "2026-10-01", "hrv": 55, "sleepSecs": 17 * 3600}]
        cleaned = clean_wellness(wellness, validate_data_quality([], wellness))
        self.assertIsNone(cleaned[0]["sleepSecs"])
        self.assertEqual(cleaned[0]["hrv"], 55)

    def test_input_rows_are_not_mutated(self):
        wellness = [{"id": "2026-10-01", "hrv": 0, "sleepSecs": 8 * 3600}]
        clean_wellness(wellness, validate_data_quality([], wellness))
        self.assertEqual(wellness[0]["hrv"], 0)


class TestModelQueue(unittest.TestCase):
    def test_list_env_is_split_and_trimmed(self):
        with mock.patch.dict(os.environ, {"X_MODELS": "a, b ,,c"}, clear=False):
            self.assertEqual(_model_queue("X_MODELS", "X_MODEL", "d"), ["a", "b", "c"])

    def test_single_model_env_is_used_when_list_missing(self):
        env = {"X_MODEL": "solo"}
        with mock.patch.dict(os.environ, env, clear=False):
            os.environ.pop("X_MODELS", None)
            self.assertEqual(_model_queue("X_MODELS", "X_MODEL", "d"), ["solo"])

    def test_default_when_nothing_is_set(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("X_MODELS", None)
            os.environ.pop("X_MODEL", None)
            self.assertEqual(_model_queue("X_MODELS", "X_MODEL", "d"), ["d"])


class TestRuleFixes(unittest.TestCase):
    def _day(self, day, zone="Z4", sport="VirtualRide", minutes=60, slot="MAIN"):
        from training_plan.core.models import PlanDay, WorkoutStep
        return PlanDay(date=day, title="Session", intervals_type=sport, duration_min=minutes, slot=slot,
                       workout_steps=[WorkoutStep(duration_min=15, zone="Z2", description="w"),
                                      WorkoutStep(duration_min=minutes - 15, zone=zone, description="m")])

    def test_hrv_veto_uses_dates_not_list_positions(self):
        from training_plan.engine.postprocess import enforce_hrv
        today = date(2026, 10, 5)
        # A double day today pushes tomorrow's session to list index 2.
        days = [self._day("2026-10-05", zone="Z2", slot="AM"), self._day("2026-10-05", zone="Z2"),
                self._day("2026-10-06"), self._day("2026-10-07")]
        result, _ = enforce_hrv(days, {"state": "LOW", "deviation_pct": -30}, today=today)
        self.assertTrue(result[2].vetoed, "tomorrow must be vetoed")
        self.assertFalse(result[3].vetoed, "the day after tomorrow is not affected")

    def test_injury_rule_keeps_steps_in_sync_with_capped_duration(self):
        from training_plan.engine.postprocess import apply_injury_rules
        days = [self._day("2026-10-05", zone="Z2", sport="Run", minutes=120)]
        result, _ = apply_injury_rules(days, "knee", injury_profile={"profile_key": "knee", "severity": "MODERATE"})
        self.assertEqual(result[0].duration_min, 60)
        self.assertEqual(sum(s.duration_min for s in result[0].workout_steps), 60)

    def test_illness_leaves_one_rest_entry_per_date(self):
        from training_plan.engine.postprocess import enforce_illness
        days = [self._day("2026-10-05", slot="AM"), self._day("2026-10-05"), self._day("2026-10-06")]
        result, _ = enforce_illness(days, {"sick": True})
        self.assertEqual([d.date for d in result], ["2026-10-05", "2026-10-06"])

    def test_strength_gap_is_measured_in_days(self):
        from training_plan.core.models import PlanDay
        from training_plan.engine.postprocess import enforce_strength_limit
        strength = lambda d: PlanDay(date=d, title="Strength", intervals_type="WeightTraining", duration_min=30,
                                     strength_steps=[{"exercise": "Squat", "sets": 3, "reps": "10"}])
        # Two days apart but adjacent in the list (locked day in between has no entry).
        result, changes = enforce_strength_limit([strength("2026-10-05"), strength("2026-10-07")], max_strength=2, min_gap=2)
        self.assertEqual(changes, [])

    def test_tss_counts_intervals_per_step(self):
        from training_plan.core.models import PlanDay, WorkoutStep
        from training_plan.engine.postprocess import estimate_tss_coggan
        steps = [WorkoutStep(duration_min=15, zone="Z2", description="Warm-up")]
        for _ in range(3):
            steps += [WorkoutStep(duration_min=10, zone="Z4", description="Rep"),
                      WorkoutStep(duration_min=5, zone="Z1", description="Easy")]
        intervals = PlanDay(date="2026-10-05", title="Intervals", intervals_type="VirtualRide",
                            duration_min=60, workout_steps=steps)
        # TSS grows with IF², so per-step TSS must exceed TSS from the averaged IF.
        linear_if = (15 * 0.70 + 30 * 1.00 + 15 * 0.50) / 60
        per_step = (15 * 0.70 ** 2 + 30 * 1.00 ** 2 + 15 * 0.50 ** 2) / 60 * 100
        self.assertAlmostEqual(estimate_tss_coggan(intervals, {}), round(per_step, 1))
        self.assertGreater(estimate_tss_coggan(intervals, {}), linear_if ** 2 * 100)

def _hrv_series(baseline_days: int, last7: float) -> list[dict]:
    start = date(2026, 8, 1)
    rows = [{"id": (start + timedelta(days=i)).isoformat(), "hrv": 55 if i % 2 else 65} for i in range(baseline_days)]
    rows += [{"id": (start + timedelta(days=baseline_days + i)).isoformat(), "hrv": last7} for i in range(7)]
    return rows


class TestHrvBaseline(unittest.TestCase):
    def test_stable_week_is_normal(self):
        from training_plan.engine.analysis import calculate_hrv
        result = calculate_hrv(_hrv_series(40, 60))
        self.assertEqual(result["method"], "ln_rmssd_swc")
        self.assertEqual(result["state"], "NORMAL")

    def test_small_drop_is_slightly_low_and_large_drop_is_low(self):
        from training_plan.engine.analysis import calculate_hrv
        self.assertEqual(calculate_hrv(_hrv_series(40, 57))["state"], "SLIGHTLY_LOW")
        self.assertEqual(calculate_hrv(_hrv_series(40, 50))["state"], "LOW")

    def test_baseline_excludes_the_last_week(self):
        from training_plan.engine.analysis import calculate_hrv
        result = calculate_hrv(_hrv_series(40, 50))
        self.assertAlmostEqual(result["ln_baseline"], round((math.log(55) + math.log(65)) / 2, 3), places=3)

    def test_short_history_falls_back_to_percentages(self):
        from training_plan.engine.analysis import calculate_hrv
        result = calculate_hrv(_hrv_series(6, 60))
        self.assertEqual(result["method"], "percent")
        for key in ("state", "trend", "stability", "deviation_pct", "avg7d", "avg60d"):
            self.assertIn(key, result)


class TestIntervalsCalls(unittest.TestCase):
    def test_fitness_is_derived_from_wellness(self):
        from training_plan.integrations.services import fitness_from_wellness
        rows = [{"id": "2026-10-01", "icu_ctl": 50.0, "icu_atl": 60.0}, {"id": "2026-10-02"}]
        self.assertEqual(fitness_from_wellness(rows), [{"date": "2026-10-01", "atl": 60.0, "ctl": 50.0, "tsb": -10.0}])

    def test_ai_events_are_deleted_in_bulk_chunks(self):
        import training_plan.integrations.intervals_events as events
        workouts = [{"id": i, "description": "ai-generated", "start_date_local": "2099-01-01T16:00:00"}
                    for i in range(120)]
        with mock.patch.object(events.requests, "put") as put:
            deleted = events.delete_ai_workouts(workouts)
        self.assertEqual(deleted, 120)
        self.assertEqual([len(call.kwargs["json"]) for call in put.call_args_list], [50, 50, 20])


if __name__ == "__main__":
    unittest.main()

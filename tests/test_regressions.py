"""Regression tests for bugs found in the 2026-10 code review (see docs/ROADMAP.md)."""

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


if __name__ == "__main__":
    unittest.main()

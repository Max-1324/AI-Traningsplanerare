"""Workout progression: levels move on logged responses only, once per session."""
import unittest
from unittest import mock

import training_plan.engine.planning.workouts as workouts
from training_plan.core.config import AI_TAG
from training_plan.engine.planning.state import save_state, set_read_only
from training_plan.engine.planning.workouts import (
    autoregulate_from_yesterday,
    check_and_advance_workout_progression,
    reduce_levels_after_break,
    workout_key_for,
)

PLANNED = {"name": "Threshold intervals (Z4) – 4×4min Z4 / 3min rest", "type": "VirtualRide",
           "start_date_local": "2026-10-04T17:00:00", "moving_time": 3000, "description": AI_TAG}


def _actual(rpe=None, feel=None, sport="VirtualRide"):
    return {"type": sport, "perceived_exertion": rpe, "feel": feel}


class TestProgression(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(workouts, "save_state", lambda state: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _level_after(self, actual, state=None):
        state = state if state is not None else {"workout_levels": {"threshold_intervals": 2}}
        check_and_advance_workout_progression(PLANNED, [actual], state)
        return state["workout_levels"]["threshold_intervals"]

    def test_no_logged_response_keeps_the_level(self):
        self.assertEqual(self._level_after(_actual()), 2)

    def test_controlled_session_moves_up_with_rpe_or_feel_alone(self):
        self.assertEqual(self._level_after(_actual(rpe=6)), 3)
        self.assertEqual(self._level_after(_actual(feel=2)), 3)

    def test_too_hard_session_moves_down(self):
        self.assertEqual(self._level_after(_actual(rpe=9)), 1)

    def test_same_session_counts_once(self):
        state = {"workout_levels": {"threshold_intervals": 2}}
        for _ in range(3):
            check_and_advance_workout_progression(PLANNED, [_actual(rpe=6)], state)
        self.assertEqual(state["workout_levels"]["threshold_intervals"], 3)

    def test_activity_in_another_sport_is_not_judged(self):
        self.assertEqual(self._level_after(_actual(rpe=6, sport="WeightTraining")), 2)

    def test_exceptional_session_gives_two_levels_not_three(self):
        state = {"workout_levels": {"threshold_intervals": 1}}
        check_and_advance_workout_progression(PLANNED, [_actual(rpe=5, feel=2)], state)
        raw = {"rpe": 5, "feel": 2, "workout_key": "threshold_intervals", "session_id": "x"}
        autoregulate_from_yesterday(raw, state)
        autoregulate_from_yesterday(raw, state)   # a second run the same day
        self.assertEqual(state["workout_levels"]["threshold_intervals"], 3)

    def test_session_is_identified_by_its_library_name(self):
        self.assertEqual(workout_key_for("VO2max intervals (Z5) – 4×5min Z5 / 4min rest"), "vo2max_intervals")
        self.assertEqual(workout_key_for("Short VO2max intervals (Z5) – 2×6×1min Z5 / 1min easy"), "vo2max_short")

    def test_levels_drop_once_after_a_break(self):
        state = {"workout_levels": {"threshold_intervals": 4, "vo2max_intervals": 1}}
        reduce_levels_after_break(state, "2026-10-01")
        reduce_levels_after_break(state, "2026-10-01")
        self.assertEqual(state["workout_levels"], {"threshold_intervals": 3, "vo2max_intervals": 1})


class TestReadOnlyState(unittest.TestCase):
    def test_dry_run_does_not_write_state(self):
        with mock.patch("training_plan.engine.planning.state.STATE_FILE") as state_file:
            set_read_only(True)
            try:
                save_state({"x": 1})
            finally:
                set_read_only(False)
            state_file.with_suffix.assert_not_called()


class TestFtpRampTest(unittest.TestCase):
    def test_ramp_test_is_exported_as_a_ramp(self):
        from training_plan.engine.planner import ftp_test_session
        from training_plan.integrations.intervals_events import build_workout_step_text
        text = build_workout_step_text(ftp_test_session("2026-10-06", "VirtualRide").workout_steps, "VirtualRide")
        self.assertIn("ramp 50-140%", text)
        self.assertNotIn("15m 112%", text)


if __name__ == "__main__":
    unittest.main()

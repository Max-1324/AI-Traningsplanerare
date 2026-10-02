import unittest
from datetime import date

from training_plan.engine.periodization import build_week_targets, done_tss_this_week
from training_plan.engine.utils import is_race_event, race_priority

FRIDAY = date(2026, 10, 2)


class TestWeekTargets(unittest.TestCase):
    def test_each_week_follows_its_own_mesocycle_position(self):
        targets = build_week_targets(50, {"week_in_block": 3, "block_number": 2}, FRIDAY, target_ctl=100)
        self.assertEqual([t.kind for t in targets], ["build", "deload", "build", "build"])
        self.assertEqual([t.week_in_block for t in targets], [3, 4, 1, 2])
        self.assertEqual(targets[2].block_number, 3)

    def test_deload_week_is_lighter_and_has_no_key_sessions(self):
        targets = build_week_targets(50, {"week_in_block": 3, "block_number": 1}, FRIDAY, target_ctl=100)
        build, deload = targets[0], targets[1]
        self.assertLess(deload.tss_target, build.tss_target * 0.7)
        self.assertEqual(deload.max_key_sessions, 0)

    def test_build_week_ramps_above_maintenance(self):
        target = build_week_targets(50, {"week_in_block": 2}, FRIDAY, target_ctl=100)[0]
        self.assertGreater(target.tss_target, 50 * 7)

    def test_target_ctl_caps_the_ramp(self):
        target = build_week_targets(85, {"week_in_block": 2}, FRIDAY, target_ctl=85)[0]
        self.assertEqual(target.ramp, 0.0)
        self.assertEqual(target.tss_target, 85 * 7)

    def test_high_fatigue_holds_current_week_at_maintenance(self):
        target = build_week_targets(60, {"week_in_block": 2}, FRIDAY, tsb=-25, target_ctl=100)[0]
        self.assertEqual(target.ramp, 0.0)

    def test_a_race_creates_taper_and_race_week(self):
        races = [{"name": "Vätternrundan", "category": "RACE_A", "start_date_local": "2026-10-25T06:00:00"}]
        targets = build_week_targets(60, {"week_in_block": 1}, FRIDAY, races=races, target_ctl=100)
        self.assertEqual(targets[2].kind, "taper")
        self.assertEqual(targets[3].kind, "race")
        self.assertEqual(targets[3].max_key_sessions, 0)
        self.assertLess(targets[3].tss_target, targets[0].tss_target / 2)

    def test_remaining_tss_subtracts_done_load(self):
        target = build_week_targets(50, {"week_in_block": 1}, FRIDAY, done_tss=200, target_ctl=100)[0]
        self.assertEqual(target.remaining_tss, target.tss_target - 200)

    def test_done_tss_counts_monday_through_today(self):
        activities = [
            {"start_date_local": "2026-09-27T10:00:00", "icu_training_load": 99},  # Sunday before
            {"start_date_local": "2026-09-28T10:00:00", "icu_training_load": 50},
            {"start_date_local": "2026-10-02T07:00:00", "icu_training_load": 40},
        ]
        self.assertEqual(done_tss_this_week(activities, FRIDAY), 90)


class TestRacePriority(unittest.TestCase):
    def test_category_wins_over_name(self):
        self.assertEqual(race_priority({"category": "RACE_C", "name": "B: Something"}), "C")

    def test_name_prefix_is_fallback(self):
        self.assertEqual(race_priority({"category": "RACE", "name": "B: Lidingöloppet"}), "B")
        self.assertEqual(race_priority({"category": "RACE", "name": "Vätternrundan"}), "A")

    def test_race_categories_are_recognized(self):
        for category in ("RACE", "RACE_A", "RACE_B", "RACE_C"):
            self.assertTrue(is_race_event({"category": category}))
        self.assertFalse(is_race_event({"category": "WORKOUT"}))


if __name__ == "__main__":
    unittest.main()

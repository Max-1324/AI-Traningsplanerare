"""Strength training: from the annual plan, and counting what you already did or planned."""
import unittest
from datetime import date, timedelta

from training_plan.engine.calendar_context import atp_weeks, sport_split, strength_sessions, week_tss
from training_plan.engine.periodization import apply_calendar_targets, build_week_targets
from training_plan.engine.planner import PlannerInputs, build_deterministic_plan
from training_plan.engine.postprocess import apply_safety_rules
from training_plan.engine.validation import validate_postprocessed_plan

MONDAY = date(2026, 10, 5)


def _target(day, sport, hours=None, load=None):
    return {"category": "TARGET", "type": sport, "name": "Week", "start_date_local": f"{day}T00:00:00",
            "time_target": int(hours * 3600) if hours else None, "load_target": load}


def _week(*entries):
    return atp_weeks([_target("2026-10-05", *e) for e in entries])["2026-10-05"]


def _strength_days(result):
    return sorted(date.fromisoformat(d.date) for d in result.plan.days if d.intervals_type == "WeightTraining")


class TestStrengthInTheAnnualPlan(unittest.TestCase):
    def test_strength_is_not_counted_as_endurance_load(self):
        week = _week(("Ride", 5), ("Run", 2.5), ("WeightTraining", 1))
        self.assertEqual(week_tss(week, 55), 7.5 * 55)
        self.assertNotIn("WeightTraining", sport_split(week, 55))

    def test_other_is_treated_as_strength(self):
        week = _week(("Ride", 5), ("Run", 2.5), ("Other", 1))
        self.assertEqual(week_tss(week, 55), 7.5 * 55)
        self.assertEqual(set(sport_split(week, 55)), {"cycling", "Run"})
        self.assertEqual(strength_sessions(week, 55), 2)

    def test_all_activities_target_loses_the_strength_part(self):
        events = [_target("2026-10-05", "WeightTraining", hours=1),
                  {"category": "TARGET", "name": "Week", "start_date_local": "2026-10-05T00:00:00",
                   "load_target": 500}]
        self.assertEqual(week_tss(atp_weeks(events)["2026-10-05"], 55), 500 - 55)

    def test_hours_become_30_minute_sessions(self):
        self.assertEqual(strength_sessions(_week(("Ride", 5), ("WeightTraining", 0.5)), 55), 1)
        self.assertEqual(strength_sessions(_week(("Ride", 5), ("WeightTraining", 1)), 55), 2)
        self.assertEqual(strength_sessions(_week(("Ride", 5), ("WeightTraining", 3)), 55), 3, "at most 3 a week")
        self.assertEqual(strength_sessions(_week(("Ride", 5), ("WeightTraining", None, 55)), 55), 2,
                         "a load target is turned back into time")
        self.assertIsNone(strength_sessions(_week(("Ride", 5)), 55))

    def test_week_targets_carry_the_strength_count(self):
        events = [_target(f"{MONDAY + timedelta(weeks=w)}", sport, hours=h)
                  for w in range(2) for sport, h in (("Ride", 6), ("WeightTraining", 1))]
        targets = apply_calendar_targets(build_week_targets(60, {"week_in_block": 1}, MONDAY, target_ctl=100),
                                         atp_weeks(events), 55)
        self.assertEqual([t.strength_sessions for t in targets[:3]], [2, 2, None])
        self.assertIn("strength 2", targets[0].summary())

    def test_strength_only_plan_keeps_our_load_target(self):
        own = build_week_targets(60, {"week_in_block": 1}, MONDAY, target_ctl=100)
        targets = apply_calendar_targets(own, atp_weeks([_target("2026-10-05", "WeightTraining", hours=1)]), 55)
        self.assertEqual(targets[0].tss_target, own[0].tss_target)
        self.assertEqual(targets[0].strength_sessions, 2)


def _inputs(today=MONDAY, strength=None, wib=1, **overrides):
    targets = build_week_targets(55, {"week_in_block": wib}, today, target_ctl=100)
    for t in targets:
        t.strength_sessions = strength
    dates = [(today + timedelta(days=i)).isoformat() for i in range(10)]
    params = dict(today=today, horizon_dates=dates, week_targets=targets, primary_sport="VirtualRide", phase="Base")
    params.update(overrides)
    return PlannerInputs(**params)


class TestStrengthInThePlan(unittest.TestCase):
    def _clean(self, inp, result):
        plan, changes = apply_safety_rules(result.plan, hrv={"state": "NORMAL"}, budgets={}, locked=set(),
                                           athlete={"id": 1}, today=inp.today,
                                           max_strength=result.max_strength_sessions)
        self.assertEqual([c for c in changes if not c.startswith("TRAIN-LOW")], [])
        validation = validate_postprocessed_plan(
            plan, athlete={"id": 1}, tss_budget=result.horizon_tss_target, postprocess_changes=changes,
            review_context={"today": inp.today.isoformat(), "max_hard_days": result.max_hard_days})
        self.assertEqual(validation.hard_failures, [])

    def test_default_is_two_a_week_in_base_and_one_towards_a_goal(self):
        # Two sessions build strength; one keeps it while training for a goal (Rønnestad et al.).
        for phase, expected in (("Base", 2), ("Build", 1)):
            inp = _inputs()
            inp.phase = phase
            result = build_deterministic_plan(inp)
            first_week = [d for d in _strength_days(result) if d < MONDAY + timedelta(days=7)]
            self.assertEqual(len(first_week), expected, phase)
            self.assertEqual(result.plan.days[0].date, MONDAY.isoformat())

    def test_annual_plan_count_with_two_days_between(self):
        inp = _inputs(strength=2)
        result = build_deterministic_plan(inp)
        days = _strength_days(result)
        self.assertEqual(len([d for d in days if d < MONDAY + timedelta(days=7)]), 2)
        self.assertTrue(all((b - a).days >= 2 for a, b in zip(days, days[1:])), days)
        self.assertGreaterEqual(result.max_strength_sessions, len(days))
        key_days = {d.date for d in result.plan.days if result.roles.get(f"{d.date}|{d.slot}") == "key"}
        for d in result.plan.days:
            if d.intervals_type == "WeightTraining" and d.date in key_days:
                self.assertEqual(d.slot, "PM", "intervals first, strength after")
        self._clean(inp, result)

    def test_recovery_week_keeps_one(self):
        result = build_deterministic_plan(_inputs(strength=3, wib=4))
        self.assertEqual(len([d for d in _strength_days(result) if d < MONDAY + timedelta(days=7)]), 1)

    def test_strength_done_this_week_counts(self):
        wednesday = MONDAY + timedelta(days=2)
        result = build_deterministic_plan(_inputs(today=wednesday, strength=1, strength_dates=["2026-10-05"]))
        self.assertFalse([d for d in _strength_days(result) if d < MONDAY + timedelta(days=7)],
                         "Monday's session covers this week")

    def test_no_strength_the_day_after_your_own_session(self):
        tuesday = MONDAY + timedelta(days=1)
        result = build_deterministic_plan(_inputs(today=tuesday, strength=2, strength_dates=["2026-10-05"]))
        days = _strength_days(result)
        self.assertNotIn(tuesday, days)
        self.assertTrue(all((d - MONDAY).days >= 2 for d in days), days)

    def test_planned_strength_in_your_calendar_counts(self):
        from training_plan.app.deterministic import _strength_dates
        manual = [{"type": "WeightTraining", "start_date_local": "2026-10-08T07:00:00"},
                  {"type": "Ride", "start_date_local": "2026-10-09T07:00:00"}]
        acts = [{"type": "WeightTraining", "name": "Gym", "start_date_local": "2026-10-04T18:00:00"},
                {"type": "Ride", "name": "Endurance", "start_date_local": "2026-10-03T10:00:00"},
                {"type": "WeightTraining", "name": "Gym", "start_date_local": "2026-09-20T18:00:00"}]
        self.assertEqual(_strength_dates(acts, manual, MONDAY), ["2026-10-04", "2026-10-08"])
        result = build_deterministic_plan(_inputs(strength=1, strength_dates=["2026-10-08"]))
        self.assertFalse([d for d in _strength_days(result) if d < MONDAY + timedelta(days=7)])


if __name__ == "__main__":
    unittest.main()


class TestStrengthProgram(unittest.TestCase):
    def test_program_progresses_after_the_first_week_and_never_resets(self):
        from training_plan.engine.planning.workouts import get_strength_workout_for_phase
        first = get_strength_workout_for_phase({"week_in_block": 1, "block_number": 1, "phase_name": "Base"})
        later = [get_strength_workout_for_phase({"week_in_block": w, "block_number": 2, "phase_name": "Base"})
                 for w in (1, 2, 3)]
        self.assertIn("Base strength", first["name"])
        self.assertTrue(all("Build strength" in p["name"] for p in later))

    def test_heavy_option(self):
        import os
        from unittest import mock
        from training_plan.engine.planning.workouts import get_strength_workout_for_phase
        with mock.patch.dict(os.environ, {"STRENGTH_STYLE": "heavy"}):
            program = get_strength_workout_for_phase({"week_in_block": 2, "block_number": 1, "phase_name": "Base"})
        self.assertIn("Heavy", program["name"])

    def test_no_unsupported_injury_claims(self):
        from training_plan.engine.libraries import PREHAB_LIBRARY, STRENGTH_LIBRARY
        notes = " ".join(e.get("notes", "") for lib in (STRENGTH_LIBRARY, PREHAB_LIBRARY)
                         for p in lib.values() for e in p["exercises"]).lower()
        for claim in ("prevents", "vmo", "mandatory", "strongest prevention"):
            self.assertNotIn(claim, notes)

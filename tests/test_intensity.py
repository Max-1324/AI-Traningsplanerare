"""Hard sessions per week, session kinds, and following the annual plan's sport split."""
import sys
import unittest
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from test_deterministic_planner import BUDGETS, _inputs, _run  # noqa: E402

from training_plan.engine.calendar_context import atp_weeks, sport_split  # noqa: E402
from training_plan.engine.intensity import (  # noqa: E402
    IntensitySignals,
    assign_emphasis,
    hard_sessions_by_week,
    hard_sessions_for_week,
    key_session_types,
)
from training_plan.engine.periodization import apply_calendar_targets, build_week_targets  # noqa: E402
from training_plan.engine.planner import PlannerInputs, build_deterministic_plan, tss_of  # noqa: E402
from training_plan.engine.planner import endurance_session  # noqa: E402
from training_plan.engine.postprocess import apply_safety_rules  # noqa: E402
from training_plan.engine.postprocess.recovery import enforce_sport_budget  # noqa: E402
from training_plan.engine.validation import validate_postprocessed_plan  # noqa: E402

MONDAY = date(2026, 10, 5)
GOOD_HISTORY = [2, 2, 0, 2]   # two a week, one recovery week


def _week(kind="build", max_key=2):
    target = build_week_targets(60, {"week_in_block": 4 if kind == "deload" else 2}, MONDAY, target_ctl=100)[0]
    return target if kind == "deload" else target.__class__(**{**target.__dict__, "kind": kind,
                                                               "max_key_sessions": max_key})


class TestHardSessionCount(unittest.TestCase):
    def test_two_is_the_default(self):
        self.assertEqual(hard_sessions_for_week(_week(), IntensitySignals(ctl=60, tsb=-5), hours=7)[0], 2)

    def test_recovery_race_and_taper_weeks_keep_their_own_count(self):
        self.assertEqual(hard_sessions_for_week(_week("deload"), IntensitySignals())[0], 0)
        self.assertEqual(hard_sessions_for_week(_week("taper", 1), IntensitySignals())[0], 1)

    def test_poor_recovery_means_one(self):
        cases = {
            "HRV": IntensitySignals(hrv_state="LOW"),
            "form": IntensitySignals(ctl=60, tsb=-25),          # -42% of CTL
            "burnout": IntensitySignals(burnout=True),
            "return to play": IntensitySignals(rtp_active=True),
            "sick": IntensitySignals(recently_sick=True),
            "missed": IntensitySignals(key_planned=6, key_missed=4),
        }
        for label, signals in cases.items():
            with self.subTest(label):
                count, reason = hard_sessions_for_week(_week(), signals, hours=12)
                self.assertEqual(count, 1)
                self.assertTrue(reason.startswith("1 hard session"))

    def test_scale_is_relative_to_the_athletes_own_fitness(self):
        # TSB -25 is fine at CTL 120 (-21%) but high risk at CTL 60 (-42%).
        self.assertEqual(hard_sessions_for_week(_week(), IntensitySignals(ctl=120, tsb=-25))[0], 2)
        self.assertEqual(hard_sessions_for_week(_week(), IntensitySignals(ctl=60, tsb=-25))[0], 1)

    def test_few_training_days_means_one(self):
        self.assertEqual(hard_sessions_for_week(_week(), IntensitySignals(), trainable_days=2)[0], 1)

    def test_three_needs_history_time_and_good_recovery(self):
        good = IntensitySignals(hrv_state="NORMAL", ctl=80, tsb=-8, hard_by_week=GOOD_HISTORY)
        self.assertEqual(hard_sessions_for_week(_week(), good, trainable_days=7, hours=11)[0], 3)
        self.assertEqual(hard_sessions_for_week(_week(), good, trainable_days=7, hours=7)[0], 2, "not enough time")
        self.assertEqual(hard_sessions_for_week(_week(), good, trainable_days=5, hours=11)[0], 2, "too few days")
        new = IntensitySignals(hrv_state="NORMAL", hard_by_week=[1, 2, 0, 2])
        self.assertEqual(hard_sessions_for_week(_week(), new, hours=11)[0], 2, "not used to two a week yet")
        tired = IntensitySignals(hrv_state="SLIGHTLY_LOW", hard_by_week=GOOD_HISTORY)
        self.assertEqual(hard_sessions_for_week(_week(), tired, hours=11)[0], 2)

    def test_history_counts_days_with_hard_sessions_per_week(self):
        acts = [{"start_date_local": "2026-09-29T07:00:00", "name": "VO2max 5x3"},
                {"start_date_local": "2026-10-01T07:00:00", "name": "Threshold"},
                {"start_date_local": "2026-10-01T18:00:00", "name": "Threshold again"},
                {"start_date_local": "2026-09-23T07:00:00", "name": "Easy"}]
        kinds = {"VO2max 5x3": "vo2", "Threshold": "threshold", "Threshold again": "threshold", "Easy": "endurance"}
        counts = hard_sessions_by_week(acts, MONDAY, lambda a: kinds[a["name"]])
        self.assertEqual(counts, [0, 0, 0, 2])


class TestSessionKinds(unittest.TestCase):
    def test_every_week_has_vo2max_and_threshold(self):
        for emphasis in ("base", "build"):
            kinds = key_session_types(2, emphasis=emphasis, week_start="2026-10-05")
            self.assertEqual(len(kinds), 3)
            self.assertTrue(any(k.startswith("vo2max") for k in kinds[:2]))
            self.assertTrue(any(k in ("threshold_intervals", "tempo_sustained") for k in kinds[:2]))

    def test_phase_decides_the_format(self):
        self.assertEqual(key_session_types(2, emphasis="base")[:2], ["vo2max_short", "tempo_sustained"])
        self.assertEqual(key_session_types(2, emphasis="build")[:2], ["vo2max_intervals", "threshold_intervals"])

    def test_one_session_alternates_week_by_week(self):
        weeks = [(MONDAY + timedelta(weeks=i)).isoformat() for i in range(4)]
        firsts = [key_session_types(1, emphasis="build", week_start=w)[0] for w in weeks]
        self.assertEqual(len(set(firsts)), 2)
        self.assertNotEqual(firsts[0], firsts[1])

    def test_kind_done_this_week_goes_last(self):
        self.assertEqual(key_session_types(2, emphasis="build", done_kinds=["vo2"])[0], "threshold_intervals")

    def test_emphasis_from_plan_race_or_rotation(self):
        self.assertEqual(assign_emphasis("2026-10-05", atp_phase="Build"), "build")
        self.assertEqual(assign_emphasis("2026-10-05", atp_phase="Base"), "base")
        self.assertEqual(assign_emphasis("2026-10-05", race_phase="Build"), "build")
        self.assertEqual(assign_emphasis("2026-10-05", race_phase="Base", has_race=True), "base")
        # No plan and no race: alternates in six-week blocks.
        rotation = [assign_emphasis((MONDAY + timedelta(weeks=i)).isoformat()) for i in range(13)]
        self.assertEqual(set(rotation), {"base", "build"})
        changes = sum(1 for a, b in zip(rotation, rotation[1:]) if a != b)
        self.assertEqual(changes, 2)

    def test_planner_without_race_plans_both_kinds_every_build_week(self):
        for phase in ("Base", "Build"):
            inp = _inputs(today=MONDAY, phase=phase, focus_areas=[])
            result, plan, changes, validation = _run(inp)
            self.assertEqual(validation.hard_failures, [])
            first_week = result.week_targets[0]
            titles = [d.title for d in plan.days if first_week.contains(d.date)
                      and result.roles.get(f"{d.date}|{d.slot}") == "key"]
            self.assertEqual(len(titles), 2, titles)
            self.assertTrue(any("VO2max" in t for t in titles), titles)
            self.assertTrue(any("Threshold" in t or "Tempo" in t for t in titles), titles)

    def test_three_hard_sessions_keep_the_sunday_long_session(self):
        targets = build_week_targets(80, {"week_in_block": 2}, MONDAY, target_ctl=120)
        targets[0].max_key_sessions = 3
        inp = PlannerInputs(today=MONDAY, horizon_dates=[(MONDAY + timedelta(days=i)).isoformat() for i in range(7)],
                            week_targets=targets, primary_sport="VirtualRide", phase="Build")
        result = build_deterministic_plan(inp)
        keys = [d for d in result.plan.days if result.roles.get(f"{d.date}|{d.slot}") == "key"]
        self.assertEqual(len(keys), 3)
        self.assertIn("long", [result.roles.get(f"{d.date}|{d.slot}") for d in result.plan.days if d.date == "2026-10-11"])
        plan, changes = apply_safety_rules(result.plan, hrv={"state": "NORMAL"}, budgets={}, locked=set(),
                                           athlete={"id": 1}, today=MONDAY)
        self.assertEqual([c for c in changes if not c.startswith("TRAIN-LOW")], [])
        validation = validate_postprocessed_plan(
            plan, athlete={"id": 1}, tss_budget=result.horizon_tss_target, postprocess_changes=changes,
            review_context={"today": MONDAY.isoformat(), "max_hard_days": result.max_hard_days})
        self.assertEqual(validation.hard_failures, [])


def _split_events(run_h=2.5, ride_h=5.0, weeks=2):
    events = []
    for w in range(weeks):
        d = (MONDAY + timedelta(weeks=w)).isoformat()
        for sport, hours in (("Run", run_h), ("Ride", ride_h)):
            events.append({"category": "TARGET", "type": sport, "name": "Week",
                           "start_date_local": f"{d}T00:00:00", "time_target": int(hours * 3600)})
    return events


class TestSportSplit(unittest.TestCase):
    def _plan(self, run_budget=150, primary="Ride", **overrides):
        targets = build_week_targets(45, {"week_in_block": 1}, MONDAY, target_ctl=85)
        targets = apply_calendar_targets(targets, atp_weeks(_split_events()), 55)
        budgets = {"Run": {"remaining": run_budget}}
        dates = [(MONDAY + timedelta(days=i)).isoformat() for i in range(14)]
        result = build_deterministic_plan(PlannerInputs(
            today=MONDAY, horizon_dates=dates, week_targets=targets, primary_sport=primary,
            sport_budgets=budgets, **overrides))
        return targets, result

    @staticmethod
    def _minutes(result, target):
        minutes = Counter()
        for d in result.plan.days:
            if target.contains(d.date):
                group = "cycling" if "Ride" in d.intervals_type else d.intervals_type
                minutes[group] += d.duration_min
        return minutes

    def test_split_comes_from_per_sport_targets(self):
        week = atp_weeks(_split_events(weeks=1))["2026-10-05"]
        self.assertEqual(sport_split(week, 55), {"Run": 0.333, "cycling": 0.667})

    def test_each_week_follows_the_split(self):
        targets, result = self._plan()
        self.assertEqual(targets[0].sport_split, {"Run": 0.333, "cycling": 0.667})
        for target in targets[:2]:
            minutes = self._minutes(result, target)
            self.assertGreaterEqual(minutes["Run"], 120, minutes)
            self.assertLessEqual(minutes["Run"], 150, minutes)
            self.assertGreater(minutes["cycling"], 240, minutes)
            week_tss = sum(tss_of(d) for d in result.plan.days if target.contains(d.date))
            self.assertGreater(week_tss, 0.9 * target.tss_target)

    def test_running_is_held_to_its_weekly_budget_and_cycling_takes_the_rest(self):
        targets, result = self._plan(run_budget=60)
        for target in targets[:2]:
            minutes = self._minutes(result, target)
            self.assertLessEqual(minutes["Run"], 60)
            week_tss = sum(tss_of(d) for d in result.plan.days if target.contains(d.date))
            self.assertGreater(week_tss, 0.9 * target.tss_target)

    def test_injured_runner_moves_everything_to_the_bike(self):
        targets, result = self._plan(avoid_sports={"Run"})
        minutes = self._minutes(result, targets[0])
        self.assertEqual(minutes["Run"], 0)
        week_tss = sum(tss_of(d) for d in result.plan.days if targets[0].contains(d.date))
        self.assertGreater(week_tss, 0.9 * targets[0].tss_target)

    def test_only_the_plans_sports_are_used(self):
        targets, result = self._plan()
        sports = {d.intervals_type for d in result.plan.days} - {"Rest", "WeightTraining"}
        self.assertLessEqual(sports, {"Run", "Ride", "VirtualRide"})

    def test_largest_share_decides_the_main_sport(self):
        targets, result = self._plan(primary="Run")
        keys = [d for d in result.plan.days if result.roles.get(f"{d.date}|{d.slot}") == "key"]
        self.assertTrue(keys)
        self.assertTrue(all("Ride" in d.intervals_type for d in keys))

    def test_planner_output_passes_the_safety_rules(self):
        inp_targets, result = self._plan()
        _, changes = apply_safety_rules(result.plan, hrv={"state": "NORMAL"}, budgets={"Run": {"remaining": 150}},
                                        locked=set(), athlete={"id": 1}, today=MONDAY)
        self.assertEqual([c for c in changes if not c.startswith("TRAIN-LOW")], [])


class TestWeeklySportBudget(unittest.TestCase):
    def test_budget_applies_per_calendar_week(self):
        days = [endurance_session("2026-10-06", "Run", 60), endurance_session("2026-10-08", "Run", 60),
                endurance_session("2026-10-13", "Run", 60), endurance_session("2026-10-15", "Run", 60)]
        _, changes = enforce_sport_budget(days, {"Run": {"remaining": 120}}, today=MONDAY)
        self.assertEqual(changes, [])

    def test_minutes_done_this_week_count(self):
        days = [endurance_session("2026-10-08", "Run", 60), endurance_session("2026-10-13", "Run", 60)]
        _, changes = enforce_sport_budget(days, {"Run": {"remaining": 90, "done_this_week": 45}},
                                          today=date(2026, 10, 7))
        self.assertEqual(len(changes), 1)
        self.assertIn("2026-10-08", changes[0])

    def test_planner_respects_the_budget_in_every_week(self):
        inp = _inputs(today=MONDAY, primary_sport="Run",
                      sport_budgets={**BUDGETS, "Run": {"remaining": 90, "done_this_week": 0}})
        result, plan, changes, validation = _run(inp)
        for monday in ("2026-10-05", "2026-10-12"):
            start = date.fromisoformat(monday)
            week = {(start + timedelta(days=i)).isoformat() for i in range(7)}
            self.assertLessEqual(sum(d.duration_min for d in result.plan.days
                                     if d.intervals_type == "Run" and d.date in week), 90)


if __name__ == "__main__":
    unittest.main()

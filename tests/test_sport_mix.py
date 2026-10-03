"""Multi-sport planning: the sport mix per week and how the planner follows it."""
import unittest
from collections import Counter
from dataclasses import replace
from datetime import date, timedelta

from training_plan.app.deterministic import _apply_sport_mix
from training_plan.engine.calendar_context import sport_group
from training_plan.engine.periodization import build_week_targets
from training_plan.engine.planner import PlannerInputs, build_deterministic_plan, tss_of
from training_plan.engine.postprocess import apply_safety_rules
from training_plan.engine.sport_mix import BASE_MIX, FOCUS_SHARE, week_sport_split

MONDAY = date(2026, 10, 5)
GROUPS = {"cycling", "Run", "ski", "Swim"}
BUDGETS = {"Run": {"remaining": 150}, "RollerSki": {"remaining": 150}, "NordicSki": {"remaining": 300}}


def _race(weeks_out, sport, priority="A", name="Race"):
    day = MONDAY + timedelta(weeks=weeks_out)
    return {"name": name, "category": f"RACE_{priority}", "type": sport, "start_date_local": day.isoformat()}


def _plan(split, *, today=MONDAY, focus="", constraints=None, weather=None, budgets=BUDGETS, days=14):
    targets = build_week_targets(50, {"week_in_block": 1, "block_number": 1}, today)
    targets = [replace(t, sport_split=split, focus=focus, source="intervals.icu") for t in targets]
    horizon = [(today + timedelta(days=i)).isoformat() for i in range(days)]
    inp = PlannerInputs(today=today, horizon_dates=horizon, week_targets=targets, primary_sport="VirtualRide",
                        sport_budgets=budgets, constraints=constraints or [], weather=weather or [])
    return targets, build_deterministic_plan(inp)


def _week(result, target):
    return [d for d in result.plan.days if target.contains(d.date) and d.duration_min > 0]


class TestSportMix(unittest.TestCase):
    def test_base_mix_without_goal(self):
        split, focus = week_sport_split(MONDAY.isoformat(), [], GROUPS)
        self.assertEqual(focus, "")
        self.assertEqual(set(split), {"cycling", "Run", "ski"})
        self.assertAlmostEqual(split["cycling"], BASE_MIX["cycling"] / sum(BASE_MIX.values()), places=2)

    def test_goal_sport_takes_over_gradually(self):
        shares = [week_sport_split(MONDAY.isoformat(), [_race(w, "NordicSki")], GROUPS)[0]["ski"]
                  for w in (30, 20, 12, 6)]
        self.assertEqual(shares, sorted(shares))
        self.assertAlmostEqual(shares[-1], FOCUS_SHARE, places=2)
        split, focus = week_sport_split(MONDAY.isoformat(), [_race(6, "NordicSki")], GROUPS)
        self.assertEqual(focus, "ski")
        self.assertGreater(split["Run"], 0)     # the other sports stay in the mix
        self.assertGreater(split["cycling"], 0)

    def test_c_races_and_races_without_sport_do_not_set_a_goal(self):
        races = [_race(6, "Run", priority="C"), {"name": "Lopp", "category": "RACE_A",
                                                 "start_date_local": (MONDAY + timedelta(weeks=6)).isoformat()}]
        self.assertEqual(week_sport_split(MONDAY.isoformat(), races, GROUPS)[1], "")

    def test_swimming_only_with_a_swim_goal(self):
        self.assertNotIn("Swim", week_sport_split(MONDAY.isoformat(), [], GROUPS)[0])
        split, _ = week_sport_split(MONDAY.isoformat(), [_race(6, "Swim", name="Vansbro")], GROUPS)
        self.assertIn("Swim", split)

    def test_annual_plan_per_sport_targets_win(self):
        targets = build_week_targets(50, {"week_in_block": 1}, MONDAY)
        targets = [replace(targets[0], sport_split={"Run": 0.7, "cycling": 0.3})] + targets[1:]
        mixed = _apply_sport_mix(targets, [_race(6, "NordicSki")])
        self.assertEqual(mixed[0].sport_split, {"Run": 0.7, "cycling": 0.3})
        self.assertEqual(mixed[0].focus, "Run")
        self.assertIn("ski", mixed[1].sport_split)

    def test_total_time_only_still_plans_running(self):
        targets = _apply_sport_mix(build_week_targets(50, {"week_in_block": 1}, MONDAY), [])
        horizon = [(MONDAY + timedelta(days=i)).isoformat() for i in range(14)]
        result = build_deterministic_plan(PlannerInputs(
            today=MONDAY, horizon_dates=horizon, week_targets=targets, primary_sport="VirtualRide",
            sport_budgets=BUDGETS))
        self.assertIn("Run", {d.intervals_type for d in result.plan.days})


class TestMultiSportPlanner(unittest.TestCase):
    def test_ski_block_keeps_the_load_and_the_other_sports(self):
        targets, result = _plan({"ski": 0.65, "cycling": 0.23, "Run": 0.12}, focus="ski")
        for target in targets[:2]:
            week = _week(result, target)
            # Day caps (weekday max, easy days after key sessions) and the ski budget limit how
            # much one week can hold; before multi-sport planning this week reached only ~40 %.
            self.assertGreaterEqual(sum(tss_of(d) for d in week), 0.8 * target.tss_target)
            groups = Counter(sport_group(d.intervals_type) for d in week)
            self.assertGreaterEqual(groups["ski"], 1)
            self.assertGreaterEqual(groups["cycling"], 2)
            self.assertGreaterEqual(groups["Run"], 1)
        keys = [d for d in result.plan.days if result.roles.get(f"{d.date}|{d.slot}") == "key"]
        self.assertTrue(any(d.intervals_type == "RollerSki" for d in keys))

    def test_no_access_to_roller_skis_moves_the_load_elsewhere(self):
        days = [(MONDAY + timedelta(days=i)).isoformat() for i in range(14)]
        constraints = [{"date": d, "blocked_types": ["RollerSki"]} for d in days]
        targets, result = _plan({"ski": 0.65, "cycling": 0.23, "Run": 0.12}, focus="ski", constraints=constraints)
        self.assertNotIn("RollerSki", {d.intervals_type for d in result.plan.days})
        for target in targets[:2]:
            self.assertGreaterEqual(sum(tss_of(d) for d in _week(result, target)), 0.8 * target.tss_target)

    def test_no_roller_skiing_in_snow_or_frost(self):
        weather = [{"date": (MONDAY + timedelta(days=i)).isoformat(), "temp_afternoon": -2, "temp_min": -6,
                    "weathercode": "snow"} for i in range(14)]
        _, result = _plan({"cycling": 0.6, "Run": 0.25, "ski": 0.15}, weather=weather)
        self.assertNotIn("RollerSki", {d.intervals_type for d in result.plan.days})

    def test_swimming_is_never_a_filler_or_substitute(self):
        days = [(MONDAY + timedelta(days=i)).isoformat() for i in range(14)]
        constraints = [{"date": d, "blocked_types": ["RollerSki"]} for d in days]
        _, result = _plan({}, constraints=constraints)
        self.assertNotIn("Swim", {d.intervals_type for d in result.plan.days})
        self.assertNotIn("Swim", {o.intervals_type for opts in result.options.values() for o in opts})

    def test_each_sport_keeps_its_minimum_sessions(self):
        targets, result = _plan({"cycling": 0.6, "Run": 0.25, "ski": 0.15})
        for target in targets[1:2]:   # a full calendar week
            groups = Counter(sport_group(d.intervals_type) for d in _week(result, target)
                             if d.intervals_type != "WeightTraining")
            self.assertGreaterEqual(groups["Run"], 2, groups)
            self.assertGreaterEqual(groups["cycling"], 2, groups)
            self.assertGreaterEqual(groups["ski"], 1, groups)

    def test_multi_sport_plan_passes_the_safety_rules(self):
        for split, focus in (({"ski": 0.65, "cycling": 0.23, "Run": 0.12}, "ski"),
                             ({"cycling": 0.6, "Run": 0.25, "ski": 0.15}, "")):
            _, result = _plan(split, focus=focus)
            _, changes = apply_safety_rules(
                result.plan, hrv={"state": "NORMAL"}, budgets=BUDGETS, locked=set(), athlete={"id": 1},
                today=MONDAY, max_strength=result.max_strength_sessions, max_rollski=result.max_rollski_per_week)
            self.assertEqual([c for c in changes if not c.startswith("TRAIN-LOW")], [])


if __name__ == "__main__":
    unittest.main()

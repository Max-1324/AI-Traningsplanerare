"""Safe progression in weight-bearing sports: weekly budget, single-session cap, per-sport ACWR."""
import unittest
from datetime import date, datetime, timedelta
from unittest import mock

import training_plan.engine.analysis.load as load
from training_plan.engine.periodization import build_week_targets
from training_plan.engine.planner import PlannerInputs, build_deterministic_plan

NOW = datetime(2026, 10, 5, 9)
MONDAY = date(2026, 10, 5)


class _FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW


def _act(days_ago, sport, minutes, tss=40):
    start = NOW - timedelta(days=days_ago)
    return {"type": sport, "start_date_local": start.strftime("%Y-%m-%dT%H:%M:%S"),
            "moving_time": minutes * 60, "icu_training_load": tss}


def _budget(activities, sport="Run"):
    with mock.patch.object(load, "datetime", _FixedDateTime):
        return load.sport_budget(sport, activities, [])


class TestSportBudget(unittest.TestCase):
    def test_steady_volume_grows_ten_percent(self):
        steady = [_act(d, "Run", 30) for d in (1, 3, 8, 10)]      # 60 min a week
        self.assertEqual(_budget(steady)["max_budget"], 66)       # was 88 (+47 %)

    def test_restart_floor_only_without_recent_history(self):
        self.assertEqual(_budget([])["max_budget"], 60)
        light = [_act(2, "Run", 20)]
        self.assertLess(_budget(light)["max_budget"], 60)          # no jump to the floor


class TestPerSportAcwr(unittest.TestCase):
    def test_steady_load_gives_ratio_near_one_and_a_spike_shows(self):
        with mock.patch.object(load, "date", mock.Mock(today=lambda: MONDAY)):
            steady = load.per_sport_acwr([_act(d, "Run", 40) for d in range(0, 28, 2)])["Run"]
            spike = load.per_sport_acwr([_act(d, "Run", 30, 20) for d in range(8, 28, 4)]
                                        + [_act(d, "Run", 70, 80) for d in range(0, 6)])["Run"]
        self.assertAlmostEqual(steady["ratio"], 1.0, delta=0.15)
        self.assertGreater(spike["ratio"], 1.5)


class TestSessionCap(unittest.TestCase):
    def _plan(self, longest):
        targets = build_week_targets(50, {"week_in_block": 1}, MONDAY)
        horizon = [(MONDAY + timedelta(days=i)).isoformat() for i in range(14)]
        return build_deterministic_plan(PlannerInputs(
            today=MONDAY, horizon_dates=horizon, week_targets=targets, primary_sport="Run",
            sport_budgets={"Run": {"remaining": 600}}, longest_session_30d=longest,
            available_sports=[{"intervals_type": "Run", "injury_risk": "high"}]))

    def test_no_run_longer_than_110_percent_of_the_longest_recent_one(self):
        runs = [d for d in self._plan({"Run": 40}).plan.days if d.intervals_type == "Run"]
        self.assertTrue(runs)
        self.assertLessEqual(max(d.duration_min for d in runs), 44)

    def test_without_recent_runs_the_sport_restarts_short(self):
        runs = [d for d in self._plan({}).plan.days if d.intervals_type == "Run"]
        self.assertTrue(all(d.duration_min <= 30 for d in runs), [d.duration_min for d in runs])


if __name__ == "__main__":
    unittest.main()

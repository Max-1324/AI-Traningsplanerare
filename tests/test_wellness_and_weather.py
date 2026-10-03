"""No watch (HRV, sleep), outdoor riding weather rule and winter limits from the calendar."""
import unittest
from datetime import date, timedelta

from training_plan.engine.analysis import calculate_hrv, calculate_readiness_score
from training_plan.engine.calendar_context import is_context_event
from training_plan.engine.intensity import IntensitySignals, hard_sessions_for_week
from training_plan.engine.libraries import _parse_sport_names, parse_constraints_from_events
from training_plan.engine.periodization import build_week_targets
from training_plan.engine.planner import PlannerInputs, _Planner, build_deterministic_plan

TODAY = date(2026, 12, 1)


def _series(today, *, days=60, missing_last=0, low_week_before_gap=False, value=lambda i: 58 + i % 8):
    """Daily wellness rows ending today; the last `missing_last` days have no HRV."""
    rows = []
    for i in range(days, -1, -1):
        d = today - timedelta(days=i)
        hrv = None
        if i >= missing_last:
            hrv = 44 if low_week_before_gap and i < missing_last + 7 else value(i)
        rows.append({"id": d.isoformat(), "hrv": hrv, "ctl": 50})
    return rows


class TestHrvWithoutWatch(unittest.TestCase):
    def test_broken_watch_is_assumed_normal_not_stuck_on_old_values(self):
        # Low week right before the watch broke 25 days ago: the old LOW must not stick.
        result = calculate_hrv(_series(TODAY, missing_last=25, low_week_before_gap=True), today=TODAY)
        self.assertEqual(result["state"], "NORMAL")
        self.assertFalse(result["measured"])
        self.assertEqual(result["deviation_pct"], 0.0)

    def test_measuring_watch_is_used(self):
        result = calculate_hrv(_series(TODAY), today=TODAY)
        self.assertTrue(result["measured"])
        self.assertEqual(result["method"], "ln_rmssd_swc")

    def test_a_few_measurements_this_week_are_enough(self):
        rows = _series(TODAY, missing_last=4)          # three measured days in the last seven
        self.assertTrue(calculate_hrv(rows, today=TODAY)["measured"])
        rows = _series(TODAY, missing_last=5)          # only two
        self.assertFalse(calculate_hrv(rows, today=TODAY)["measured"])

    def test_new_watch_after_a_gap_starts_a_new_baseline(self):
        # Old watch ~60 ms, then three weeks without, then a new watch reading ~40 ms.
        rows = _series(TODAY, days=70, value=lambda i: 40 + i % 3 if i < 10 else 60 + i % 6)
        for row in rows:
            if TODAY - timedelta(days=31) < date.fromisoformat(row["id"]) < TODAY - timedelta(days=9):
                row["hrv"] = None
        result = calculate_hrv(rows, today=TODAY)
        self.assertNotIn(result["state"], ("LOW", "SLIGHTLY_LOW"), "a lower-reading watch is not low HRV")
        self.assertTrue(result["measured"])

    def test_today_defaults_to_the_latest_row(self):
        self.assertTrue(calculate_hrv(_series(TODAY))["measured"])

    def test_missing_hrv_does_not_hold_back_three_hard_sessions(self):
        target = build_week_targets(80, {"week_in_block": 2}, TODAY, target_ctl=120)[0]
        for state in ("NORMAL", "INSUFFICIENT_DATA"):
            signals = IntensitySignals(hrv_state=state, ctl=80, tsb=-5, hard_by_week=[2, 2, 0, 2])
            self.assertEqual(hard_sessions_for_week(target, signals, trainable_days=7, hours=11)[0], 3)


class TestReadinessWithoutWatch(unittest.TestCase):
    def test_old_sleep_and_missing_hrv_count_as_normal(self):
        wellness = [{"id": (TODAY - timedelta(days=5)).isoformat(), "sleepSecs": 4 * 3600},
                    {"id": TODAY.isoformat(), "ctl": 50}]
        hrv = calculate_hrv(_series(TODAY, missing_last=20), today=TODAY)
        result = calculate_readiness_score(hrv, wellness, [], today=TODAY)
        self.assertEqual(result["components"]["sleep"], 70)
        self.assertEqual(result["components"]["hrv"], 70)
        self.assertEqual(set(result["missing"]), {"hrv", "sleep", "rhr", "rpe", "feel"})
        self.assertEqual(result["limiters"], [])
        self.assertEqual(result["score"], 70, "nothing measured: a normal day")
        self.assertIsNone(result["raw_inputs"]["sleep_hours"])
        self.assertIn("assumed normal", result["summary"])

    def test_last_nights_sleep_still_counts(self):
        wellness = [{"id": (TODAY - timedelta(days=1)).isoformat(), "sleepSecs": 5 * 3600}]
        result = calculate_readiness_score({"deviation_pct": 0, "measured": True}, wellness, [], today=TODAY)
        self.assertEqual(result["raw_inputs"]["sleep_hours"], 5.0)
        self.assertNotIn("sleep", result["missing"])


def _planner(weather, **overrides):
    targets = build_week_targets(50, {"week_in_block": 2}, TODAY, target_ctl=100)
    dates = [(TODAY + timedelta(days=i)).isoformat() for i in range(10)]
    params = dict(today=TODAY, horizon_dates=dates, week_targets=targets, primary_sport="Ride", weather=weather)
    params.update(overrides)
    return PlannerInputs(**params)


def _day(offset, **values):
    return {"date": (TODAY + timedelta(days=offset)).isoformat(), "rain_morning_mm": 0, "rain_afternoon_mm": 0,
            "temp_min": 3, **values}


class TestOutdoorRule(unittest.TestCase):
    def test_temperature_limit(self):
        p = _Planner(_planner([_day(0, temp_afternoon=4), _day(1, temp_afternoon=6)]))
        self.assertFalse(p.outdoor_ok(_day(0)["date"]))
        self.assertTrue(p.outdoor_ok(_day(1)["date"]))

    def test_snow_and_sleet_mean_indoors(self):
        p = _Planner(_planner([_day(0, temp_afternoon=6, weathercode="lightsleet")]))
        self.assertFalse(p.outdoor_ok(_day(0)["date"]))

    def test_no_morning_ride_after_a_frosty_night(self):
        p = _Planner(_planner([_day(0, temp_min=-1, temp_morning=6, temp_afternoon=9)]))
        self.assertFalse(p.outdoor_ok(_day(0)["date"], "AM"))
        self.assertTrue(p.outdoor_ok(_day(0)["date"], "MAIN"))

    def test_days_past_the_forecast_use_its_last_day(self):
        p = _Planner(_planner([_day(0, temp_afternoon=1)]))
        self.assertFalse(p.outdoor_ok((TODAY + timedelta(days=12)).isoformat()))

    def test_cold_week_has_no_outdoor_rides(self):
        cold = [_day(i, temp_morning=-3, temp_afternoon=3, temp_min=-5) for i in range(10)]
        result = build_deterministic_plan(_planner(cold))
        self.assertNotIn("Ride", {d.intervals_type for d in result.plan.days})


class TestWinterLimitFromCalendar(unittest.TestCase):
    def test_words_for_outdoor_and_indoor_cycling(self):
        self.assertEqual(_parse_sport_names("utomhuscykel"), ["Ride"])
        self.assertEqual(_parse_sport_names("utecykling"), ["Ride"])
        self.assertEqual(_parse_sport_names("inomhuscykel"), ["VirtualRide"])
        self.assertEqual(_parse_sport_names("Wahoo"), ["VirtualRide"])
        self.assertEqual(_parse_sport_names("löpning"), ["Run"])

    def test_a_winter_long_note_that_started_earlier_still_applies(self):
        note = {"id": 7, "category": "NOTE", "name": "Ej: utomhuscykel",
                "start_date_local": "2026-11-01T00:00:00", "end_date_local": "2027-04-01T00:00:00"}
        self.assertTrue(is_context_event(note), "the calendar fetch keeps it")
        constraints = parse_constraints_from_events([note])
        today = [c for c in constraints if c["date"] == TODAY.isoformat()]
        self.assertEqual(today[0]["blocked_types"], ["Ride"])
        warm = [_day(i, temp_morning=12, temp_afternoon=15, temp_min=8) for i in range(10)]
        result = build_deterministic_plan(_planner(warm, constraints=constraints))
        sports = {d.intervals_type for d in result.plan.days}
        self.assertNotIn("Ride", sports)
        self.assertIn("VirtualRide", sports)


if __name__ == "__main__":
    unittest.main()

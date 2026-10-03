"""Scenario tests for the deterministic planner (engine/planner.py).

The central invariant: whatever the situation, the planner's own plan passes the
safety rules unchanged and the deterministic validation with zero hard failures.
"""
import itertools
import unittest
from datetime import date, timedelta

from training_plan.core.catalogs import ALL_SPORTS_CATALOG, INTENSE
from training_plan.engine.periodization import build_week_targets
from training_plan.engine.planner import PlannerInputs, build_deterministic_plan, session_key, tss_of
from training_plan.engine.postprocess import apply_safety_rules
from training_plan.engine.postprocess.injury import injury_restrictions
from training_plan.engine.validation import validate_postprocessed_plan

FRIDAY = date(2026, 10, 2)
ATHLETE = {"id": "i1"}
BUDGETS = {"RollerSki": {"remaining": 120}, "Run": {"remaining": 90}}


def _horizon(today, days=10):
    return [(today + timedelta(days=i)).isoformat() for i in range(days)]


def _inputs(today=FRIDAY, week_in_block=2, ctl=55, **overrides):
    targets = build_week_targets(ctl, {"week_in_block": week_in_block, "block_number": 1}, today,
                                 races=overrides.pop("races", None), target_ctl=100)
    params = dict(
        today=today, horizon_dates=_horizon(today), week_targets=targets, phase="Build",
        mesocycle={"week_in_block": week_in_block}, workout_levels={"threshold_intervals": 2},
        primary_sport="Ride", focus_areas=["threshold"], sport_budgets=BUDGETS,
    )
    params.update(overrides)
    return PlannerInputs(**params)


def _run(inp, *, hrv_state="NORMAL", today_wellness=None, rtp=None, constraints=None,
         injury_note="", injury_profile=None, time_text=""):
    result = build_deterministic_plan(inp)
    plan, changes = apply_safety_rules(
        result.plan, hrv={"state": hrv_state, "deviation_pct": -30}, budgets=inp.sport_budgets,
        locked=set(inp.locked_dates), athlete=ATHLETE, today=inp.today, constraints=constraints,
        injury_note=injury_note, injury_profile=injury_profile, today_wellness=today_wellness,
        time_available_text=time_text,
    )
    context = {
        "today": inp.today.isoformat(),
        "locked_dates": inp.locked_dates,
        "max_hard_days": result.max_hard_days,
        "rtp_status": rtp or {},
    }
    if inp.time_available_today is not None:
        context["time_available_min"] = inp.time_available_today
    base = {d: v for d, v in inp.base_tss_by_date.items() if d in inp.horizon_dates}
    sick = bool((today_wellness or {}).get("sick"))
    validation = validate_postprocessed_plan(
        plan, athlete=ATHLETE, base_tss_by_date=base, tss_budget=0 if sick else result.horizon_tss_target,
        review_context=context, postprocess_changes=changes,
    )
    return result, plan, changes, validation


def _rule_changes(changes):
    # Warm-up/nutrition decorations are expected; anything else means the planner broke a rule.
    return [c for c in changes if not c.startswith("TRAIN-LOW-STRIP")]


class TestPlannerScenarios(unittest.TestCase):
    def assertClean(self, changes, validation):
        self.assertEqual(_rule_changes(changes), [])
        self.assertEqual(validation.hard_failures, [])

    def test_normal_build_week_is_valid_and_close_to_target(self):
        inp = _inputs(today=date(2026, 10, 5))  # Monday: whole weeks
        result, plan, changes, validation = _run(inp)
        self.assertClean(changes, validation)
        week = result.week_targets[0]
        week_tss = sum(tss_of(d) for d in plan.days if week.contains(d.date))
        self.assertGreater(week_tss, 0.8 * week.tss_target)
        self.assertLess(week_tss, 1.1 * week.tss_target)

    def test_key_sessions_come_from_library_with_progression_level(self):
        result, plan, *_ = _run(_inputs(today=date(2026, 10, 5)))
        titles = [d.title for d in plan.days]
        self.assertIn("Threshold intervals (Z4) – 4×5min Z4 / 3min rest", titles)  # level 2

    def test_deload_week_has_no_intensity(self):
        inp = _inputs(today=date(2026, 10, 5), week_in_block=4)
        result, plan, changes, validation = _run(inp)
        self.assertClean(changes, validation)
        deload = result.week_targets[0]
        for day in plan.days:
            if deload.contains(day.date):
                self.assertFalse({s.zone for s in day.workout_steps} & INTENSE, day.title)

    def test_low_readiness_only_affects_today_and_tomorrow(self):
        today = date(2026, 10, 5)
        restricted = {today.isoformat(), (today + timedelta(days=1)).isoformat()}
        inp = _inputs(today=today, restricted_dates=restricted, restriction_reason="HRV low")
        result, plan, changes, validation = _run(inp, hrv_state="LOW")
        self.assertClean(changes, validation)
        for day in plan.days:
            if day.date in restricted:
                self.assertFalse({s.zone for s in day.workout_steps} & INTENSE, day.title)
                self.assertLessEqual(day.duration_min, 60)
        self.assertGreaterEqual(result.max_hard_days, 2, "key sessions move later instead of disappearing")

    def test_locked_dates_are_left_alone(self):
        today = date(2026, 10, 5)
        locked = {"2026-10-07"}
        inp = _inputs(today=today, locked_dates=locked, base_tss_by_date={"2026-10-07": 80})
        result, plan, changes, validation = _run(inp)
        self.assertClean(changes, validation)
        self.assertNotIn("2026-10-07", {d.date for d in plan.days})

    def test_race_week_follows_protocol_and_rests_on_race_day(self):
        from training_plan.engine.analysis import race_week_protocol
        today = date(2026, 10, 5)
        races = [{"name": "Testloppet", "category": "RACE_A", "start_date_local": "2026-10-10T08:00:00"}]
        race_week = race_week_protocol(races, today, dominant_sport="Ride")
        inp = _inputs(today=today, races=races, race_week=race_week)
        result, plan, changes, validation = _run(inp)
        self.assertClean(changes, validation)
        race_day = [d for d in plan.days if d.date == "2026-10-10"]
        self.assertEqual([d.intervals_type for d in race_day], ["Rest"])
        self.assertTrue(any("Pre-race" in d.title for d in plan.days))

    def test_return_to_play_protocol(self):
        rtp = {"is_active": True, "days_off": 6}
        today = date(2026, 10, 5)
        inp = _inputs(today=today, rtp_status=rtp, restricted_dates=set(_horizon(today, 7)))
        result, plan, changes, validation = _run(inp, rtp=rtp)
        self.assertClean(changes, validation)
        self.assertEqual([d.duration_min for d in plan.days[:3]], [30, 45, 60])

    def test_time_budget_today_is_respected(self):
        today = date(2026, 10, 6)
        inp = _inputs(today=today, time_available_today=45, restricted_dates={today.isoformat()})
        result, plan, changes, validation = _run(inp, time_text="45m")
        self.assertClean(changes, validation)
        self.assertLessEqual(sum(d.duration_min for d in plan.days if d.date == today.isoformat()), 45)

    def test_injury_avoids_sport_and_adds_rehab(self):
        note = "ont i knät"
        restrictions = injury_restrictions(note)
        inp = _inputs(today=date(2026, 10, 5), primary_sport="Run",
                      avoid_sports=restrictions["avoid_sports"], injury=restrictions, injury_note=note)
        result, plan, changes, validation = _run(inp, injury_note=note)
        self.assertClean(changes, validation)
        self.assertNotIn("Run", {d.intervals_type for d in plan.days})
        self.assertTrue(any(d.title.startswith("Injury rehab") for d in plan.days))

    def test_runner_without_bike_gets_generic_run_sessions(self):
        catalog = [s for s in ALL_SPORTS_CATALOG if s["intervals_type"] in ("Run", "WeightTraining")]
        inp = _inputs(today=date(2026, 10, 5), primary_sport="Run", available_sports=catalog,
                      sport_budgets={"Run": {"remaining": 400}})
        result, plan, changes, validation = _run(inp)
        self.assertClean(changes, validation)
        self.assertEqual({d.intervals_type for d in plan.days} - {"Rest", "WeightTraining"}, {"Run"})
        self.assertTrue(any("Threshold intervals" in d.title for d in plan.days))

    def test_calendar_constraint_is_respected(self):
        today = date(2026, 10, 5)
        constraints = [{"date": "2026-10-08", "allowed_types": ["Run"], "reason": "Travel"}]
        inp = _inputs(today=today, constraints=constraints)
        result, plan, changes, validation = _run(inp, constraints=constraints)
        self.assertClean(changes, validation)
        self.assertTrue({d.intervals_type for d in plan.days if d.date == "2026-10-08"} <= {"Run", "Rest"})

    def test_illness_turns_everything_into_rest(self):
        result, plan, changes, validation = _run(_inputs(), today_wellness={"sick": True})
        self.assertEqual(validation.hard_failures, [])
        self.assertEqual({d.intervals_type for d in plan.days}, {"Rest"})

    def test_every_session_offers_itself_as_first_option(self):
        result = build_deterministic_plan(_inputs())
        for day in result.plan.days:
            self.assertIs(result.options[session_key(day)][0], day)

    def test_missed_sessions_are_not_caught_up(self):
        # Friday with nothing done this week: the remaining days get at most a pro-rata share.
        result = build_deterministic_plan(_inputs())
        week = result.week_targets[0]
        rest_of_week = sum(tss_of(d) for d in result.plan.days if week.contains(d.date))
        self.assertLessEqual(rest_of_week, week.tss_target * 3 / 7 * 1.15 + 1)

    def test_many_situations_always_validate(self):
        for offset, wib, ctl, restricted in itertools.product(range(7), (1, 3, 4), (25, 55, 90), (False, True)):
            today = date(2026, 10, 5) + timedelta(days=offset)
            r_dates = {today.isoformat(), (today + timedelta(days=1)).isoformat()} if restricted else set()
            inp = _inputs(today=today, week_in_block=wib, ctl=ctl, restricted_dates=r_dates)
            with self.subTest(today=today.isoformat(), week_in_block=wib, ctl=ctl, restricted=restricted):
                result, plan, changes, validation = _run(inp, hrv_state="LOW" if restricted else "NORMAL")
                self.assertClean(changes, validation)

    def test_mixed_situations_always_validate(self):
        runner = [s for s in ALL_SPORTS_CATALOG if s["intervals_type"] in ("Run", "WeightTraining")]
        rainy = [{"date": d, "rain_afternoon_mm": 12, "rain_morning_mm": 8, "temp_afternoon": 1, "temp_morning": -2}
                 for d in _horizon(date(2026, 10, 5), 16)]
        cases = itertools.product(
            range(0, 7, 2),                      # weekday of "today"
            ("Ride", "Run"),                     # primary sport
            (None, "knee pain", "ont i vaden"),  # injury note
            (None, 30, 75),                      # minutes available today
            (False, True),                       # a locked manual session on day 2
            (False, True),                       # rainy and cold all week
        )
        for offset, primary, note, minutes, locked, rain in cases:
            today = date(2026, 10, 5) + timedelta(days=offset)
            restrictions = injury_restrictions(note) if note else None
            locked_dates = {(today + timedelta(days=2)).isoformat()} if locked else set()
            inp = _inputs(
                today=today, primary_sport=primary,
                available_sports=runner if primary == "Run" and rain else None,
                avoid_sports=restrictions["avoid_sports"] if restrictions else set(),
                injury=restrictions, injury_note=note or "",
                time_available_today=minutes,
                restricted_dates={today.isoformat()} if minutes is not None and minutes < 60 else set(),
                locked_dates=locked_dates, base_tss_by_date={d: 70 for d in locked_dates},
                weather=rainy if rain else [],
            )
            with self.subTest(today=today.isoformat(), primary=primary, injury=note, minutes=minutes,
                              locked=locked, rain=rain):
                result, plan, changes, validation = _run(
                    inp, injury_note=note or "", time_text=f"{minutes}m" if minutes is not None else "")
                self.assertClean(changes, validation)

    def test_strength_sessions_keep_their_gap_across_the_week_boundary(self):
        # Found on a Saturday: a weekend strength session in a deload week and a Monday one
        # (sick Tuesday-Wednesday) were one day apart, and validation blocked the whole plan.
        sick = {"2026-10-13", "2026-10-14"}
        for today in (date(2026, 10, 10), date(2026, 10, 11)):
            for wib in (1, 2, 3, 4):
                inp = _inputs(today=today, week_in_block=wib, unavailable_dates=sick)
                with self.subTest(today=today.isoformat(), week_in_block=wib):
                    result, plan, changes, validation = _run(inp)
                    self.assertClean(changes, validation)
                    strength = sorted(date.fromisoformat(d.date) for d in plan.days
                                      if d.intervals_type == "WeightTraining")
                    self.assertTrue(all((b - a).days >= 2 for a, b in zip(strength, strength[1:])), strength)


if __name__ == "__main__":
    unittest.main()

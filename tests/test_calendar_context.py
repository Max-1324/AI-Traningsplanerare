"""intervals.icu calendar context: annual training plan targets, availability events, target sync."""
import unittest
from datetime import date, timedelta
from unittest import mock

from training_plan.engine.calendar_context import atp_weeks, availability_by_date, tss_per_hour
from training_plan.engine.periodization import apply_calendar_targets, build_week_targets
from training_plan.engine.planner import PlannerInputs, build_deterministic_plan, tss_of
from training_plan.engine.postprocess import apply_safety_rules
from training_plan.engine.validation import validate_postprocessed_plan

MONDAY = date(2026, 10, 5)


def _target(day, load=None, seconds=None, own=False, name="Week"):
    return {"id": f"t{day}", "category": "TARGET", "start_date_local": f"{day}T00:00:00",
            "end_date_local": f"{date.fromisoformat(day) + timedelta(days=1)}T00:00:00",
            "name": name, "load_target": load, "time_target": seconds,
            "description": "ai-generated" if own else ""}


ATP_EVENTS = [
    {"category": "PLAN", "name": "Season 2027", "tags": ["Build"],
     "start_date_local": "2026-09-14T00:00:00", "end_date_local": "2026-11-01T00:00:00"},
    _target("2026-10-05", load=520),
    _target("2026-10-12", seconds=9 * 3600),
    _target("2026-10-19", load=330),
    {"category": "NOTE", "plan_applied": "atp", "name": "Recovery week",
     "start_date_local": "2026-10-19T00:00:00", "end_date_local": "2026-10-26T00:00:00"},
    _target("2026-10-26", load=999, own=True),           # our own synced target: ignored
    {"category": "TARGET", "name": "FTP 300 W", "start_date_local": "2026-12-01T00:00:00"},  # a goal
]


class TestAnnualTrainingPlan(unittest.TestCase):
    def test_reads_targets_phase_and_recovery_weeks(self):
        weeks = atp_weeks(ATP_EVENTS)
        self.assertEqual(sorted(weeks), ["2026-10-05", "2026-10-12", "2026-10-19"])
        self.assertEqual(weeks["2026-10-05"]["total"]["load"], 520)
        self.assertEqual(weeks["2026-10-05"]["phase"], "Build")
        self.assertFalse(weeks["2026-10-05"]["recovery"])
        self.assertTrue(weeks["2026-10-19"]["recovery"])

    def test_per_sport_targets_are_summed(self):
        from training_plan.engine.calendar_context import week_tss
        events = [dict(_target("2026-10-05", load=300), type="Ride"),
                  dict(_target("2026-10-05", seconds=3 * 3600), type="Run")]
        week = atp_weeks(events)["2026-10-05"]
        self.assertEqual(week_tss(week, tss_per_hour=50), 450)
        self.assertEqual(sorted(week["sports"]), ["Ride", "Run"])

    def test_all_activities_target_wins_over_the_sum(self):
        from training_plan.engine.calendar_context import week_tss
        events = [dict(_target("2026-10-05", load=300), type="Ride"),
                  dict(_target("2026-10-05", load=150), type="Run"),
                  _target("2026-10-05", load=500)]
        self.assertEqual(week_tss(atp_weeks(events)["2026-10-05"], tss_per_hour=50), 500)

    def test_atp_targets_replace_our_own_week_targets(self):
        targets = build_week_targets(55, {"week_in_block": 3}, MONDAY, target_ctl=100)
        applied = apply_calendar_targets(targets, atp_weeks(ATP_EVENTS), tss_per_hour=60)
        self.assertEqual([t.tss_target for t in applied[:3]], [520, 540, 330])
        self.assertEqual([t.kind for t in applied[:3]], ["build", "build", "deload"])
        self.assertEqual(applied[2].max_key_sessions, 0)
        self.assertEqual(applied[0].source, "intervals.icu")
        self.assertEqual(applied[3].source, "planner", "weeks without an ATP target keep our own target")

    def test_atp_target_is_capped_by_the_ramp_limit(self):
        # Low fitness (CTL 19) and an ambitious plan: the first week is phased in.
        targets = build_week_targets(19, {"week_in_block": 1}, MONDAY, target_ctl=100)
        applied = apply_calendar_targets(targets, atp_weeks([_target("2026-10-05", load=600)]), tss_per_hour=60)
        self.assertLessEqual(applied[0].tss_target, round((19 + 6 * 6) * 7))
        self.assertIn("capped from 600", applied[0].note)

    def test_recovery_week_is_recognised_from_the_drop_in_load(self):
        events = [_target("2026-10-05", load=400), _target("2026-10-12", load=420), _target("2026-10-19", load=300)]
        targets = build_week_targets(60, {"week_in_block": 1}, MONDAY, target_ctl=100)
        applied = apply_calendar_targets(targets, atp_weeks(events), tss_per_hour=60)
        self.assertEqual([t.kind for t in applied[:3]], ["build", "build", "deload"])

    def test_race_weeks_keep_their_race_handling(self):
        races = [{"name": "Testloppet", "category": "RACE_A", "start_date_local": "2026-10-10T08:00:00"}]
        targets = build_week_targets(55, {"week_in_block": 2}, MONDAY, races=races, target_ctl=100)
        applied = apply_calendar_targets(targets, atp_weeks(ATP_EVENTS), tss_per_hour=60)
        self.assertEqual(applied[0].kind, "race")
        self.assertEqual(applied[0].tss_target, 520)

    def test_tss_per_hour_from_history(self):
        acts = [{"icu_training_load": 60, "moving_time": 3600}] * 10
        self.assertEqual(tss_per_hour(acts), 60)
        self.assertEqual(tss_per_hour([]), 55.0)


class TestAvailability(unittest.TestCase):
    DATES = [(MONDAY + timedelta(days=i)).isoformat() for i in range(10)]

    def test_defaults_and_explicit_availability(self):
        events = [
            {"category": "SICK", "name": "Förkyld", "start_date_local": "2026-10-05T00:00:00",
             "end_date_local": "2026-10-07T00:00:00"},                                  # Mon–Tue
            {"category": "INJURED", "name": "Vad", "start_date_local": "2026-10-08T00:00:00"},  # Thu only
            {"category": "HOLIDAY", "name": "Resa", "start_date_local": "2026-10-10T00:00:00",
             "end_date_local": "2026-10-12T00:00:00", "training_availability": "LIMITED"},
            {"category": "HOLIDAY", "name": "Helg", "start_date_local": "2026-10-13T00:00:00"},  # NORMAL
        ]
        result = availability_by_date(events, self.DATES)
        self.assertEqual({d: v[0] for d, v in result.items()}, {
            "2026-10-05": "UNAVAILABLE", "2026-10-06": "UNAVAILABLE",
            "2026-10-08": "LIMITED", "2026-10-10": "LIMITED", "2026-10-11": "LIMITED",
        })

    def test_unavailable_wins_over_limited(self):
        events = [
            {"category": "HOLIDAY", "start_date_local": "2026-10-05T00:00:00", "training_availability": "LIMITED"},
            {"category": "SICK", "start_date_local": "2026-10-05T00:00:00"},
        ]
        self.assertEqual(availability_by_date(events, self.DATES)["2026-10-05"][0], "UNAVAILABLE")

    def test_planner_leaves_unavailable_days_empty_and_scales_the_week(self):
        targets = build_week_targets(55, {"week_in_block": 2}, MONDAY, target_ctl=100)
        dates = self.DATES
        unavailable = {"2026-10-05", "2026-10-06", "2026-10-07"}
        inputs = PlannerInputs(today=MONDAY, horizon_dates=dates, week_targets=targets, primary_sport="Ride",
                               unavailable_dates=unavailable)
        result = build_deterministic_plan(inputs)
        self.assertFalse(unavailable & {d.date for d in result.plan.days})
        week_tss = sum(tss_of(d) for d in result.plan.days if targets[0].contains(d.date))
        self.assertLessEqual(week_tss, targets[0].tss_target * 4 / 7 * 1.15 + 1)
        plan, changes = apply_safety_rules(result.plan, hrv={"state": "NORMAL"}, budgets={}, locked=unavailable,
                                           athlete={"id": 1}, today=MONDAY)
        validation = validate_postprocessed_plan(plan, athlete={"id": 1}, tss_budget=result.horizon_tss_target,
                                                 review_context={"today": MONDAY.isoformat(), "locked_dates": unavailable,
                                                                 "max_hard_days": result.max_hard_days},
                                                 postprocess_changes=changes)
        self.assertEqual(validation.hard_failures, [])


class TestPlannerInputsFromCalendar(unittest.TestCase):
    def test_calendar_events_reach_the_planner(self):
        from training_plan.app.deterministic import build_planner_inputs
        events = ATP_EVENTS + [
            {"category": "SICK", "name": "Feber", "start_date_local": "2026-10-07T00:00:00"},
            {"category": "INJURED", "name": "Knä", "start_date_local": "2026-10-08T00:00:00",
             "training_availability": "LIMITED"},
        ]
        inputs = build_planner_inputs(
            today=MONDAY, horizon=9, ctl=55, tsb=0, mesocycle={"week_in_block": 2}, phase={"phase": "Build"},
            trajectory={}, races=[], race_week={}, rtp_status={}, state={}, dominant_sport="Ride", weather=[],
            constraints=[], locked_dates=set(), base_tss_by_date={}, budgets={}, sport_acwr={},
            hrv={"state": "NORMAL"}, readiness={"score": 70}, wellness=[], activities=[], morning={},
            injury_profile=None, development_needs={}, ftp_check={}, motivation={}, calendar_events=events,
        )
        self.assertEqual(inputs.week_targets[0].tss_target, 520)
        self.assertIn("2026-10-07", inputs.unavailable_dates)
        self.assertIn("2026-10-08", inputs.restricted_dates)
        self.assertIn("sick: Feber", inputs.restriction_reason)


class TestWeekTargetSync(unittest.TestCase):
    def test_writes_only_weeks_without_athlete_targets_and_updates_own(self):
        import training_plan.integrations.intervals_events as events_module
        targets = build_week_targets(55, {"week_in_block": 2}, MONDAY, target_ctl=100)
        calendar = [
            _target("2026-10-05", load=500),                 # athlete target: leave alone
            _target("2026-10-12", load=1, own=True),         # ours, outdated: update
        ]
        with mock.patch.object(events_module.requests, "post") as post, \
             mock.patch.object(events_module.requests, "put") as put:
            written = events_module.sync_week_targets(targets, calendar)
        self.assertEqual(written, 3)
        self.assertEqual(put.call_count, 1)
        self.assertEqual(put.call_args.kwargs["json"]["load_target"], targets[1].tss_target)
        created_weeks = [c.kwargs["json"]["start_date_local"][:10] for c in post.call_args_list]
        self.assertEqual(created_weeks, [targets[2].week_start, targets[3].week_start])
        body = post.call_args_list[0].kwargs["json"]
        self.assertEqual(body["category"], "TARGET")
        self.assertIn("ai-generated", body["description"])

    def test_sync_errors_never_break_the_run(self):
        import training_plan.integrations.intervals_events as events_module
        targets = build_week_targets(55, {"week_in_block": 2}, MONDAY, target_ctl=100)
        with mock.patch.object(events_module.requests, "post", side_effect=RuntimeError("422")):
            self.assertEqual(events_module.sync_week_targets(targets, []), 0)


if __name__ == "__main__":
    unittest.main()

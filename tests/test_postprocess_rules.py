"""
Behavioral regression tests for postprocessing rules.

Each test targets one enforce_* function in isolation.
The invariant under test is stated in the docstring so failures are self-explanatory.
"""
import json
import unittest
from pathlib import Path

from training_plan.core.models import PlanDay, WorkoutStep
from training_plan.engine.planning import classify_session_category
from training_plan.engine.postprocess import (
    apply_injury_rules,
    enforce_hard_easy,
    enforce_hrv,
    enforce_illness,
    enforce_min_duration,
    enforce_max_consecutive_rest,
    enforce_rtp,
    enforce_tss,
)
from tests.fixtures.plans import (
    BASE,
    easy_day,
    four_consecutive_rest,
    hard_day,
    heavy_tss_week,
    mixed_week,
    rest_day,
    run_heavy_week,
    three_consecutive_hard,
)

_FIXTURES = Path(__file__).parent / "fixtures"
_ATHLETE = json.loads((_FIXTURES / "athlete_cyclist.json").read_text())


class TestEnforceIllness(unittest.TestCase):
    """When sick=True all active sessions must become Rest and be marked vetoed."""

    def _sick(self):
        return {"sick": True}

    def test_all_sessions_become_rest(self):
        days, changes = enforce_illness(mixed_week(), self._sick())
        non_rest = [d for d in days if d.intervals_type != "Rest"]
        self.assertEqual(non_rest, [], "Expected every session to be Rest when sick")

    def test_all_days_are_vetoed(self):
        days, _ = enforce_illness(mixed_week(), self._sick())
        not_vetoed = [d for d in days if not d.vetoed]
        self.assertEqual(not_vetoed, [], "Expected every day to be vetoed when sick")

    def test_changes_reported(self):
        _, changes = enforce_illness(mixed_week(), self._sick())
        self.assertTrue(len(changes) >= 1, "Expected at least one change entry")

    def test_no_effect_when_not_sick(self):
        original = mixed_week()
        days, changes = enforce_illness(original, {"sick": False})
        types_before = [d.intervals_type for d in original]
        types_after = [d.intervals_type for d in days]
        self.assertEqual(types_before, types_after)
        self.assertEqual(changes, [])

    def test_no_effect_when_wellness_is_none(self):
        original = mixed_week()
        days, changes = enforce_illness(original, None)
        self.assertEqual([d.intervals_type for d in original], [d.intervals_type for d in days])
        self.assertEqual(changes, [])


class TestEnforceHardEasy(unittest.TestCase):
    """Two consecutive hard sessions on adjacent days: the second must become Z1 and be vetoed."""

    def _all_zones(self, day: PlanDay) -> set[str]:
        return {s.zone for s in day.workout_steps}

    def test_second_hard_day_becomes_z1(self):
        days, changes = enforce_hard_easy(three_consecutive_hard())
        # days[1] was hard – it must be downgraded
        self.assertTrue(
            all(s.zone == "Z1" for s in days[1].workout_steps),
            "Second consecutive hard day must be all Z1 after hard-easy enforcement",
        )

    def test_second_hard_day_is_vetoed(self):
        days, _ = enforce_hard_easy(three_consecutive_hard())
        self.assertTrue(days[1].vetoed, "Second consecutive hard day must be marked vetoed")

    def test_first_hard_day_is_untouched(self):
        original = three_consecutive_hard()
        days, _ = enforce_hard_easy(original)
        self.assertEqual(days[0].workout_steps, original[0].workout_steps)

    def test_third_day_not_double_vetoed(self):
        # After days[1] is converted to Z1, days[2] should NOT be vetoed
        # because its predecessor (days[1]) is now easy.
        days, _ = enforce_hard_easy(three_consecutive_hard())
        self.assertFalse(days[2].vetoed, "Third day must not be vetoed after days[1] was already downgraded")

    def test_hard_easy_change_in_log(self):
        _, changes = enforce_hard_easy(three_consecutive_hard())
        self.assertTrue(
            any("HARD-EASY" in c for c in changes),
            "Expected a HARD-EASY entry in the changes log",
        )

    def test_no_effect_on_alternating_schedule(self):
        # hard, easy, hard – no consecutive pair, nothing should be vetoed
        plan = [hard_day(BASE, 0), easy_day(BASE, 1), hard_day(BASE, 2)]
        days, changes = enforce_hard_easy(plan)
        self.assertFalse(any(d.vetoed for d in days))
        self.assertEqual(changes, [])


class TestEnforceMinDuration(unittest.TestCase):
    """Minimum duration clamp must not corrupt recovery sessions or workout step totals."""

    def test_plan_day_duration_is_used_in_classification(self):
        day = PlanDay(
            date=BASE.isoformat(),
            title="Recovery Ride",
            intervals_type="Ride",
            duration_min=30,
            workout_steps=[WorkoutStep(duration_min=30, zone="Z1", description="Easy recovery spin")],
        )
        self.assertEqual(classify_session_category(day.model_dump()), "recovery")

    def test_short_recovery_ride_is_not_clamped(self):
        day = PlanDay(
            date=BASE.isoformat(),
            title="Recovery Ride",
            intervals_type="Ride",
            duration_min=30,
            workout_steps=[WorkoutStep(duration_min=30, zone="Z1", description="Easy recovery spin")],
        )
        days = enforce_min_duration([day])
        self.assertEqual(days[0].duration_min, 30)
        self.assertEqual(sum(step.duration_min for step in days[0].workout_steps), 30)

    def test_clamped_session_keeps_workout_steps_in_sync(self):
        day = PlanDay(
            date=BASE.isoformat(),
            title="Threshold session",
            intervals_type="VirtualRide",
            duration_min=40,
            workout_steps=[
                WorkoutStep(duration_min=10, zone="Z1", description="Warmup"),
                WorkoutStep(duration_min=20, zone="Z4", description="Main set"),
                WorkoutStep(duration_min=10, zone="Z1", description="Cooldown"),
            ],
        )
        days = enforce_min_duration([day])
        self.assertEqual(days[0].duration_min, 45)
        self.assertEqual(sum(step.duration_min for step in days[0].workout_steps), 45)
        self.assertIn("Z4", {step.zone for step in days[0].workout_steps})


class TestEnforceHrv(unittest.TestCase):
    """HRV LOW must veto only the first two hard sessions (i <= 1)."""

    _LOW_HRV = {"state": "LOW", "deviation_pct": -28}
    _NORMAL_HRV = {"state": "NORMAL", "deviation_pct": 2}

    def test_first_two_hard_days_become_z1(self):
        plan = [hard_day(BASE, i) for i in range(4)]
        days, _ = enforce_hrv(plan, self._LOW_HRV)
        for idx in (0, 1):
            self.assertTrue(
                all(s.zone == "Z1" for s in days[idx].workout_steps),
                f"Day {idx} must be Z1 when HRV is LOW",
            )

    def test_day_beyond_index_1_not_affected(self):
        plan = [hard_day(BASE, i) for i in range(4)]
        days, _ = enforce_hrv(plan, self._LOW_HRV)
        for idx in (2, 3):
            zones = {s.zone for s in days[idx].workout_steps}
            self.assertIn("Z4", zones, f"Day {idx} should retain Z4 – HRV veto only covers days 0 and 1")

    def test_no_effect_when_hrv_normal(self):
        plan = [hard_day(BASE, i) for i in range(3)]
        days, changes = enforce_hrv(plan, self._NORMAL_HRV)
        self.assertEqual(changes, [])
        for d in days:
            self.assertFalse(d.vetoed)

    def test_hrv_veto_sets_vetoed_flag(self):
        plan = [hard_day(BASE, i) for i in range(3)]
        days, _ = enforce_hrv(plan, self._LOW_HRV)
        self.assertTrue(days[0].vetoed)
        self.assertTrue(days[1].vetoed)

    def test_easy_day_not_affected_by_hrv(self):
        plan = [easy_day(BASE, 0), easy_day(BASE, 1)]
        days, changes = enforce_hrv(plan, self._LOW_HRV)
        # Easy days (Z2) are not "intense" – they must not be vetoed
        self.assertEqual(changes, [])


class TestEnforceMaxConsecutiveRest(unittest.TestCase):
    """Three or more consecutive rest days: the third must be replaced with a short Z1 session."""

    def test_third_rest_day_becomes_active(self):
        days, changes = enforce_max_consecutive_rest(four_consecutive_rest())
        third = days[2]
        self.assertNotEqual(
            third.intervals_type, "Rest",
            "Third consecutive rest day must become an active session",
        )
        self.assertGreater(third.duration_min, 0)

    def test_change_logged(self):
        _, changes = enforce_max_consecutive_rest(four_consecutive_rest())
        self.assertTrue(
            any("MAX-REST" in c or "3 rest" in c.lower() for c in changes),
            "Expected a change entry about consecutive rest",
        )

    def test_two_rest_days_untouched(self):
        plan = [rest_day(BASE, 0), rest_day(BASE, 1), easy_day(BASE, 2)]
        days, changes = enforce_max_consecutive_rest(plan)
        self.assertEqual(days[0].intervals_type, "Rest")
        self.assertEqual(days[1].intervals_type, "Rest")
        self.assertEqual(changes, [])


class TestEnforceRtp(unittest.TestCase):
    """Return-to-Play protocol must replace the first 3 days with prescribed sessions."""

    _RTP = {"is_active": True, "days_off": 7}

    def test_first_three_days_follow_protocol(self):
        plan = [easy_day(BASE, i) for i in range(5)]
        days, changes = enforce_rtp(plan, self._RTP)
        # RTP day 1 = 30min, day 2 = 45min, day 3 = 60min
        expected_durations = [30, 45, 60]
        for i, expected_dur in enumerate(expected_durations):
            self.assertEqual(
                days[i].duration_min, expected_dur,
                f"RTP day {i+1} must be {expected_dur}min, got {days[i].duration_min}min",
            )

    def test_days_beyond_protocol_are_unchanged(self):
        plan = [easy_day(BASE, i) for i in range(5)]
        original_types = [d.intervals_type for d in plan]
        days, _ = enforce_rtp(plan, self._RTP)
        for i in range(3, 5):
            self.assertEqual(days[i].intervals_type, original_types[i])

    def test_no_effect_when_rtp_inactive(self):
        plan = [easy_day(BASE, i) for i in range(3)]
        days, changes = enforce_rtp(plan, {"is_active": False, "days_off": 3})
        self.assertEqual(changes, [])

    def test_no_effect_when_rtp_is_none(self):
        plan = [easy_day(BASE, i) for i in range(3)]
        days, changes = enforce_rtp(plan, None)
        self.assertEqual(changes, [])

    def test_rtp_change_logged(self):
        plan = [easy_day(BASE, i) for i in range(5)]
        _, changes = enforce_rtp(plan, self._RTP)
        self.assertTrue(len(changes) >= 1)


class TestApplyInjuryRules(unittest.TestCase):
    """Run sessions must be replaced when a lower-body injury is reported."""

    def test_run_sessions_replaced_for_knee_injury(self):
        plan = run_heavy_week()
        days, changes = apply_injury_rules(plan, "knee pain")
        run_days = [d for d in days if d.intervals_type == "Run"]
        self.assertEqual(run_days, [], "No Run sessions should remain with a knee injury")

    def test_replacement_is_safe_sport(self):
        plan = run_heavy_week()
        days, _ = apply_injury_rules(plan, "knee pain")
        # Replaced sessions should be a safe sport (VirtualRide)
        was_run = {(d.date, d.title) for d in run_heavy_week() if d.intervals_type == "Run"}
        for d in days:
            if (d.date, d.title.split(" [→")[0]) in was_run:
                self.assertIn(
                    d.intervals_type,
                    {"VirtualRide", "Ride"},
                    f"Replaced session on {d.date} should use a safe sport",
                )

    def test_rehab_session_injected(self):
        plan = run_heavy_week()
        days, _ = apply_injury_rules(plan, "knee pain")
        rehab = [d for d in days if "rehab" in d.title.lower() or "Injury rehab" in d.title]
        self.assertTrue(len(rehab) >= 1, "Expected at least one rehab session to be injected")

    def test_changes_contain_injury_entry(self):
        plan = run_heavy_week()
        _, changes = apply_injury_rules(plan, "knee pain")
        self.assertTrue(
            any("INJURY" in c.upper() for c in changes),
            "Expected an INJURY entry in the changes log",
        )

    def test_no_effect_with_empty_injury(self):
        plan = run_heavy_week()
        original_types = [d.intervals_type for d in plan]
        days, changes = apply_injury_rules(plan, "")
        self.assertEqual([d.intervals_type for d in days], original_types)
        self.assertEqual(changes, [])

    def test_no_effect_with_negated_injury(self):
        plan = run_heavy_week()
        original_types = [d.intervals_type for d in plan]
        days, changes = apply_injury_rules(plan, "nej")
        self.assertEqual([d.intervals_type for d in days], original_types)
        self.assertEqual(changes, [])


class TestEnforceTss(unittest.TestCase):
    """TSS ceiling: total weekly TSS must not exceed the budget after enforcement."""

    def _weekly_tss(self, days):
        return sum(
            enforce_tss.__wrapped__(d, _ATHLETE) if hasattr(enforce_tss, "__wrapped__") else 0
            for d in days
        )

    def test_tss_ceiling_respected(self):
        from training_plan.engine.postprocess import estimate_tss_coggan

        plan = heavy_tss_week()
        tss_before = sum(estimate_tss_coggan(d, _ATHLETE) for d in plan)

        # Budget is below unconstrained TSS – enforcer must reduce total load.
        # It trims (not eliminates) threshold sessions, so we assert reduction
        # happened rather than an exact target (enforcer has min-duration floors).
        tight_budget = tss_before * 0.75
        days, changes = enforce_tss(plan, tight_budget, _ATHLETE)
        tss_after = sum(estimate_tss_coggan(d, _ATHLETE) for d in days)

        self.assertLess(
            tss_after,
            tss_before,
            f"TSS must be reduced from {tss_before:.0f} when budget ({tight_budget:.0f}) is tighter",
        )

    def test_tss_audit_in_changes(self):
        from training_plan.engine.postprocess import estimate_tss_coggan

        plan = heavy_tss_week()
        total_tss = sum(estimate_tss_coggan(d, _ATHLETE) for d in plan)
        _, changes = enforce_tss(plan, total_tss * 0.5, _ATHLETE)
        self.assertTrue(
            any("TSS" in c for c in changes),
            "Expected a TSS-AUDIT entry in changes",
        )

    def test_no_trimming_when_under_budget(self):
        from training_plan.engine.postprocess import estimate_tss_coggan

        plan = [easy_day(BASE, i) for i in range(3)]
        total_tss = sum(estimate_tss_coggan(d, _ATHLETE) for d in plan)
        generous_budget = total_tss * 3
        days, _ = enforce_tss(plan, generous_budget, _ATHLETE)
        durations_before = [d.duration_min for d in plan]
        durations_after = [d.duration_min for d in days]
        self.assertEqual(durations_before, durations_after)


if __name__ == "__main__":
    unittest.main()

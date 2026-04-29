import unittest
from datetime import date, timedelta

from training_plan.core.models import AIPlan, PlanDay, PlanReview, ReviewDimension, WorkoutStep
from training_plan.engine.analysis import calculate_readiness_score, development_needs_analysis, format_athlete_profile
from training_plan.engine.pipeline import compute_scores_from_review
from training_plan.engine.planning import coach_confidence_analysis


class TestCoachingLogic(unittest.TestCase):
    def test_readiness_uses_chronological_wellness_for_latest_sleep(self):
        wellness = [
            {"id": "2026-04-02", "sleepSecs": 5 * 3600, "restingHR": 52},
            {"id": "2026-04-01", "sleepSecs": 8 * 3600, "restingHR": 54},
            {"id": "2026-04-03", "sleepSecs": 8 * 3600, "restingHR": 51},
        ]
        activities = [
            {"start_date_local": "2026-04-02T10:00:00", "perceived_exertion": 7, "feel": 3},
            {"start_date_local": "2026-04-01T10:00:00", "perceived_exertion": 5, "feel": 2},
        ]

        result = calculate_readiness_score(
            {"deviation_pct": 0},
            wellness,
            activities,
        )

        self.assertEqual(result["raw_inputs"]["sleep_hours"], 8.0)

    def test_medium_coach_confidence_is_not_labeled_low(self):
        result = coach_confidence_analysis(
            data_quality={"warnings": []},
            activities=[{}] * 10,
            wellness=[{}] * 7,
            fitness=[{}] * 14,
            hrv={"state": "INSUFFICIENT_DATA"},
        )

        self.assertEqual(result["score"], 90)
        self.assertEqual(result["level"], "HIGH")

        medium = coach_confidence_analysis(
            data_quality={"warnings": ["a", "b"]},
            activities=[{}] * 9,
            wellness=[{}] * 7,
            fitness=[{}] * 14,
            hrv={"state": "NORMAL"},
        )
        self.assertEqual(medium["score"], 70)
        self.assertEqual(medium["level"], "MEDIUM")

    def test_race_gap_text_drives_development_needs(self):
        result = development_needs_analysis(
            phase={"phase": "Base"},
            readiness={"score": 70},
            motivation={"state": "NEUTRAL"},
            compliance={"weighted_completion_rate": 100, "key_completion_rate": 100},
            ftp_check={"needs_test": False},
            np_if_analysis={"flags": []},
            session_quality={"category_scores": {"threshold": {"count": 2, "avg_score": 75}}},
            race_demands={"gaps": ["Durability gap: longest ride is under 4h.", "Fueling gap: too few long nutrition repetitions."]},
            polarization={"mid_pct": 10},
        )

        self.assertIn("durability", [item["area"] for item in result["priorities"]])
        self.assertIn("1 long Z2 session", result["must_hit_sessions"])

    def test_athlete_profile_surfaces_weight_and_ftp_per_kg(self):
        athlete = {
            "dob": "1990-06-01",
            "weight": 75,
            "sportSettings": [{"types": ["Ride", "VirtualRide"], "ftp": 300, "max_hr": 190}],
        }

        text = format_athlete_profile(athlete, [])

        self.assertIn("Weight 75.0kg", text)
        self.assertIn("Cycling FTP 4.0W/kg", text)

    def _pass_review(self):
        strong = ReviewDimension(rating="STRONG")
        return PlanReview(
            summary="good",
            goal_alignment=strong,
            key_sessions=strong,
            efficiency=strong,
            load_and_risk=strong,
            individualization=strong,
            race_demands=strong,
            overall_verdict="PASS",
        )

    def _threshold_plan(self, offset_days: int):
        target = date.today() + timedelta(days=offset_days)
        return AIPlan(
            stress_audit="ok",
            summary="threshold",
            days=[
                PlanDay(
                    date=target.isoformat(),
                    title="Threshold session",
                    intervals_type="Ride",
                    duration_min=70,
                    workout_steps=[
                        WorkoutStep(duration_min=15, zone="Z2", description="Warmup"),
                        WorkoutStep(duration_min=40, zone="Z4", description="Main"),
                        WorkoutStep(duration_min=15, zone="Z1", description="Cooldown"),
                    ],
                )
            ],
        )

    def test_scoring_penalizes_acute_fatigue_only_for_near_term_hard_work(self):
        context = {
            "today": date.today().isoformat(),
            "readiness": {
                "score": 48,
                "raw_inputs": {
                    "sleep_hours": 5.5,
                    "hrv_deviation_pct": -10,
                    "avg_rpe_last5": 7.8,
                },
            },
        }

        baseline = compute_scores_from_review(self._pass_review(), plan=self._threshold_plan(3), review_context={})
        fatigued_today = compute_scores_from_review(
            self._pass_review(),
            plan=self._threshold_plan(0),
            review_context=context,
        )

        self.assertGreater(fatigued_today.risk, baseline.risk)
        self.assertIn("acute fatigue", fatigued_today.rationale)

    def test_scoring_preserves_future_key_sessions_when_fitness_trend_supports_progression(self):
        context = {
            "today": date.today().isoformat(),
            "readiness": {
                "score": 52,
                "raw_inputs": {"sleep_hours": 5.8, "hrv_deviation_pct": -9},
            },
            "trajectory": {"required_weekly_tss": 450},
        }

        scores = compute_scores_from_review(
            self._pass_review(),
            plan=self._threshold_plan(3),
            review_context=context,
        )

        self.assertLessEqual(scores.risk, 4)
        self.assertGreaterEqual(scores.effectiveness, 9)
        self.assertIn("outside the 48h fatigue window", scores.rationale)


if __name__ == "__main__":
    unittest.main()

import unittest

from training_plan.engine.ai import (
    _build_double_session_rules,
    _build_json_schema,
    _build_planner_insights_section,
    _build_yesterday_feedback_section,
)


class PromptBuilderTests(unittest.TestCase):
    def test_json_schema_makes_output_contract_explicit(self):
        text = _build_json_schema(320, "2026-05-14", {"Rest", "Ride", "Run"})

        self.assertIn("Return ONLY valid JSON", text)
        self.assertIn("No markdown", text)
        self.assertIn("Total=Z vs budget 320", text)
        self.assertIn("One of: Rest | Ride | Run", text)
        self.assertIn("Do NOT include locked dates (2026-05-14)", text)
        self.assertIn("workout_steps MUST be included", text)

    def test_planner_insights_groups_key_signals(self):
        text = _build_planner_insights_section(
            {
                "capacity_map": {
                    "areas": [{"name": "Durability", "score": 72, "status": "OK", "meaning": "solid"}],
                    "strongest": ["Tempo"],
                    "weakest": ["Long endurance"],
                },
                "benchmark_system": {
                    "summary": "Checkpoint due.",
                    "benchmarks": [
                        {
                            "name": "FTP check",
                            "priority": "HIGH",
                            "due_in_days": 3,
                            "session": "Ramp test",
                            "purpose": "Update zones",
                        }
                    ],
                },
                "minimum_effective_dose": {
                    "summary": "Protect key work.",
                    "must_hit_sessions": ["1 threshold session"],
                },
            }
        )

        self.assertIn("PLANNER INSIGHTS:", text)
        self.assertIn("CAPACITY MAP:", text)
        self.assertIn("Strongest: Tempo | Weakest: Long endurance", text)
        self.assertIn("FTP check (HIGH in ~3d): Ramp test", text)
        self.assertIn("Must-protect: 1 threshold session", text)

    def test_yesterday_feedback_names_exact_feedback_date(self):
        text = _build_yesterday_feedback_section("Completed planned ride.", "2026-05-13")

        self.assertIn("session on 2026-05-13", text)
        self.assertIn('Do NOT use the word "yesterday"', text)
        self.assertIn("Concrete tips for the next similar session", text)

    def test_double_session_rules_require_separate_json_entries(self):
        text = _build_double_session_rules()

        self.assertIn("same date and different slot", text)
        self.assertIn("Never combine two sports", text)
        self.assertIn("NEVER Z4+ on both sessions", text)


if __name__ == "__main__":
    unittest.main()

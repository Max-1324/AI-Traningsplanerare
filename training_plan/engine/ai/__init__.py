"""Compatibility facade for AI-related helpers.

The implementation lives in smaller modules so callers can choose the part they need,
while legacy imports from training_plan.engine.ai keep working.
"""

from training_plan.engine.prompt_builders import (
    _build_double_session_rules,
    _build_json_schema,
    _build_key_session_directive,
    _build_planner_insights_section,
    _build_sports_section,
    _build_yesterday_feedback_section,
    build_prompt,
    fmt,
    morning_questions,
    sanitize,
)
from training_plan.engine.ai.client import call_ai
from training_plan.engine.ai.parsing import parse_plan
from training_plan.engine.ai.display import format_existing_plan, plan_update_mode, print_plan

__all__ = [
    "_build_double_session_rules",
    "_build_json_schema",
    "_build_key_session_directive",
    "_build_planner_insights_section",
    "_build_sports_section",
    "_build_yesterday_feedback_section",
    "build_prompt",
    "call_ai",
    "fmt",
    "format_existing_plan",
    "morning_questions",
    "parse_plan",
    "plan_update_mode",
    "print_plan",
    "sanitize",
]

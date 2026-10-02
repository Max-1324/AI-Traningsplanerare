"""Compatibility facade for prompt and morning-check builders."""

from training_plan.engine.prompt.inputs import (
    fmt,
    morning_questions,
    sanitize,
)
from training_plan.engine.prompt.sections import (
    _build_double_session_rules,
    _build_json_schema,
    _build_key_session_directive,
    _build_planner_insights_section,
    _build_sports_section,
    _build_yesterday_feedback_section,
)
from training_plan.engine.prompt.generation import build_prompt

__all__ = [
    "_build_double_session_rules",
    "_build_json_schema",
    "_build_key_session_directive",
    "_build_planner_insights_section",
    "_build_sports_section",
    "_build_yesterday_feedback_section",
    "build_prompt",
    "fmt",
    "morning_questions",
    "sanitize",
]

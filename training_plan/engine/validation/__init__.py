"""Compatibility facade for deterministic plan validation."""

from training_plan.engine.validation.structure import repair_postprocessed_plan
from training_plan.engine.validation.rules import validate_postprocessed_plan
from training_plan.engine.validation.adapters import build_validation_review, build_validation_scores

__all__ = [
    "build_validation_review",
    "build_validation_scores",
    "repair_postprocessed_plan",
    "validate_postprocessed_plan",
]

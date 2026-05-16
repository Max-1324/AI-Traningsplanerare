"""Compatibility facade for higher-level planning insight builders."""

from training_plan.engine.insights.profiles import (
    build_capacity_map,
    build_individualization_profile,
    build_nutrition_readiness,
)
from training_plan.engine.insights.execution import (
    build_execution_friction,
    build_minimum_effective_dose,
    build_training_frequency_target,
)
from training_plan.engine.insights.forecast import (
    build_benchmark_system,
    build_block_learning,
    build_performance_forecast,
    build_race_readiness_score,
    build_season_plan,
)

__all__ = [name for name in globals() if not name.startswith("_")]

"""Compatibility facade for athlete and training analysis helpers."""

from training_plan.engine.analysis.data import (
    analyze_motivation,
    calculate_hrv,
    calculate_readiness_score,
    validate_data_quality,
)
from training_plan.engine.analysis.load import (
    acwr,
    acwr_trend_analysis,
    analyze_np_if,
    choose_target_ramp,
    ctl_ramp_from_daily_tss,
    per_sport_acwr,
    rpe_trend,
    sport_budget,
    sport_volumes,
    tsb_zone,
    tss_budget,
)
from training_plan.engine.analysis.strategy import (
    check_return_to_play,
    development_needs_analysis,
    format_race_week_for_prompt,
    race_week_protocol,
    taper_quality_score,
    training_phase,
    update_block_objective,
)
from training_plan.engine.analysis.athlete import (
    analyze_yesterday,
    athlete_profile,
    biometric_vetoes,
    compute_tss_reference,
    env_nutrition,
    format_athlete_profile,
    parse_zones,
)

__all__ = [name for name in globals() if not name.startswith("_")]

"""Compatibility facade for safety and recovery post-processing rules."""

from training_plan.engine.postprocess.injury import (
    INJURY_PROFILES,
    apply_injury_rules,
)
from training_plan.engine.postprocess.recovery import (
    HARD_THRESHOLD,
    enforce_deload,
    enforce_hard_easy,
    enforce_hrv,
    enforce_illness,
    enforce_locked,
    enforce_max_consecutive_rest,
    enforce_min_duration,
    enforce_motivation_state,
    enforce_per_sport_acwr_veto,
    enforce_rollski_limit,
    enforce_rtp,
    enforce_sport_budget,
    enforce_strength_limit,
    enforce_today_time_budget,
    ensure_warmup,
    intensity_rating,
    is_intense,
)

__all__ = [name for name in globals() if not name.startswith("_")]

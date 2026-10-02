from training_plan.engine.libraries import enforce_schedule_constraints
from training_plan.engine.postprocess.safety import (
    HARD_THRESHOLD,
    INJURY_PROFILES,
    apply_injury_rules,
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
from training_plan.engine.postprocess.load import (
    ZONE_NP_RATIO,
    _consolidate_steps,
    enforce_tss,
    estimate_tss_coggan,
    ftp_for_sport,
    repair_low_tss,
)
from training_plan.engine.postprocess.nutrition import (
    add_env_nutrition,
    calculate_nutrition_periodization,
    strip_train_low_contradiction,
)

def post_process(plan, hrv, budgets, locked, budget, activities, weather, athlete,
                 injury_note="", injury_profile=None, mesocycle=None, constraints=None, today_wellness=None,
                 rtp_status=None, per_sport_acwr_data=None, motivation=None,
                 med_active=False,
                 phase=None, races=None, wellness=None, base_tss_by_date=None, horizon_days=None,
                 time_available_text=""):
    days = plan.days
    all_c = []

    # Sjukdom och RTP är de mest kritiska, kör dem först.
    if today_wellness:
        days, c = enforce_illness(days, today_wellness); all_c += c
        if c: # If sick, no need to apply other rules
            return plan.model_copy(update={"days": days}), all_c
            
    if rtp_status:
        days, c = enforce_rtp(days, rtp_status); all_c += c
        # After RTP, other rules might still apply to later days, so we continue

    days, c = enforce_locked(days, locked);            all_c += c
    days, c = enforce_hrv(days, hrv);                 all_c += c
    if motivation:
        days, c = enforce_motivation_state(days, motivation); all_c += c
    days, c2 = apply_injury_rules(days, injury_note, injury_profile=injury_profile);  all_c += c2
    if constraints:
        days, c = enforce_schedule_constraints(days, constraints); all_c += c
    if per_sport_acwr_data:
        days, c = enforce_per_sport_acwr_veto(days, per_sport_acwr_data); all_c += c
    days, c = enforce_sport_budget(days, budgets);     all_c += c
    days, c = enforce_hard_easy(days);                 all_c += c
    days, c = enforce_strength_limit(days); all_c += c
    days, c = enforce_rollski_limit(days);  all_c += c
    if mesocycle:
        days, c = enforce_deload(days, mesocycle, athlete);  all_c += c
    days, c = repair_low_tss(
        days,
        budget,
        athlete,
        base_tss_by_date=base_tss_by_date,
        med_active=med_active,
        budgets=budgets,
    ); all_c += c
    days, c = enforce_tss(days, budget, athlete, base_tss_by_date=base_tss_by_date, horizon_days=horizon_days); all_c += c
    days     = ensure_warmup(days)
    days     = add_env_nutrition(days, weather, phase=phase, races=races, athlete=athlete, wellness=wellness)
    days, c  = strip_train_low_contradiction(days); all_c += c
    days     = enforce_min_duration(days)
    days, c = enforce_today_time_budget(days, time_available_text); all_c += c
    days     = [_consolidate_steps(d) for d in days]
    return plan.model_copy(update={"days": days}), all_c


def apply_safety_rules(plan, *, hrv, budgets, locked, athlete, weather=None, today=None,
                       injury_note="", injury_profile=None, constraints=None, today_wellness=None,
                       per_sport_acwr_data=None, phase=None, races=None, wellness=None,
                       time_available_text=""):
    """Safety net for plans from the deterministic planner.

    The planner already respects these rules, so in practice they should not change
    anything except adding warm-up and nutrition text. Rules that the planner owns
    (deload, TSS budget/repair, Return-to-Play, motivation scaling) are not run here.
    Any veto that still fires is reported, and validation then rejects the plan.
    """
    days = list(plan.days)
    all_c = []
    if today_wellness:
        days, c = enforce_illness(days, today_wellness); all_c += c
        if c:
            return plan.model_copy(update={"days": days}), all_c
    days, c = enforce_locked(days, locked);                    all_c += c
    days, c = enforce_hrv(days, hrv, today=today);             all_c += c
    days, c = apply_injury_rules(days, injury_note, injury_profile=injury_profile); all_c += c
    if constraints:
        days, c = enforce_schedule_constraints(days, constraints); all_c += c
    if per_sport_acwr_data:
        days, c = enforce_per_sport_acwr_veto(days, per_sport_acwr_data); all_c += c
    days, c = enforce_sport_budget(days, budgets);             all_c += c
    days, c = enforce_hard_easy(days);                         all_c += c
    days, c = enforce_strength_limit(days);                    all_c += c
    days, c = enforce_rollski_limit(days);                     all_c += c
    days     = ensure_warmup(days)
    days     = add_env_nutrition(days, weather or [], phase=phase, races=races, athlete=athlete, wellness=wellness)
    days, c  = strip_train_low_contradiction(days);            all_c += c
    days, c  = enforce_today_time_budget(days, time_available_text); all_c += c
    return plan.model_copy(update={"days": days}), all_c


__all__ = [
    "apply_safety_rules",
    "HARD_THRESHOLD",
    "INJURY_PROFILES",
    "ZONE_NP_RATIO",
    "add_env_nutrition",
    "apply_injury_rules",
    "calculate_nutrition_periodization",
    "enforce_deload",
    "enforce_hard_easy",
    "enforce_hrv",
    "enforce_illness",
    "enforce_locked",
    "enforce_max_consecutive_rest",
    "enforce_min_duration",
    "enforce_motivation_state",
    "enforce_per_sport_acwr_veto",
    "enforce_rollski_limit",
    "enforce_rtp",
    "enforce_sport_budget",
    "enforce_strength_limit",
    "enforce_today_time_budget",
    "enforce_tss",
    "ensure_warmup",
    "estimate_tss_coggan",
    "ftp_for_sport",
    "intensity_rating",
    "is_intense",
    "post_process",
    "repair_low_tss",
    "strip_train_low_contradiction",
]

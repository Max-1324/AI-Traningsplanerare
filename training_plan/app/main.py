import training_plan.core.common as common
from training_plan.core.common import *
from training_plan.core.cli import parse_args
from training_plan.engine.libraries import *
from training_plan.engine.planning import *
from training_plan.integrations.services import *
from training_plan.integrations.services import _stockholm_now_naive
from training_plan.engine.analysis import *
from training_plan.engine.insights import *
from training_plan.engine.postprocess import *
from training_plan.engine.ai import *
from training_plan.engine.pipeline import *

args = None


def main(argv=None):
    global args

    # Tvinga Python att använda UTF-8 för in- och utmatning så att å, ä, ö fungerar i Windows-terminaler
    import sys
    if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stdin.reconfigure(encoding='utf-8')

    args = parse_args(argv)
    common.args = args
    ensure_required_config()
    log.info("Hämtar data från intervals.icu...")
    try:
        athlete    = fetch_athlete()
        wellness   = fetch_wellness(args.days_history)
        fitness    = fetch_fitness(args.days_history)
        activities = fetch_activities(args.days_history)
        races      = fetch_races(365)
        planned    = fetch_planned_workouts(args.horizon)
        all_events = fetch_all_planned_events(days_back=28)
        log.info(f"  {len(activities)} aktiviteter | {len(wellness)} wellness | {len(races)} tävlingar | {len(planned)} planerade")
    except requests.HTTPError as e:
        log.error(f"API-fel: {e}"); sys.exit(1)

    state = load_state()

    manual_workouts = [w for w in planned if not is_ai_generated(w) and w.get("category") == "WORKOUT"]
    ai_workouts     = [w for w in planned if is_ai_generated(w)]
    locked_dates    = {w.get("start_date_local","")[:10] for w in manual_workouts}
    if manual_workouts: log.info(f"  {len(manual_workouts)} manuella pass låsta: {', '.join(sorted(locked_dates))}")

    log.info("Hämtar väder...")
    weather = fetch_weather(args.horizon)

    # ── DATAKVALITETSVALIDERING ──────────────────────────────────────────────
    dq = validate_data_quality(activities, wellness)
    if dq["has_issues"]:
        log.warning(f"⚠️  Datakvalitet: {len(dq['warnings'])} varningar")
    activities_clean = [a for a in activities
                        if (a.get("id") or a.get("start_date_local","")) not in dq["filtered_activity_ids"]]
    wellness_clean   = [w for w in wellness
                        if w.get("id","")[:10] not in dq["bad_wellness_dates"]]

    lf  = fitness[-1] if fitness else {}
    atl = lf.get("atl",0.0); ctl = max(lf.get("ctl",1.0),1.0); tsb_val = lf.get("tsb",0.0)
    hrv         = calculate_hrv(wellness_clean)
    phase       = training_phase(races, date.today())
    budgets     = {st: sport_budget(st, activities_clean, manual_workouts) for st in ("Run","RollerSki")}

    # ── MOTIVATIONSANALYS ────────────────────────────────────────────────────
    motivation = analyze_motivation(wellness_clean, activities_clean)
    log.info(f"🧠 Motivation: {motivation['state']} ({motivation['summary']})")

    today_wellness    = next((w for w in wellness if w.get("id","").startswith(date.today().isoformat())), None)

    y_events = [w for w in all_events
                if w.get("start_date_local","")[:10] == (date.today()-timedelta(days=1)).isoformat()
                and w.get("category") == "WORKOUT"]
    yesterday_planned = y_events[0] if y_events else None

    yesterday_actuals = fetch_yesterday_actual(activities_clean)

    # --- PROGRESSION CHECK + AUTOREGULERING ---------------------------------
    check_and_advance_workout_progression(yesterday_planned, yesterday_actuals, state)
    # Bygg rådata för autoregulering (dubbel-avancering om exceptionell prestation)
    yesterday_raw: dict = {}
    if yesterday_actuals:
        a0 = yesterday_actuals[0]
        yesterday_raw = {
            "rpe":    a0.get("perceived_exertion"),
            "feel":   a0.get("feel"),
            "sport":  a0.get("type", ""),
            "missed": False,
        }
        # Försök matcha mot ett pass i biblioteket
        planned_name = (yesterday_planned.get("name","") if yesterday_planned else "").lower()
        for wk_key_c, wk_def_c in WORKOUT_LIBRARY.items():
            for lvl_c in wk_def_c["levels"]:
                kp = re.findall(r"(\d+)\s*[x×]\s*(\d+)", lvl_c["label"].lower())
                if kp:
                    r_, m_ = kp[0]
                    if re.search(rf"{r_}\s*[x×]\s*{m_}", planned_name):
                        yesterday_raw["workout_key"] = wk_key_c
                        break
            if "workout_key" in yesterday_raw:
                break
    elif yesterday_planned and is_ai_generated(yesterday_planned):
        yesterday_raw = {"missed": True}
    auto_signals = autoregulate_from_yesterday(yesterday_raw, state)

    morning = morning_questions(args.auto, today_wellness, yesterday_planned, yesterday_actuals)
    if not args.auto and not args.dry_run:
        save_morning_wellness(morning, today_wellness=today_wellness)
    vetos   = biometric_vetoes(hrv, morning.get("life_stress",1))

    # ── RETURN TO PLAY ───────────────────────────────────────────────────────
    # Använd den ofiltrerade aktivitetslistan här. En aktivitet med "dålig" data
    # (t.ex. för hög IF pga fel FTP) är fortfarande en aktivitet, inte en vilodag.
    # Filtret är för aggressivt för just denna kontroll.
    rtp_status = check_return_to_play(activities, date.today())
    if rtp_status.get("is_active"):
        log.info(f"🚑 Return to Play-protokoll aktivt ({rtp_status['days_off']} vilodagar i rad)")

    mesocycle = determine_mesocycle(fitness, activities_clean, state)
    save_state(state)
    log.info(f"🔄 Mesocykel: Block {mesocycle['block_number']}, Vecka {mesocycle['week_in_block']}/4"
             + (" [DELOAD]" if mesocycle['is_deload'] else ""))

    # ── 2: CTL-TRAJEKTORIA (körs FÖRE budget så vi kan använda required_weekly_tss) ──
    taper_config = get_taper_config(races, date.today())
    race_date = taper_config["race_date"]
    taper_days = taper_config["taper_days"]
    trajectory = ctl_trajectory(ctl, race_date, TARGET_CTL, taper_days=taper_days)
    if trajectory["has_target"]:
        log.info(f"🎯 CTL-trajektoria: {trajectory['message']}")

    # Faktisk ramp från intervals.icu:s egna CTL-värden (7 dgr bakåt)
    actual_weekly_ramp = None
    if len(fitness) >= 8:
        ctl_7d_ago = fitness[-8].get("ctl", ctl)
        actual_weekly_ramp = round(ctl - ctl_7d_ago, 1)
        log.info(f"📈 Faktisk CTL-ramp (intervals.icu): {actual_weekly_ramp:+.1f} CTL/vecka")

    tsb_bgt = tss_budget(
        ctl, tsb_val, args.horizon + 1, fitness, mesocycle["load_factor"],  # +1: matchar enforce_tss horizon_days
        required_weekly_tss=trajectory.get("required_weekly_tss"),
        actual_weekly_ramp=actual_weekly_ramp,
    )
    target_ramp = choose_target_ramp(
        ctl,
        mesocycle_factor=mesocycle["load_factor"],
        required_weekly_tss=trajectory.get("required_weekly_tss"),
        actual_weekly_ramp=actual_weekly_ramp,
    )
    budget_daily_tss = tsb_bgt / max(args.horizon + 1, 1)
    budget_ramp = ctl_ramp_from_daily_tss(ctl, budget_daily_tss)
    log.info(f"🎚️ Rampmål: +{target_ramp:.1f} CTL/vecka | Budget motsvarar ca +{budget_ramp:.1f} CTL/vecka")

    # ── 3: COMPLIANCE ────────────────────────────────────────────────────────
    compliance = compliance_analysis(all_events, activities_clean, days=28)
    log.info(f"📋 Compliance: {compliance['completion_rate']}% ({compliance['total_completed']}/{compliance['total_planned']})")

    # ── 4: PASSBIBLIOTEK ─────────────────────────────────────────────────────
    workout_levels = state.get("workout_levels", {})
    workout_lib_text = get_next_workouts(workout_levels, phase["phase"])
    log.info(f"📚 Passbibliotek: {', '.join(f'{k}=L{v}' for k,v in workout_levels.items())}")

    # ── 6: FTP-TEST ──────────────────────────────────────────────────────────
    ftp_check = ftp_test_check(activities_clean, planned, athlete)
    if ftp_check["needs_test"]:
        log.info(f"🔬 {ftp_check['recommendation']}")

    # ── PREHAB ───────────────────────────────────────────────────────────────
    vols_clean = sport_volumes(activities_clean)
    dominant_sport = max(vols_clean, key=vols_clean.get) if vols_clean else "VirtualRide"
    prehab = recommend_prehab(morning.get("injury_today", ""), dominant_sport)
    log.info(f"🤸 Prehab: {prehab['name']}")

    # ── ACWR TREND ANALYSIS ──────────────────────────────────────────────────
    acwr_trend = acwr_trend_analysis(fitness)
    if acwr_trend.get("warning"):
        log.info(f"📊 {acwr_trend['warning']}")
    else:
        log.info(f"📊 ACWR: {acwr_trend.get('current_ratio','?')} {acwr_trend.get('zone_emoji','')} {acwr_trend.get('current_zone','?')}")

    # ── SPORT-SPECIFIK ACWR ──────────────────────────────────────────────────
    sport_acwr = per_sport_acwr(activities_clean)
    danger_sports = [s for s, d in sport_acwr.items() if d["zone"] == "DANGER"]
    if danger_sports:
        log.warning(f"⚠️  Sport-ACWR DANGER: {', '.join(danger_sports)}")

    # ── RACE WEEK PROTOCOL ───────────────────────────────────────────────────
    race_week = race_week_protocol(races, date.today())
    if race_week.get("is_active"):
        log.info(f"🏁 Race week aktiv! {race_week['race_name']} om {race_week['days_to_race']}d")

    # ── PRE-RACE LOGISTIK ────────────────────────────────────────────────────
    pre_race_advice = pre_race_logistics_advice(race_week.get("days_to_race", 999)) if race_week else ""

    # ── TAPER QUALITY SCORE ──────────────────────────────────────────────────
    taper_score = taper_quality_score(fitness, race_date, taper_days=taper_days)
    if taper_score.get("is_in_taper"):
        log.info(f"📉 Taper dag {taper_score['taper_day']}/{taper_score['taper_days']} | Score: {taper_score['score']}/100 {taper_score['verdict']}")

    # ── YESTERDAY ANALYSIS ───────────────────────────────────────────────────
    yesterday_analysis = analyze_yesterday(yesterday_planned, yesterday_actuals, activities_clean)

    # ── SCHEDULE CONSTRAINTS ─────────────────────────────────────────────────
    constraints = parse_constraints_from_events(planned)
    horizon_dates = [(date.today() + timedelta(days=i)).isoformat() for i in range(args.horizon + 1)]
    constraints_text = format_constraints_for_prompt(constraints, horizon_dates)
    if constraints:
        log.info(f"📅 Schema-begränsningar: {len(constraints)} regler från intervals.icu")

    # ── Sammanfatta befintlig plan ───────────────────────────────────────────
    existing_plan_summary = format_existing_plan(ai_workouts)

    # ── NYA ANALYSER ─────────────────────────────────────────────────────────
    readiness      = calculate_readiness_score(hrv, wellness_clean, activities_clean)
    np_if_analysis = analyze_np_if(activities_clean)
    polarization   = polarization_analysis(activities_clean, days=21)
    session_quality = session_quality_analysis(activities_clean, days=28)
    race_demands   = race_demands_analysis(races, activities_clean)
    coach_confidence = coach_confidence_analysis(dq, activities_clean, wellness_clean, fitness, hrv)
    state["learned_patterns"] = update_learned_patterns(state, all_events, activities_clean)
    historical_validation, outcome_tracking = update_plan_outcome_tracking(state, activities_clean)
    development_needs = development_needs_analysis(
        phase, readiness, motivation, compliance, ftp_check,
        np_if_analysis, session_quality, race_demands, polarization,
    )
    block_objective = update_block_objective(state, mesocycle, phase, development_needs, race_demands)
    learned_patterns_raw = state["learned_patterns"]
    capacity_map = build_capacity_map(
        activities_clean,
        session_quality=session_quality,
        race_demands=race_demands,
        readiness=readiness,
        np_if_analysis=np_if_analysis,
        polarization=polarization,
    )
    nutrition_readiness = build_nutrition_readiness(
        activities_clean,
        race_demands=race_demands,
        athlete=athlete,
        phase=phase,
    )
    individualization_profile = build_individualization_profile(
        state,
        learned_patterns=learned_patterns_raw,
        compliance=compliance,
        session_quality=session_quality,
        motivation=motivation,
        outcome_tracking=outcome_tracking,
    )
    minimum_effective_dose = build_minimum_effective_dose(
        ctl,
        tsb_bgt,
        readiness=readiness,
        motivation=motivation,
        compliance=compliance,
        block_objective=block_objective,
        development_needs=development_needs,
        race_demands=race_demands,
        coach_confidence=coach_confidence,
    )
    execution_friction = build_execution_friction(
        constraints,
        manual_workouts,
        compliance=compliance,
        learned_patterns=learned_patterns_raw,
        motivation=motivation,
        morning=morning,
        minimum_effective_dose=minimum_effective_dose,
    )
    training_frequency_target = build_training_frequency_target(
        args.horizon + 1,
        manual_workouts,
        readiness=readiness,
        motivation=motivation,
        compliance=compliance,
        minimum_effective_dose=minimum_effective_dose,
        execution_friction=execution_friction,
        mesocycle=mesocycle,
        morning=morning,
    )
    benchmark_system = build_benchmark_system(
        activities_clean,
        planned,
        athlete=athlete,
        phase=phase,
        ftp_check=ftp_check,
        race_demands=race_demands,
        capacity_map=capacity_map,
        nutrition_readiness=nutrition_readiness,
        readiness=readiness,
        np_if_analysis=np_if_analysis,
    )
    block_learning = build_block_learning(
        state,
        compliance=compliance,
        session_quality=session_quality,
        outcome_tracking=outcome_tracking,
        development_needs=development_needs,
        individualization_profile=individualization_profile,
    )
    performance_forecast = build_performance_forecast(
        fitness,
        readiness=readiness,
        compliance=compliance,
        trajectory=trajectory,
        capacity_map=capacity_map,
        coach_confidence=coach_confidence,
        nutrition_readiness=nutrition_readiness,
        block_learning=block_learning,
    )
    race_readiness = build_race_readiness_score(
        readiness=readiness,
        race_demands=race_demands,
        session_quality=session_quality,
        compliance=compliance,
        taper_score=taper_score,
        coach_confidence=coach_confidence,
        performance_forecast=performance_forecast,
        capacity_map=capacity_map,
        nutrition_readiness=nutrition_readiness,
    )
    season_plan = build_season_plan(
        phase=phase,
        races=races,
        mesocycle=mesocycle,
        trajectory=trajectory,
        development_needs=development_needs,
        block_objective=block_objective,
        benchmark_system=benchmark_system,
        performance_forecast=performance_forecast,
        capacity_map=capacity_map,
        race_readiness=race_readiness,
    )
    planner_insights = {
        "capacity_map": capacity_map,
        "nutrition_readiness": nutrition_readiness,
        "individualization_profile": individualization_profile,
        "minimum_effective_dose": minimum_effective_dose,
        "execution_friction": execution_friction,
        "training_frequency_target": training_frequency_target,
        "benchmark_system": benchmark_system,
        "block_learning": block_learning,
        "performance_forecast": performance_forecast,
        "race_readiness": race_readiness,
        "season_plan": season_plan,
    }
    state["planner_insights"] = {
        "updated": date.today().isoformat(),
        **{key: value.get("summary", "") for key, value in planner_insights.items()},
    }
    save_state(state)
    learned_patterns = format_learned_patterns(learned_patterns_raw)
    log.info(f"💪 Readiness: {readiness['score']}/100 ({readiness['label']})")
    log.info(f"🎯 {development_needs['summary']}")
    log.info(f"🏁 {race_demands['summary']}")
    log.info(f"🛠️ {session_quality['summary']}")
    log.info(f"🧭 {coach_confidence['summary']}")

    # Datum att exkludera från AI-planen = endast manuellt inlagda pass (AI-events regenereras alltid)
    log.info(f"Capacity map: {capacity_map['summary']}")
    log.info(f"Performance forecast: {performance_forecast['summary']}")
    log.info(f"Race readiness: {race_readiness['summary']}")
    log.info(f"Historical validation: {historical_validation['summary']}")
    log.info(f"Outcome tracking: {outcome_tracking['summary']}")
    existing_plan_dates = locked_dates  # locked_dates = datum med manuella (ej AI) pass
    # TSS från manuella pass (AI-events räknas inte – de ska regenereras)
    base_tss_by_date = {}
    for w in manual_workouts:
        d = w.get("start_date_local","")[:10]
        if not d:
            continue
        base_tss_by_date[d] = base_tss_by_date.get(d, 0) + (w.get("planned_load", 0) or 0)

    log.info(f"🤖 Coachen granskar plan och dagsform...")
    prompt_morning = dict(morning)
    if not prompt_morning.get("time_available"):
        prompt_morning["time_available"] = "Ingen explicit tidsgrans"

    prompt = build_prompt(
        activities, wellness_clean, fitness, races, weather, prompt_morning, args.horizon,
        manual_workouts, athlete, hrv, budgets, tsb_bgt, vetos, phase,
        existing_plan_summary, mesocycle, trajectory, compliance,
        workout_lib_text, ftp_check, yesterday_analysis, constraints_text,
        acwr_trend=acwr_trend, race_week=race_week, taper_score=taper_score,
        rtp_status=rtp_status,
        data_quality=dq, per_sport_acwr=sport_acwr, motivation=motivation,
        prehab=prehab, pre_race_info=pre_race_advice,
        autoregulation_signals=auto_signals, mesocycle_for_strength=mesocycle,
        readiness=readiness, np_if_analysis=np_if_analysis, learned_patterns=learned_patterns,
        exclude_dates=existing_plan_dates, development_needs=development_needs,
        block_objective=block_objective, race_demands=race_demands,
        session_quality=session_quality, coach_confidence=coach_confidence,
        polarization=polarization, historical_validation=historical_validation,
        outcome_tracking=outcome_tracking, planner_insights=planner_insights,
    )
    review_context = {
        "today": date.today().isoformat(),
        "phase": phase.get("phase"),
        "mesocycle": {
            "block_number": mesocycle.get("block_number"),
            "week_in_block": mesocycle.get("week_in_block"),
            "is_deload": mesocycle.get("is_deload"),
            "load_factor": mesocycle.get("load_factor"),
        },
        "trajectory": {
            "message": trajectory.get("message"),
            "required_weekly_tss": trajectory.get("required_weekly_tss"),
            "required_daily_tss": trajectory.get("required_daily_tss"),
        },
        "block_objective": block_objective,
        "development_needs": {
            "summary": development_needs.get("summary"),
            "must_hit_sessions": development_needs.get("must_hit_sessions", []),
            "priorities": development_needs.get("priorities", [])[:3],
        },
        "race_demands": race_demands,
        "readiness": readiness,
        "motivation": motivation,
        "compliance": {
            "completion_rate": compliance.get("completion_rate"),
            "intensity_missed": compliance.get("intensity_missed"),
            "intensity_planned": compliance.get("intensity_planned"),
        },
        "coach_confidence": coach_confidence,
        "session_quality": session_quality,
        "capacity_map": {
            "summary": capacity_map.get("summary"),
            "weakest": capacity_map.get("weakest", []),
            "strongest": capacity_map.get("strongest", []),
        },
        "performance_forecast": performance_forecast,
        "race_readiness": race_readiness,
        "nutrition_readiness": nutrition_readiness,
        "minimum_effective_dose": minimum_effective_dose,
        "execution_friction": execution_friction,
        "training_frequency_target": training_frequency_target,
        "benchmark_system": {
            "summary": benchmark_system.get("summary"),
            "benchmarks": benchmark_system.get("benchmarks", [])[:3],
        },
        "block_learning": block_learning,
        "season_plan": {
            "summary": season_plan.get("summary"),
            "blocks": season_plan.get("blocks", [])[:4],
        },
        "historical_validation_summary": historical_validation.get("summary", ""),
        "outcome_tracking_summary": outcome_tracking.get("summary", ""),
    }

    def apply_postprocess(candidate_plan):
        return post_process(
            candidate_plan, hrv, budgets, locked_dates, tsb_bgt, activities_clean, weather, athlete,
            injury_note=morning.get('injury_today', ''), mesocycle=mesocycle,
            constraints=constraints, today_wellness=today_wellness, rtp_status=rtp_status,
            per_sport_acwr_data=sport_acwr, motivation=motivation,
            phase=phase, races=races, wellness=wellness_clean,
            base_tss_by_date=base_tss_by_date, horizon_days=args.horizon + 1,
            time_available_text=morning.get("time_available", ""),
        )

    plan, changes, decision_trace = run_plan_pipeline(
        args.provider,
        prompt,
        apply_postprocess,
        athlete,
        base_tss_by_date,
        review_context,
        max_iterations=int(os.getenv("PLAN_REVIEW_MAX_ITERATIONS", "2")),
        candidate_count=int(os.getenv("PLAN_CANDIDATE_COUNT", "3")),
    )
    # Rensa coach-feedback om det inte finns faktisk aktivitetsdata att ge feedback om
    if not yesterday_analysis:
        plan = plan.model_copy(update={"yesterday_feedback": ""})
    planned_total_tss = sum(estimate_tss_coggan(d, athlete) for d in plan.days) + sum(base_tss_by_date.values())
    planned_daily_tss = planned_total_tss / max(args.horizon + 1, 1)
    planned_ramp = ctl_ramp_from_daily_tss(ctl, planned_daily_tss)
    log.info(f"📐 Planerad ramp från sparad plan: ca +{planned_ramp:.1f} CTL/vecka")

    print_plan(
        plan, changes, mesocycle, trajectory, acwr_trend, taper_score, race_week, rtp_status,
        planner_insights=planner_insights,
    )

    if args.dry_run:
        print("\nDRY-RUN - ingenting sparades.")
        print(f"Validering: {len(changes)} ändringar gjorda av post-processing.")
        ans = input("Vill du spara ändå? (j/n) [n]: ").strip().lower()
        if ans not in ("j","ja","y","yes"): return

    now_local = _stockholm_now_naive()

    # ── Avgör uppdateringsläge ────────────────────────────────────────────────
    mode, mode_reason = plan_update_mode(
        ai_workouts, yesterday_actuals, yesterday_planned, hrv, wellness, activities, args.horizon
    )

    # Kontrollera om befintlig plan uppfyller TSS-kravet – om inte, tvinga omplanering
    if mode == "none" and ai_workouts:
        future_ai = [w for w in ai_workouts
                     if w.get("start_date_local","")[:10] >= date.today().isoformat()]
        future_ai_tss = sum(w.get("planned_load", 0) or 0 for w in future_ai)
        future_manual_tss = sum(
            load for day_str, load in base_tss_by_date.items()
            if day_str >= date.today().isoformat()
        )
        future_tss = future_ai_tss + future_manual_tss
        if future_tss < tsb_bgt * 0.75:
            mode = "full"
            mode_reason = (f"Befintlig plan ({future_tss} TSS inkl. manuella pass) täcker under 75% av budget "
                           f"({tsb_bgt} TSS) – regenererar.")

    log.info(f"📋 Läge: {mode.upper()} – {mode_reason}")

    log.info("Uppdaterar intervals.icu...")

    # Nutrition på manuella pass
    man_nutr = {m.date: m.nutrition for m in plan.manual_workout_nutrition if m.nutrition}
    for w in manual_workouts:
        d = w.get("start_date_local","")[:10]
        if d in man_nutr:
            update_manual_nutrition(w, man_nutr[d])
            log.info(f"  Nutrition tillagd: {w.get('name','?')} ({d})")

    # ── Spara Daglig Coach-anteckning ─────────────────────────────────────────
    if not args.dry_run:
        save_daily_note_to_icu(plan, changes, planner_insights=planner_insights)
    else:
        print("\n[DRY-RUN] Skulle ha sparat daglig coach-anteckning till intervals.")

    # ── 7: VECKORAPPORT (körs på måndagar eller full regen) ──────────────────
    if date.today().weekday() == 0 or mode == "full":
        try:
            report = generate_weekly_report(
                activities_clean, wellness_clean, fitness, mesocycle, trajectory, compliance, ftp_check,
                acwr_trend=acwr_trend, taper_score=taper_score, ai_feedback=plan.weekly_feedback,
                motivation=motivation, development_needs=development_needs,
                block_objective=block_objective, race_demands=race_demands,
                session_quality=session_quality, coach_confidence=coach_confidence,
                polarization=polarization, planner_insights=planner_insights,
            )
            if not args.dry_run:
                save_weekly_report_to_icu(report)
            else:
                print("\n" + report)
        except Exception as e:
            log.warning(f"Veckorapport misslyckades: {e}")

    if mode == "none":
        log.info(f"✅ {mode_reason}")
        print(f"\n✅ {mode_reason}\n")
        return

    record_plan_decision(state, plan, decision_trace, planned_total_tss, block_objective, race_demands)
    save_state(state)

    if mode == "full":
        started_ai = [w for w in ai_workouts if event_has_started(w, now_local)]
        deleted = delete_ai_workouts(ai_workouts, now_local)
        if deleted: log.info(f"  Tog bort {deleted} gamla AI-workouts")
        if started_ai:
            log.info(f"  Behåller {len(started_ai)} AI-events som redan startat/skett")
        days_to_save = [day for day in plan.days if not plan_day_has_started(day, now_local)]

    elif mode == "extend":
        # Behåll befintliga datum och lägg bara till saknade, men tillåt dubbelpass om datumet kan ersättas säkert.
        existing_count = {}
        started_dates = set()
        for w in ai_workouts:
            d = w.get("start_date_local","")[:10]
            if not d:
                continue
            existing_count[d] = existing_count.get(d, 0) + 1
            if event_has_started(w, now_local):
                started_dates.add(d)

        new_count = {}
        for day in plan.days:
            if plan_day_has_started(day, now_local):
                continue
            new_count[day.date] = new_count.get(day.date, 0) + 1

        dates_to_delete = {
            day_str for day_str, cnt in new_count.items()
            if existing_count.get(day_str, 0) > 0
            and cnt > existing_count.get(day_str, 0)
            and day_str not in started_dates
        }
        if dates_to_delete:
            to_del = [
                {"id": w["id"]}
                for w in ai_workouts
                if w.get("start_date_local","")[:10] in dates_to_delete and not event_has_started(w, now_local)
            ]
            for chunk in [to_del[i:i+50] for i in range(0, len(to_del), 50)]:
                requests.put(f"{BASE}/athlete/{ATHLETE_ID}/events/bulk-delete", auth=AUTH, timeout=15, json=chunk).raise_for_status()
            log.info(f"  Ersätter {len(to_del)} event(s) med dubbelpass på: {', '.join(sorted(dates_to_delete))}")

        existing_dates = {d for d in existing_count if d not in dates_to_delete}
        days_to_save = [
            day for day in plan.days
            if not plan_day_has_started(day, now_local) and day.date not in existing_dates
        ]
        preserved = len(started_dates & set(new_count))
        if preserved:
            log.info(f"  Behåller {preserved} datum med redan startade AI-events")
        log.info(f"  Behåller {len(existing_dates)} befintliga datum, lägger till {len(days_to_save)} nya.")

    skipped_started_days = len(plan.days) - len(days_to_save)
    if skipped_started_days:
        log.info(f"  Hoppar över {skipped_started_days} nya plan-dag(ar) som redan startat/skett")

    saved = errors = 0
    for day in days_to_save:
        try:
            if day.intervals_type != "Rest" and day.duration_min > 0:
                save_workout(day, athlete)
            else:
                save_event(day)
            saved += 1
        except requests.HTTPError as e:
            log.error(f"Misslyckades spara {day.date}: {e}"); errors += 1

    vetoed_count = sum(1 for d in plan.days if d.vetoed)
    log.info(f"Klart! {saved} pass sparade. {vetoed_count} säkerhetsjusterades av reglerna. {errors} fel. {len(changes)} post-processing-ändringar.")
    print("\nKör igen imorgon bitti.\n")


if __name__ == "__main__":
    main()

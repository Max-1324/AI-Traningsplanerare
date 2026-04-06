from __future__ import annotations

from collections import defaultdict
from typing import Callable, Optional

from training_plan.core.common import *
from training_plan.core.models import AIPlan, PlanDecisionTrace, PlanReview, PlanScores
from training_plan.engine.ai import call_ai, parse_plan
from training_plan.engine.planning import classify_session_category
from training_plan.engine.postprocess import estimate_tss_coggan

_KEY_PLAN_CATEGORIES = {"ftp_test", "long_ride", "threshold", "vo2"}
_INTENSITY_BY_ZONE = {
    "Z1": 0.55,
    "Z2": 0.70,
    "Z3": 0.83,
    "Z4": 0.95,
    "Z5": 1.05,
    "Z6": 1.15,
    "Z7": 1.25,
}
_CANDIDATE_VARIATIONS = [
    {
        "label": "Kandidat A",
        "focus": "Balanserad huvudkandidat",
        "instructions": [
            "Bygg en balanserad plan med tydliga must-hit-pass och rimlig risk.",
            "Optimera för helheten snarare än för max volym eller max aggressivitet.",
        ],
    },
    {
        "label": "Kandidat B",
        "focus": "Enkelhet och robusthet först",
        "instructions": [
            "Förenkla upplägget aktivt och minimera filler-pass.",
            "Skydda nyckelpassen men välj den mest robusta och genomförbara strukturen.",
        ],
    },
    {
        "label": "Kandidat C",
        "focus": "Hög specificitet mot målet",
        "instructions": [
            "Prioritera race demands och blockets primära adaptation tydligare.",
            "Var gärna mer specifik än Kandidat A, men håll dig inom säkra belastningsramar.",
        ],
    },
]


def generate_plan(provider: str, prompt: str) -> AIPlan:
    return parse_plan(call_ai(provider, prompt))


def _extract_json_payload(raw: str) -> dict:
    clean = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    candidates = [clean]
    matches = list(re.finditer(r"\{", clean))
    if matches:
        candidates.append(clean[matches[0].start():])

    last_error: Exception | None = None
    for candidate in candidates:
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError as exc:
            last_error = exc

    raise ValueError(f"Kunde inte extrahera JSON-objekt: {last_error}")


def _parse_structured_response(raw: str, model_cls, fallback, label: str):
    try:
        payload = _extract_json_payload(raw)
        parsed = model_cls.model_validate(payload)
        log.info(f"✅ {label} parsad OK")
        return parsed
    except Exception as exc:
        log.warning(f"{label} kunde inte tolkas: {exc}")
        return fallback


def _weighted_plan_intensity(day: PlanDay) -> float | None:
    if not day.workout_steps:
        return None

    total = sum(step.duration_min for step in day.workout_steps) or 0
    if total <= 0:
        return None

    weighted = 0.0
    for step in day.workout_steps:
        weighted += step.duration_min * _INTENSITY_BY_ZONE.get(step.zone.upper(), 0.70)
    return round(weighted / total, 2)


def classify_plan_day(day: PlanDay) -> str:
    payload = {
        "name": day.title,
        "type": day.intervals_type,
        "moving_time": day.duration_min * 60,
        "icu_intensity": _weighted_plan_intensity(day),
    }
    return classify_session_category(payload)


def summarize_plan_candidate(plan: AIPlan, athlete: dict | None = None,
                             base_tss_by_date: Optional[dict[str, float]] = None) -> dict:
    base_tss_by_date = base_tss_by_date or {}
    daily = []
    total_tss = sum(base_tss_by_date.values())
    key_sessions = []

    for day in plan.days:
        category = classify_plan_day(day)
        tss = estimate_tss_coggan(day, athlete) if athlete else 0
        total_tss += tss
        item = {
            "date": day.date,
            "title": day.title,
            "type": day.intervals_type,
            "slot": day.slot,
            "duration_min": day.duration_min,
            "category": category,
            "estimated_tss": round(tss, 1),
        }
        daily.append(item)
        if category in _KEY_PLAN_CATEGORIES:
            key_sessions.append(item)

    return {
        "planned_total_tss": round(total_tss, 1),
        "manual_total_tss": round(sum(base_tss_by_date.values()), 1),
        "planned_days": len(plan.days),
        "key_sessions": key_sessions,
        "daily": daily,
    }


def _plan_for_prompt(plan: AIPlan) -> str:
    return json.dumps(plan.model_dump(exclude={"decision_trace"}, exclude_none=True), ensure_ascii=False, indent=2)


def _compact_context(review_context: dict) -> str:
    return json.dumps(review_context, ensure_ascii=False, indent=2, default=str)


def _candidate_specs(candidate_count: int) -> list[dict]:
    specs = []
    for idx in range(candidate_count):
        base = _CANDIDATE_VARIATIONS[idx % len(_CANDIDATE_VARIATIONS)]
        specs.append({
            "label": f"Kandidat {chr(65 + idx)}",
            "focus": base["focus"],
            "instructions": list(base["instructions"]),
        })
    return specs


def build_candidate_prompt(base_prompt: str, candidate_spec: dict, attempt: int, total_candidates: int) -> str:
    instructions = "\n".join(f"- {line}" for line in candidate_spec["instructions"])
    return f"""
DU SKA SKAPA {candidate_spec['label']} av {total_candidates} i urvalet för revisionsvarv {attempt}.

Syftet är att ge review/scoring flera verkliga alternativ att välja mellan.
Skapa därför en meningsfullt annorlunda kandidat, inte bara små kosmetiska ändringar.

FOKUS FÖR DENNA KANDIDAT:
{candidate_spec['focus']}
{instructions}

KRAV:
- Behåll exakt samma JSON-schema som briefen kräver.
- Kopiera inte samma plan som de andra kandidaterna med bara små ordbyten.
- Skillnaden ska synas i prioritering, enkelhet, nyckelpass eller riskprofil.

BRIEF:
{base_prompt}
""".strip()


def build_review_prompt(plan: AIPlan, athlete: dict | None, base_tss_by_date: dict[str, float],
                        review_context: dict, postprocess_changes: list[str]) -> str:
    plan_summary = summarize_plan_candidate(plan, athlete, base_tss_by_date)
    changes_text = "\n".join(f"- {c}" for c in postprocess_changes) if postprocess_changes else "- Inga postprocess-ändringar"
    return f"""
ROLL: Du är en oberoende och skeptisk granskningscoach för uthållighetsplanering.
Du skapade INTE planen nedan. Din uppgift är att hitta fel, blinda fläckar, onödig komplexitet och övertro.
Var särskilt vaksam på planer som optimerar fel sak, gömmer filler-pass eller är säkra trots svag data.

BEDÖM PLANEN UTIFRÅN:
A. Målkoppling
B. Nyckelpass
C. Effektivitet
D. Belastning & risk
E. Individualisering
F. Race demands

COUNTERFACTUAL THINKING:
- Finns en enklare plan med liknande effekt?
- Vad händer om volymen minskas men kvaliteten behålls?
- Vad händer om fokus skiftas från nuvarande primära fokus till bästa alternativet?

VIKTIGT:
- Belöna inte bara planer som "låter coachiga".
- Om datagrunden är osäker ska det synas i reviewn.
- Filler-pass ska straffas.
- Must-hit-pass ska vara tydligt skyddade.

KONTEXT:
{_compact_context(review_context)}

PLANMETRIK:
{json.dumps(plan_summary, ensure_ascii=False, indent=2)}

POSTPROCESSING SOM REDAN HAR GJORTS:
{changes_text}

KANDIDATPLAN:
{_plan_for_prompt(plan)}

Returnera ENBART JSON med exakt detta schema:
{{
  "summary": "2-4 meningar med tydlig huvuddom",
  "goal_alignment": {{"rating": "STRONG|ADEQUATE|WEAK|CRITICAL", "rationale": "", "issues": [""], "recommendations": [""]}},
  "key_sessions": {{"rating": "STRONG|ADEQUATE|WEAK|CRITICAL", "rationale": "", "issues": [""], "recommendations": [""]}},
  "efficiency": {{"rating": "STRONG|ADEQUATE|WEAK|CRITICAL", "rationale": "", "issues": [""], "recommendations": [""]}},
  "load_and_risk": {{"rating": "STRONG|ADEQUATE|WEAK|CRITICAL", "rationale": "", "issues": [""], "recommendations": [""]}},
  "individualization": {{"rating": "STRONG|ADEQUATE|WEAK|CRITICAL", "rationale": "", "issues": [""], "recommendations": [""]}},
  "race_demands": {{"rating": "STRONG|ADEQUATE|WEAK|CRITICAL", "rationale": "", "issues": [""], "recommendations": [""]}},
  "strengths": ["max 4 konkreta styrkor"],
  "must_fix": ["det viktigaste som måste ändras innan planen kan litas på"],
  "uncertainty_sources": ["vad gör dig osäker"],
  "counterfactuals": [
    {{"question": "Finns en enklare plan med liknande effekt?", "answer": "", "tradeoffs": "", "recommendation": ""}},
    {{"question": "Vad händer om volym minskas men kvalitet behålls?", "answer": "", "tradeoffs": "", "recommendation": ""}},
    {{"question": "Vad händer om fokus skiftas till bästa alternativet?", "answer": "", "tradeoffs": "", "recommendation": ""}}
  ],
  "overall_verdict": "PASS|REVISE|REJECT"
}}
""".strip()


def review_plan(provider: str, plan: AIPlan, athlete: dict | None,
                base_tss_by_date: dict[str, float], review_context: dict,
                postprocess_changes: list[str]) -> PlanReview:
    fallback = PlanReview(
        summary="Review-steget kunde inte tolkas säkert. Planen bör förenklas och granskas igen.",
        must_fix=["Review-svaret var ogiltigt; kör ett säkrare revisionsvarv."],
        uncertainty_sources=["Reviewer-response kunde inte parsas."],
        overall_verdict="REVISE",
    )
    raw = call_ai(provider, build_review_prompt(plan, athlete, base_tss_by_date, review_context, postprocess_changes))
    return _parse_structured_response(raw, PlanReview, fallback, "Plan-review")


def build_score_prompt(plan: AIPlan, review: PlanReview, athlete: dict | None,
                       base_tss_by_date: dict[str, float], review_context: dict) -> str:
    plan_summary = summarize_plan_candidate(plan, athlete, base_tss_by_date)
    return f"""
ROLL: Du är en separat scoring-modell. Du skapade inte planen och du skrev inte reviewn.
Din uppgift är att poängsätta planen nyktert utifrån prestationsnytta, risk, specificitet, enkelhet och hur säker du är.

SCORINGREGLER:
- Effectiveness 0-10: sannolik prestationsnytta för blockmålet
- Risk 0-10: risk för överbelastning, dålig absorption eller failure
- Specificity 0-10: hur väl planen matchar målet och race demands
- Simplicity 0-10: hur robust, tydlig och genomförbar planen är
- Confidence 0-10: hur säker du är på din egen bedömning

VIKTIGT:
- Hög osäkerhet i data eller review ska sänka confidence.
- Hög risk ska inte gömmas bakom hög effectiveness.
- En enkel plan med skyddade must-hit-pass får gärna slå en mer avancerad plan.

KONTEXT:
{_compact_context(review_context)}

PLANMETRIK:
{json.dumps(plan_summary, ensure_ascii=False, indent=2)}

REVIEW:
{json.dumps(review.model_dump(exclude_none=True), ensure_ascii=False, indent=2)}

PLAN:
{_plan_for_prompt(plan)}

Returnera ENBART JSON:
{{
  "effectiveness": 0,
  "risk": 0,
  "specificity": 0,
  "simplicity": 0,
  "confidence": 0,
  "rationale": "kort förklaring",
  "uncertainty_sources": ["vad gör scoret osäkert"],
  "action_hint": "ACCEPT|REVISE|REJECT"
}}
""".strip()


def score_plan(provider: str, plan: AIPlan, review: PlanReview, athlete: dict | None,
               base_tss_by_date: dict[str, float], review_context: dict) -> PlanScores:
    fallback = PlanScores(
        effectiveness=5,
        risk=6,
        specificity=5,
        simplicity=5,
        confidence=2,
        rationale="Score-steget kunde inte tolkas säkert; välj revision före acceptans.",
        uncertainty_sources=["Scoring-response kunde inte parsas."],
        action_hint="REVISE",
    )
    raw = call_ai(provider, build_score_prompt(plan, review, athlete, base_tss_by_date, review_context))
    return _parse_structured_response(raw, PlanScores, fallback, "Plan-score")


def decide_plan(review: PlanReview, scores: PlanScores) -> tuple[str, str]:
    dimensions = [
        review.goal_alignment,
        review.key_sessions,
        review.efficiency,
        review.load_and_risk,
        review.individualization,
        review.race_demands,
    ]
    critical_count = sum(1 for dim in dimensions if dim.rating == "CRITICAL")
    weak_count = sum(1 for dim in dimensions if dim.rating == "WEAK")

    reasons = []
    if review.must_fix:
        reasons.append(f"{len(review.must_fix)} must-fix")
    if critical_count:
        reasons.append(f"{critical_count} kritiska områden")
    if scores.risk >= 8:
        reasons.append(f"risk {scores.risk}/10")
    if scores.effectiveness <= 4:
        reasons.append(f"effectiveness {scores.effectiveness}/10")
    if scores.specificity <= 4:
        reasons.append(f"specificity {scores.specificity}/10")

    if (
        review.overall_verdict == "REJECT"
        or scores.action_hint == "REJECT"
        or scores.risk >= 8
        or scores.effectiveness <= 4
        or scores.specificity <= 4
        or critical_count >= 2
    ):
        return "REJECT", ", ".join(reasons) or "Planen underkänns av review/scoring."

    if (
        review.overall_verdict == "PASS"
        and scores.action_hint == "ACCEPT"
        and scores.effectiveness >= 7
        and scores.specificity >= 7
        and scores.risk <= 5
        and scores.simplicity >= 6
        and scores.confidence >= 4
        and not review.must_fix
        and critical_count == 0
        and weak_count <= 1
    ):
        return "ACCEPT", "Planen är målkopplad, tillräckligt säker och behöver inga tvingande ändringar."

    reasons.extend([
        f"effectiveness {scores.effectiveness}/10",
        f"risk {scores.risk}/10",
        f"specificity {scores.specificity}/10",
        f"simplicity {scores.simplicity}/10",
        f"confidence {scores.confidence}/10",
    ])
    return "REVISE", ", ".join(dict.fromkeys(reasons))


def build_revision_prompt(base_generation_prompt: str, plan: AIPlan, review: PlanReview,
                          scores: PlanScores, action: str, attempt: int,
                          postprocess_changes: list[str]) -> str:
    hard_reset = action == "REJECT"
    changes_text = "\n".join(f"- {c}" for c in postprocess_changes) if postprocess_changes else "- Inga postprocess-ändringar"
    revision_mode = (
        "KASTA den tidigare strukturen och bygg om planen från grunden."
        if hard_reset else
        "Behåll endast de delar som fortfarande är tydligt försvarbara. Revidera resten aktivt."
    )
    return f"""
ROLL: Du är revisionsplaneraren. Du MÅSTE förbättra planen utifrån oberoende review och scoring.
Försök inte försvara den gamla planen. Om reviewn säger att något är svagt eller fel ska det åtgärdas.

REVISIONSVARV: {attempt}
KRAV:
- Åtgärda must-fix först
- Skydda rätt must-hit-pass
- Ta bort filler-pass
- Förenkla om samma effekt kan nås med mindre friktion
- Visa osäkerhet i summary när datagrunden är osäker
- Om action är REJECT ska du tänka om från grunden

REVISIONSLÄGE:
{revision_mode}

NUVARANDE PLAN:
{_plan_for_prompt(plan)}

REVIEW:
{json.dumps(review.model_dump(exclude_none=True), ensure_ascii=False, indent=2)}

SCORES:
{json.dumps(scores.model_dump(exclude_none=True), ensure_ascii=False, indent=2)}

POSTPROCESSING SOM REDAN HAR GJORTS:
{changes_text}

ORIGINALT PLANERINGSBRIEF:
{base_generation_prompt}

Returnera ENBART samma AIPlan-JSON-schema som originalprompten kräver.
""".strip()


def _candidate_rank(review: PlanReview, scores: PlanScores) -> float:
    verdict_bonus = {"PASS": 2.0, "REVISE": 0.5, "REJECT": -2.0}
    return (
        scores.effectiveness * 2.0
        + scores.specificity * 1.6
        + scores.simplicity * 1.2
        + scores.confidence * 0.8
        - scores.risk * 1.8
        - len(review.must_fix) * 0.7
        + verdict_bonus.get(review.overall_verdict, 0.0)
    )


def _candidate_round_line(label: str, action: str, scores: PlanScores, review: PlanReview,
                          focus: str = "") -> str:
    must_fix = review.must_fix[0] if review.must_fix else "no major must-fix"
    return (
        f"{label}: {action} | Focus {focus or 'balanced'} | Effekt {scores.effectiveness}/10 | "
        f"Risk {scores.risk}/10 | Spec {scores.specificity}/10 | Enkelhet {scores.simplicity}/10 | "
        f"Confidence {scores.confidence}/10 | Must-fix {must_fix}"
    )


def _pick_round_winner(results: list[dict]) -> dict:
    accepted = [result for result in results if result["action"] == "ACCEPT"]
    pool = accepted if accepted else results
    return max(pool, key=lambda result: result["rank"])


def run_plan_pipeline(provider: str, generation_prompt: str,
                      postprocess_candidate: Callable[[AIPlan], tuple[AIPlan, list[str]]],
                      athlete: dict | None, base_tss_by_date: dict[str, float],
                      review_context: dict, max_iterations: int = 2,
                      candidate_count: int = 3) -> tuple[AIPlan, list[str], PlanDecisionTrace]:
    max_iterations = max(1, max_iterations)
    candidate_count = max(1, candidate_count)
    best_candidate: tuple[AIPlan, list[str], PlanDecisionTrace, float] | None = None
    current_plan: AIPlan | None = None
    current_changes: list[str] = []
    revision_history: list[str] = []
    last_trace: PlanDecisionTrace | None = None
    candidate_specs = _candidate_specs(candidate_count)

    for attempt in range(1, max_iterations + 1):
        if attempt == 1:
            log.info("🧠 Generate %s plan candidates", candidate_count)
            round_base_prompt = generation_prompt
        else:
            review = last_trace.review if last_trace and last_trace.review else PlanReview()
            scores = last_trace.scores if last_trace and last_trace.scores else PlanScores(
                effectiveness=5,
                risk=5,
                specificity=5,
                simplicity=5,
                confidence=3,
                rationale="Fallback inför revision.",
                action_hint="REVISE",
            )
            action = last_trace.action if last_trace else "REVISE"
            log.info(f"🔁 Revision varv {attempt}/{max_iterations} ({action})")
            round_base_prompt = build_revision_prompt(
                generation_prompt,
                current_plan or best_candidate[0],
                review,
                scores,
                action,
                attempt,
                current_changes,
            )

        round_results = []
        for candidate_spec in candidate_specs:
            candidate_prompt = build_candidate_prompt(round_base_prompt, candidate_spec, attempt, candidate_count)
            candidate_plan = generate_plan(provider, candidate_prompt)
            candidate_plan, candidate_changes = postprocess_candidate(candidate_plan)
            review = review_plan(
                provider,
                candidate_plan,
                athlete,
                base_tss_by_date,
                review_context,
                candidate_changes,
            )
            scores = score_plan(
                provider,
                candidate_plan,
                review,
                athlete,
                base_tss_by_date,
                review_context,
            )
            action, rationale = decide_plan(review, scores)
            rank = _candidate_rank(review, scores)
            round_results.append({
                "label": candidate_spec["label"],
                "focus": candidate_spec["focus"],
                "plan": candidate_plan,
                "changes": list(candidate_changes),
                "review": review,
                "scores": scores,
                "action": action,
                "rationale": rationale,
                "rank": rank,
            })
            log.info(
                "🧪 %s -> %s | Effekt %s/10 | Risk %s/10 | Spec %s/10 | Enkelhet %s/10 | Confidence %s/10",
                candidate_spec["label"],
                action,
                scores.effectiveness,
                scores.risk,
                scores.specificity,
                scores.simplicity,
                scores.confidence,
            )

        round_summary = [
            _candidate_round_line(
                result["label"], result["action"], result["scores"], result["review"], result["focus"]
            )
            for result in round_results
        ]
        winner = _pick_round_winner(round_results)
        current_plan = winner["plan"]
        current_changes = winner["changes"]
        revision_history.append(
            f"Varv {attempt}: valde {winner['label']} ({winner['focus']}) -> {winner['action']} | "
            f"Effekt {winner['scores'].effectiveness}/10 | Risk {winner['scores'].risk}/10 | "
            f"Spec {winner['scores'].specificity}/10 | Enkelhet {winner['scores'].simplicity}/10 | "
            f"Confidence {winner['scores'].confidence}/10"
        )

        trace = PlanDecisionTrace(
            action=winner["action"],
            rationale=winner["rationale"],
            iterations_run=attempt,
            used_with_override=False,
            selected_candidate=winner["label"],
            historical_validation_summary=review_context.get("historical_validation_summary", ""),
            outcome_tracking_summary=review_context.get("outcome_tracking_summary", ""),
            review=winner["review"],
            scores=winner["scores"],
            candidate_pool_summary=round_summary,
            revision_history=list(revision_history),
        )
        winner_rank = winner["rank"]

        if best_candidate is None or winner_rank > best_candidate[3]:
            best_candidate = (current_plan, list(current_changes), trace, winner_rank)
        last_trace = trace

        log.info("🏁 Varv %s vinnare: %s", attempt, _candidate_round_line(
            winner["label"], winner["action"], winner["scores"], winner["review"], winner["focus"]
        ))

        if winner["action"] == "ACCEPT":
            accepted_plan = current_plan.model_copy(update={"decision_trace": trace})
            return accepted_plan, current_changes, trace

    assert best_candidate is not None
    best_plan, best_changes, best_trace, _ = best_candidate
    best_trace = best_trace.model_copy(update={
        "used_with_override": True,
        "revision_history": list(revision_history) + [
            "Ingen kandidat nådde ACCEPT inom max iterationer; bästa reviderade versionen används med försiktighet."
        ],
    })
    log.warning("⚠️ Ingen plan nådde ACCEPT. Använder bästa reviderade kandidat med override.")
    return best_plan.model_copy(update={"decision_trace": best_trace}), best_changes, best_trace


def _parse_iso_date(value: str) -> Optional[date]:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except Exception:
        return None


def _plan_session_record(day: PlanDay) -> dict:
    category = classify_plan_day(day)
    return {
        "date": day.date,
        "title": day.title,
        "type": day.intervals_type,
        "duration_min": day.duration_min,
        "slot": day.slot,
        "category": category,
        "is_key": category in _KEY_PLAN_CATEGORIES,
    }


def record_plan_decision(state: dict, plan: AIPlan, trace: PlanDecisionTrace,
                         planned_total_tss: float, block_objective: dict | None = None,
                         race_demands: dict | None = None):
    bucket = state.setdefault("plan_pipeline", {})
    history = bucket.setdefault("history", [])
    sessions = [_plan_session_record(day) for day in plan.days]
    entry = {
        "created_on": date.today().isoformat(),
        "action": trace.action,
        "used_with_override": trace.used_with_override,
        "iterations_run": trace.iterations_run,
        "plan_dates": [session["date"] for session in sessions],
        "planned_sessions": sessions,
        "key_sessions": [session for session in sessions if session["is_key"]],
        "planned_total_tss": round(planned_total_tss, 1),
        "objective": (block_objective or {}).get("objective", ""),
        "primary_focus": (block_objective or {}).get("primary_focus", ""),
        "target_event": (race_demands or {}).get("target_name", ""),
        "review": trace.review.model_dump(exclude_none=True) if trace.review else {},
        "scores": trace.scores.model_dump(exclude_none=True) if trace.scores else {},
        "rationale": trace.rationale,
        "summary": plan.summary,
    }

    history = [
        item for item in history
        if not (
            item.get("created_on") == entry["created_on"]
            and item.get("plan_dates") == entry["plan_dates"]
        )
    ]
    history.append(entry)
    bucket["history"] = history[-40:]


def _match_planned_session(planned_session: dict, actuals: list[dict]) -> Optional[dict]:
    target_category = planned_session.get("category", "general")
    target_type = planned_session.get("type", "")
    target_duration = planned_session.get("duration_min", 0) or 0

    best_match = None
    best_score = -1
    for actual in actuals:
        score = 0
        actual_category = classify_session_category(actual)
        actual_type = actual.get("type", "")
        actual_duration = round(((actual.get("moving_time") or actual.get("elapsed_time") or 0) / 60))

        if actual_category == target_category:
            score += 3
        if actual_type == target_type:
            score += 2
        if target_duration and actual_duration:
            duration_gap = abs(actual_duration - target_duration) / max(target_duration, 1)
            if duration_gap <= 0.25:
                score += 2
            elif duration_gap <= 0.50:
                score += 1
        if target_type == "Rest" and not actual_duration:
            score += 1

        if score > best_score:
            best_match = actual
            best_score = score

    return best_match if best_score >= 3 else None


def update_plan_outcome_tracking(state: dict, activities: list[dict]) -> tuple[dict, dict]:
    bucket = state.setdefault("plan_pipeline", {})
    history = bucket.setdefault("history", [])
    today = date.today()

    activities_by_date: dict[str, list[dict]] = defaultdict(list)
    for activity in activities:
        d = activity.get("start_date_local", "")[:10]
        if d:
            activities_by_date[d].append(activity)

    for entry in history:
        if entry.get("outcome"):
            continue
        plan_dates = entry.get("plan_dates", [])
        plan_end = max((_parse_iso_date(d) for d in plan_dates), default=None)
        if not plan_end or plan_end >= today:
            continue

        planned_sessions = entry.get("planned_sessions", [])
        completed = 0
        key_total = sum(1 for session in planned_sessions if session.get("is_key"))
        key_completed = 0
        realized_load = 0.0

        for session in planned_sessions:
            match = _match_planned_session(session, activities_by_date.get(session["date"], []))
            if match:
                completed += 1
                realized_load += match.get("icu_training_load", 0) or 0
                if session.get("is_key"):
                    key_completed += 1

        planned_total = len(planned_sessions)
        planned_tss = entry.get("planned_total_tss", 0) or 0
        completion_rate = round(completed / planned_total, 2) if planned_total else None
        key_completion_rate = round(key_completed / key_total, 2) if key_total else None
        realized_load_pct = round(realized_load / planned_tss, 2) if planned_tss else None

        if key_completion_rate is not None and key_completion_rate >= 0.8:
            verdict = "Planen omsattes starkt i praktiken."
        elif completion_rate is not None and completion_rate < 0.5:
            verdict = "Planen fick låg faktisk efterlevnad."
        else:
            verdict = "Planen gav blandat utfall."

        entry["outcome"] = {
            "completion_rate": completion_rate,
            "key_session_completion_rate": key_completion_rate,
            "realized_load_pct": realized_load_pct,
            "verdict": verdict,
        }

    evaluated = [entry for entry in history if entry.get("outcome")]
    recent = evaluated[-6:]
    if not recent:
        historical_validation = {
            "evaluated_plans": 0,
            "summary": "Historisk validering: ännu inga tidigare planer med färdigt utfall att utvärdera.",
        }
        outcome_tracking = {
            "evaluated_plans": 0,
            "summary": "Outcome tracking: väntar på att tidigare planfönster ska avslutas innan kalibrering kan göras.",
        }
        bucket["historical_validation"] = historical_validation
        bucket["outcome_tracking"] = outcome_tracking
        return historical_validation, outcome_tracking

    avg_completion = round(sum((entry["outcome"].get("completion_rate") or 0) for entry in recent) / len(recent), 2)
    avg_key_completion = round(sum((entry["outcome"].get("key_session_completion_rate") or 0) for entry in recent) / len(recent), 2)
    avg_effectiveness = round(sum((entry.get("scores", {}).get("effectiveness") or 0) for entry in recent) / len(recent), 1)
    avg_confidence = round(sum((entry.get("scores", {}).get("confidence") or 0) for entry in recent) / len(recent), 1)

    simplicity_strong = [
        entry["outcome"].get("key_session_completion_rate")
        for entry in recent
        if (entry.get("scores", {}).get("simplicity") or 0) >= 7
        and entry["outcome"].get("key_session_completion_rate") is not None
    ]
    simplicity_weak = [
        entry["outcome"].get("key_session_completion_rate")
        for entry in recent
        if (entry.get("scores", {}).get("simplicity") or 0) <= 5
        and entry["outcome"].get("key_session_completion_rate") is not None
    ]

    bias_note = "Kalibreringen ser relativt neutral ut."
    if avg_effectiveness >= 8 and avg_key_completion < 0.6:
        bias_note = "Modellen tenderar att överskatta effekt när nyckelpassen inte blir genomförda."
    elif avg_effectiveness <= 5 and avg_key_completion >= 0.75:
        bias_note = "Modellen verkar ibland underskatta vad atleten faktiskt kan absorbera."

    simplicity_note = ""
    if simplicity_strong and simplicity_weak:
        strong_avg = sum(simplicity_strong) / len(simplicity_strong)
        weak_avg = sum(simplicity_weak) / len(simplicity_weak)
        if strong_avg > weak_avg + 0.15:
            simplicity_note = " Enklare planer har hittills gett bättre nyckelpass-compliance."

    historical_validation = {
        "evaluated_plans": len(recent),
        "avg_completion_rate": avg_completion,
        "avg_key_session_completion_rate": avg_key_completion,
        "summary": (
            f"Historisk validering (proxy): {len(recent)} tidigare planfönster utvärderade. "
            f"Snitt efterlevnad {round(avg_completion * 100)}% och nyckelpass {round(avg_key_completion * 100)}%."
            f"{simplicity_note}"
        ),
    }
    outcome_tracking = {
        "evaluated_plans": len(recent),
        "avg_effectiveness": avg_effectiveness,
        "avg_confidence": avg_confidence,
        "summary": (
            f"Outcome tracking: predicted effectiveness {avg_effectiveness}/10 och confidence {avg_confidence}/10 "
            f"har hittills gett {round(avg_key_completion * 100)}% key-session completion. {bias_note}"
        ),
    }

    bucket["historical_validation"] = historical_validation
    bucket["outcome_tracking"] = outcome_tracking
    return historical_validation, outcome_tracking

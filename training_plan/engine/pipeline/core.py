from __future__ import annotations

from training_plan.core.common import *
from training_plan.core.models import AIPlan, PlanDay, PlanReview, PlanScores
from training_plan.engine.ai import call_ai, parse_plan
from training_plan.engine.planning import classify_session_category
from training_plan.engine.postprocess import estimate_tss_coggan

_KEY_PLAN_CATEGORIES = {"ftp_test", "long_ride", "threshold", "vo2"}
# ZONE_INTENSITY imported from training_plan.core.catalogs via common *-import
_CANDIDATE_VARIATIONS = [
    {
        "label": "Candidate A",
        "focus": "Balanced with protected key sessions",
        "instructions": [
            "Build a balanced plan that protects 3-4 key sessions per week.",
            "Use 80/20 polarization: 80% easy endurance, 20% structured intensity.",
            "Include test-and-adjust feedback loops; skip non-critical filler.",
        ],
    },
    {
        "label": "Candidate B",
        "focus": "Conservative recovery-first approach",
        "instructions": [
            "Minimize load: reduce weekly TSS by 15% from budget while hitting must-hit sessions.",
            "Maximize recovery window between hard sessions (2+ days easy minimum).",
            "Emphasize sleep/HRV feedback over aggressive periodization.",
        ],
    },
    {
        "label": "Candidate C",
        "focus": "Race-specific preparation",
        "instructions": [
            "Weight race demands more heavily than the block objective when they conflict.",
            "Use race-specific intensity distribution while respecting all safety rules and vetoes.",
            "Keep risk acceptable; do not trade safety for TSS or specificity.",
        ],
    },
]

_FIRST_ROUND_GENERATION_TEMPERATURE = float(os.getenv("PLAN_FIRST_ROUND_TEMPERATURE", "0.35"))
_REVISION_GENERATION_TEMPERATURE = float(os.getenv("PLAN_REVISION_TEMPERATURE", "0.15"))
_REVIEW_TEMPERATURE = float(os.getenv("PLAN_REVIEW_TEMPERATURE", "0.05"))
_PAIRWISE_TEMPERATURE = float(os.getenv("PLAN_PAIRWISE_TEMPERATURE", "0.05"))
_PAIRWISE_SCORE_MARGIN = int(os.getenv("PLAN_PAIRWISE_SCORE_MARGIN", "1"))
_EARLY_STOP_PATIENCE = int(os.getenv("PLAN_EARLY_STOP_PATIENCE", "2"))
_INVALID_REVIEW_RANK_PENALTY = float(os.getenv("PLAN_INVALID_REVIEW_RANK_PENALTY", "4.0"))
_INVALID_REVIEW_COMPETITIVE_MARGIN = float(os.getenv("PLAN_INVALID_REVIEW_COMPETITIVE_MARGIN", "2.0"))
_DEBUG_PARSE_FAILURES = os.getenv("PLAN_DEBUG_PARSE_FAILURES", "").strip().lower() in {"1", "true", "yes", "on"}
_TSS_GAP_REVISION_MIN_MISSING = int(os.getenv("PLAN_TSS_GAP_REVISION_MIN_MISSING", "120"))
_TSS_GAP_REVISION_MIN_PCT = float(os.getenv("PLAN_TSS_GAP_REVISION_MIN_PCT", "0.90"))
_TSS_DEFICIT_VETO_PCT = float(os.getenv("PLAN_TSS_DEFICIT_VETO_PCT", "0.85"))
_VETO_TRIGGERS = [
    "HARD-EASY", "TAK V", "VOLYMSPÄRR", "VOLYMSPARR", "STYRKEGRÄNS",
    "RULLSKIDSGRÄNS", "ACWR-VETO", "HRV-VETO", "TIDSBUDGET",
    "TIME BUDGET", "TSS-UNDERSKOTT VETO", "TSS-DEFICIT VETO",
]


_DEBUG_AI = os.getenv("LOG_LEVEL", "INFO").upper() == "DEBUG"


def _debug_ai_call(label: str, prompt: str, raw: str) -> None:
    if not _DEBUG_AI:
        return
    preview_prompt = prompt[:3000] + ("\n...[truncated]" if len(prompt) > 3000 else "")
    preview_raw = raw[:3000] + ("\n...[truncated]" if len(raw) > 3000 else "")
    log.debug("---- %s PROMPT ----\n%s\n---- %s PROMPT END ----", label, preview_prompt, label)
    log.debug("---- %s RESPONSE ----\n%s\n---- %s RESPONSE END ----", label, preview_raw, label)


def classify_injury(provider: str, injury_text: str) -> dict | None:
    """Classify a free-text injury description into a structured profile using AI.
    Returns None on failure so callers can fall back to keyword matching."""
    if not injury_text or injury_text.lower() in ("", "nej", "n", "inga", "no"):
        return None
    prompt = f"""You are a sports medicine triage assistant. Classify the athlete's injury description.

Injury description: "{injury_text}"

Return ONLY valid JSON with this exact schema:
{{
  "profile_key": "knee|hip|back|shoulder|calf_achilles|shin|generic",
  "severity": "MILD|MODERATE|SEVERE",
  "double_poling_safe": true,
  "note": "one sentence — what body part, likely cause, and key restriction"
}}

Rules:
- "knee" = knee pain of any kind
- "hip" = hip, glute, or groin pain
- "back" = spine, lower back, or neck
- "shoulder" = shoulder, elbow, or wrist
- "calf_achilles" = calf, achilles, or heel
- "shin" = shin splints or anterior lower leg
- "generic" = unclear or multiple areas
- MILD = activity possible with adaptation | MODERATE = significant restriction | SEVERE = rest required
- double_poling_safe = true if the injury does NOT involve the shoulder/arm/wrist"""

    try:
        raw = call_ai(provider, prompt, temperature=0.0)
        _debug_ai_call("INJURY-CLASSIFY", prompt, raw or "")
        payload = json.loads(raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip())
        if payload.get("profile_key") in {"knee","hip","back","shoulder","calf_achilles","shin","generic"}:
            log.info("⚕️ Injury classified: %s (%s) – %s",
                     payload["profile_key"], payload.get("severity"), payload.get("note"))
            return payload
    except Exception as exc:
        log.warning("Injury classification failed (%s) – falling back to keyword matching.", exc)
    return None


def generate_plan(provider: str, prompt: str, temperature: float | None = None) -> AIPlan:
    raw = call_ai(provider, prompt, temperature=temperature)
    _debug_ai_call("GENERATE", prompt, raw or "")
    return parse_plan(raw)


def _extract_json_payload(raw: str) -> dict:
    clean = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    candidates = [clean]
    
    start_idx = clean.find("{")
    end_idx = clean.rfind("}")
    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        candidates.append(clean[start_idx:end_idx+1])

    last_error: Exception | None = None
    for candidate in candidates:
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return data
            if isinstance(data, list):
                first = next((item for item in data if isinstance(item, dict)), None)
                if first is not None:
                    return first
        except json.JSONDecodeError as exc:
            last_error = exc

    raise ValueError(f"Could not extract JSON object: {last_error}")


def _parse_structured_response(raw: str, model_cls, fallback, label: str):
    try:
        payload = _extract_json_payload(raw)
        parsed = model_cls.model_validate(payload)
        log.info(f"✅ {label} parsed OK")
        return parsed
    except Exception as exc:
        log.warning(f"{label} could not be parsed: {exc}")
        if _DEBUG_PARSE_FAILURES:
            preview = (raw or "").strip()
            if len(preview) > 4000:
                preview = preview[:4000] + "\n...[truncated]"
            log.warning("---- %s raw response start ----", label)
            if preview:
                for line in preview.splitlines():
                    log.warning("%s", line)
            else:
                log.warning("<empty response>")
            log.warning("---- %s raw response end ----", label)
        return fallback


def _is_invalid_review_fallback(review: PlanReview) -> bool:
    return bool(
        review.must_fix
        and "review response was invalid" in review.must_fix[0].lower()
    )


def _weighted_plan_intensity(day: PlanDay) -> float | None:
    if not day.workout_steps:
        return None

    total = sum(step.duration_min for step in day.workout_steps) or 0
    if total <= 0:
        return None

    weighted = 0.0
    for step in day.workout_steps:
        weighted += step.duration_min * ZONE_INTENSITY.get(step.zone.upper(), 0.70)
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



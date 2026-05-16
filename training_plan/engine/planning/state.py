from training_plan.core.common import *
from training_plan.core.models import AppState
from training_plan.engine.libraries import *
from training_plan.engine.utils import safe_date_str
from training_plan.engine.planning.metrics import _weekly_tss_history, _weeks_since_deload

def load_state() -> dict:
    """Load and validate the persistent state file.

    Validates the raw JSON against AppState so that every key has an explicit
    default. Unknown keys from older state files are preserved (extra="allow").
    Returns a plain dict so all existing dict-access patterns work unchanged.
    """
    raw: dict = {}
    if STATE_FILE.exists():
        try:
            raw = json.loads(STATE_FILE.read_text())
        except Exception as exc:
            log.warning("Could not load state file %s: %s. Using defaults.", STATE_FILE, exc)
    try:
        return AppState.model_validate(raw).model_dump()
    except Exception as exc:
        log.warning("State file %s did not match schema: %s. Using defaults.", STATE_FILE, exc)
        return AppState().model_dump()

def save_state(state: dict):
    tmp_state = STATE_FILE.with_suffix(STATE_FILE.suffix + ".tmp")
    tmp_state.write_text(json.dumps(state, indent=2, ensure_ascii=False))
    tmp_state.replace(STATE_FILE)


_FAILURE_MEMORY_LIMIT = 8
_FAILURE_MEMORY_MIN_SCORE = 0.5
_FAILURE_MEMORY_DECAY = 0.85
_FAILURE_CATEGORIES = {
    "complexity_overload": "Plans became too complex and likely hurt execution.",
    "lost_key_sessions": "Important key sessions were weakened, misplaced, or not protected enough.",
    "low_specificity": "The plan drifted away from block objective or race demands.",
    "high_risk_load": "Load/risk balance was too aggressive or physiologically unsafe.",
    "repeated_veto": "Python had to veto the plan repeatedly, meaning the structure was not legal from the start.",
    "low_tss": "Planned load ended up too low relative to budget and objective.",
    "filler_sessions": "The plan contained low-value filler instead of clear purpose.",
    "weak_individualization": "The plan did not adapt well enough to this athlete's profile or constraints.",
    "uncertainty_high": "The plan relied too much on weak or uncertain data.",
}


def _failure_memory_bucket(state: dict) -> dict:
    bucket = state.setdefault("failure_memory", {})
    bucket.setdefault("patterns", {})
    return bucket


def _categorize_failure_signals(trace, changes: list[str]) -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []
    if not trace or not trace.review or not trace.scores:
        return results

    review = trace.review
    scores = trace.scores
    must_fix_text = " ".join(review.must_fix or []).lower()
    advice_text = " ".join(review.coaching_advice or []).lower()
    summary_text = f"{review.summary} {trace.rationale}".lower()
    change_text = " ".join(changes or []).lower()

    if scores.simplicity <= 5 or "filler" in must_fix_text or "filler" in advice_text:
        results.append(("complexity_overload", "Simplicity was weak or filler sessions appeared."))
    if review.key_sessions.rating in ("WEAK", "CRITICAL") or "must-hit" in must_fix_text or "key session" in must_fix_text:
        results.append(("lost_key_sessions", "Key sessions were not protected strongly enough."))
    if scores.specificity <= 5 or review.race_demands.rating in ("WEAK", "CRITICAL"):
        results.append(("low_specificity", "Specificity to block/race demands was too weak."))
    if scores.risk >= 7 or review.load_and_risk.rating in ("WEAK", "CRITICAL"):
        results.append(("high_risk_load", "Risk/load balance was too aggressive or unstable."))
    if any("veto" in c.lower() for c in changes):
        results.append(("repeated_veto", "Python had to veto parts of the plan."))
    if "tss-deficit veto" in change_text or "too low" in must_fix_text or "missing" in must_fix_text and "tss" in must_fix_text:
        results.append(("low_tss", "The plan under-shot the intended load budget."))
    if "filler" in must_fix_text or "filler" in summary_text:
        results.append(("filler_sessions", "The plan included low-value filler sessions."))
    if scores.simplicity <= 5 or review.individualization.rating in ("WEAK", "CRITICAL"):
        results.append(("weak_individualization", "The plan was not individualized enough."))
    if scores.confidence <= 4 or len(review.uncertainty_sources or []) >= 3:
        results.append(("uncertainty_high", "The plan depended on too much uncertainty."))

    deduped = []
    seen = set()
    for key, reason in results:
        if key in seen:
            continue
        seen.add(key)
        deduped.append((key, reason))
    return deduped


def update_failure_memory(state: dict, trace, changes: list[str]) -> dict:
    bucket = _failure_memory_bucket(state)
    patterns = bucket.setdefault("patterns", {})
    today = date.today()
    last_updated = bucket.get("last_updated")
    if last_updated:
        try:
            days_since = max((today - date.fromisoformat(last_updated)).days, 0)
        except Exception:
            days_since = 0
        if days_since > 0:
            decay_factor = _FAILURE_MEMORY_DECAY ** days_since
            for item in patterns.values():
                item["score"] = round(item.get("score", 0.0) * decay_factor, 3)

    for category, reason in _categorize_failure_signals(trace, changes):
        item = patterns.setdefault(category, {
            "score": 0.0,
            "count": 0,
            "last_seen": today.isoformat(),
            "example": reason,
            "label": _FAILURE_CATEGORIES.get(category, category),
        })
        item["score"] = round(item.get("score", 0.0) + 1.0, 3)
        item["count"] = item.get("count", 0) + 1
        item["last_seen"] = today.isoformat()
        item["example"] = reason
        item["label"] = _FAILURE_CATEGORIES.get(category, category)

    filtered = {
        key: value for key, value in patterns.items()
        if value.get("score", 0.0) >= _FAILURE_MEMORY_MIN_SCORE
    }
    ranked = sorted(
        filtered.items(),
        key=lambda kv: (-kv[1].get("score", 0.0), -kv[1].get("count", 0), kv[0]),
    )[:_FAILURE_MEMORY_LIMIT]
    bucket["patterns"] = {key: value for key, value in ranked}
    bucket["last_updated"] = today.isoformat()
    return bucket


def format_failure_memory(memory: dict) -> str:
    if not memory:
        return ""
    patterns = memory.get("patterns", {})
    if not patterns:
        return ""
    ranked = sorted(
        patterns.items(),
        key=lambda kv: (-kv[1].get("score", 0.0), -kv[1].get("count", 0), kv[0]),
    )[:3]
    if not ranked:
        return ""
    lines = ["FAILURE MEMORY (recent recurring planning mistakes to avoid):"]
    for key, item in ranked:
        if item.get("count", 0) < 2 and item.get("score", 0.0) < 1.5:
            continue
        lines.append(
            f"  - Avoid {key}: {item.get('label', key)} "
            f"(score {round(item.get('score', 0.0), 1)}, seen {item.get('count', 0)}x). "
            f"Recent example: {item.get('example', '')}"
        )
    return "\n".join(lines) if len(lines) > 1 else ""

def is_ai_generated(w):
    return AI_TAG in (w.get("description") or "")

# ══════════════════════════════════════════════════════════════════════════════
# 1 & 5. MESOCYCLE PERIODIZATION + AUTO DELOAD
# ══════════════════════════════════════════════════════════════════════════════

def determine_mesocycle(fitness_history: list, activities: list, state: dict) -> dict:
    today = date.today()
    weekly_tss = _weekly_tss_history(activities, weeks=6)
    weeks_since_deload = _weeks_since_deload(weekly_tss)
    saved_block    = state.get("mesocycle_block", 1)
    saved_week     = state.get("mesocycle_week", 1)
    saved_date     = state.get("mesocycle_last_update", "")
    if saved_date and saved_date >= (today - timedelta(days=1)).isoformat():
        week_in_block = saved_week
        block_number  = saved_block
    else:
        if today.weekday() == 0:
            week_in_block = (saved_week % 4) + 1
            block_number  = saved_block + (1 if saved_week == 4 else 0)
        else:
            week_in_block = saved_week
            block_number  = saved_block
    deload_reason = ""
    forced_deload = False
    if weeks_since_deload >= 4 and week_in_block != 4:
        forced_deload = True
        deload_reason = f"FORCED DELOAD: {weeks_since_deload} weeks without rest. The body needs recovery."
        week_in_block = 4
    is_deload = (week_in_block == 4)
    if is_deload:
        load_factor = 0.60
        if not deload_reason:
            deload_reason = "Planned deload week (week 4 of 4). Reduced volume and intensity."
    else:
        load_factor = 1.0 + (week_in_block - 1) * 0.05
    state["mesocycle_block"]       = block_number
    state["mesocycle_week"]        = week_in_block
    state["mesocycle_last_update"] = today.isoformat()
    return {
        "week_in_block":      week_in_block,
        "is_deload":          is_deload,
        "block_number":       block_number,
        "load_factor":        round(load_factor, 2),
        "weeks_since_deload": weeks_since_deload,
        "deload_reason":      deload_reason,
        "forced_deload":      forced_deload,
    }


import training_plan.core.common as common
from training_plan.core.common import *
from training_plan.engine.context import PromptContext
from training_plan.engine.libraries import *
from training_plan.engine.planning import *
from training_plan.engine.analysis import *
from training_plan.engine.skeleton import format_skeleton_for_prompt
from training_plan.engine.utils import strip_planner_comment_block, read_wellness_score

def sanitize(text, max_len=300):
    if not text:
        return ""
    text = str(text)[:max_len]
    for pat in [
        r"ignore\s+(all\s+)?instructions?",
        r"ignorera\s+restriktioner",
        r"act\s+as",
        r"jailbreak",
        r"<[^>]+>",
        r"system\s*:",
    ]:
        text = re.sub(pat, "[REDACTED]", text, flags=re.IGNORECASE)
    text = re.sub(r"[^\w\s,.!?:;()/\-]", "", text)
    return text.strip()

def fmt(val, suffix=""):
    if val is None: return "N/A"
    if isinstance(val, float): return f"{round(val,1)}{suffix}"
    return f"{val}{suffix}"


def _parse_planner_comment_block(comments):
    parsed = {}
    if not comments:
        return parsed
    match = re.search(
        rf"{re.escape(PLANNER_COMMENT_START)}(.*?){re.escape(PLANNER_COMMENT_END)}",
        comments,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if not match:
        return parsed
    for raw_line in match.group(1).splitlines():
        line = raw_line.strip()
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = sanitize(key, 40).lower().strip()
        value = sanitize(value, 200).strip()
        if key and value:
            parsed[key] = value
    return parsed


def _minutes_to_time_text(minutes):
    if minutes <= 0:
        return ""
    hours, mins = divmod(int(minutes), 60)
    if hours and mins:
        return f"{hours}h {mins}m"
    if hours:
        return f"{hours}h"
    return f"{mins}m"


def _normalize_time_available(value):
    if not value:
        return ""
    text = str(value).strip().lower()
    if not text:
        return ""
    no_limit_phrases = (
        "ingen begr",
        "ingen tids",
        "obegr",
        "unlimited",
        "no limit",
        "fri tid",
        "fritt",
    )
    if any(phrase in text for phrase in no_limit_phrases):
        return ""
    normalized = (
        text.replace("timmar", "h")
        .replace("timme", "h")
        .replace("hours", "h")
        .replace("hour", "h")
        .replace("hrs", "h")
        .replace("hr", "h")
        .replace("minuter", "m")
        .replace("minutes", "m")
        .replace("minute", "m")
        .replace("mins", "m")
        .replace("min", "m")
    )
    hours_match = re.search(r"(\d+(?:[.,]\d+)?)\s*h(?:\s*(\d+)\s*m)?", normalized)
    if hours_match:
        minutes = round(float(hours_match.group(1).replace(",", ".")) * 60)
        if hours_match.group(2):
            minutes += int(hours_match.group(2))
        return _minutes_to_time_text(minutes)
    mins_match = re.search(r"(\d+)\s*m", normalized)
    if mins_match:
        return _minutes_to_time_text(int(mins_match.group(1)))
    if normalized.isdigit():
        return normalized
    return sanitize(value, 20)


def _extract_time_available_from_comments(comments):
    if not comments:
        return None
    text = comments.lower()
    no_limit_patterns = (
        r"ingen\s+tids?(?:begransning|grans|limit)",
        r"no\s+time\s+limit",
        r"unlimited",
        r"fri\s+tid",
        r"obegr[a-z]*\s+tid",
    )
    for pattern in no_limit_patterns:
        if re.search(pattern, text):
            return ""
    time_patterns = (
        r"(?:tid(?:\s+idag)?|time(?:\s+today)?|max(?:\s+tid)?|time\s+limit|available|tillg[a-z]*lig(?:\s+tid)?|kan\s+bara(?:\s+tr[a-z]+)?|bara|endast)[^0-9]{0,20}(\d+(?:[.,]\d+)?\s*h(?:\s*\d+\s*m)?|\d+\s*m)",
        r"(\d+(?:[.,]\d+)?\s*h(?:\s*\d+\s*m)?|\d+\s*m)\s*(?:max|totalt|available|tillg[a-z]*ligt?|tid)",
    )
    for pattern in time_patterns:
        match = re.search(pattern, text)
        if match:
            return _normalize_time_available(match.group(1))
    return None


def _read_wellness_injury(today_wellness):
    if not today_wellness:
        return None
    for key in ("injury", "Injury"):
        value = today_wellness.get(key)
        if value in (None, "", 0, "0"):
            continue
        try:
            score = int(float(value))
        except (TypeError, ValueError):
            text = sanitize(str(value), 150)
            return text or None
        if score <= 1:
            return None
        return f"Wellness injury score {score}/4"
    return None

# ══════════════════════════════════════════════════════════════════════════════
# PROMPT
# ══════════════════════════════════════════════════════════════════════════════

def morning_questions(auto, today_wellness, yesterday_planned, yesterday_actuals):
    raw_comments = (today_wellness or {}).get("comments", "")
    structured_comments = _parse_planner_comment_block(raw_comments)
    free_comments = strip_planner_comment_block(raw_comments)
    comment_time = _extract_time_available_from_comments(free_comments)
    structured_time = _normalize_time_available(
        structured_comments.get("time_available") or structured_comments.get("time") or ""
    )
    existing_time = structured_time if comment_time is None else comment_time
    existing_stress = read_wellness_score(today_wellness, ("stress", "Stress"), default=1)
    existing_injury = (
        structured_comments.get("injury")
        or structured_comments.get("injury_today")
        or _read_wellness_injury(today_wellness)
    )
    existing_note = structured_comments.get("athlete_note") or structured_comments.get("note") or ""

    note_parts = []
    clean_free_comments = sanitize(free_comments, 250)
    if clean_free_comments:
        note_parts.append(clean_free_comments)
    if existing_note and existing_note not in note_parts:
        note_parts.append(existing_note)

    answers = {
        "life_stress": existing_stress,
        "injury_today": existing_injury,
        "athlete_note": " | ".join(note_parts),
        "time_available": existing_time or "",
    }

    if auto:
        if yesterday_planned and is_ai_generated(yesterday_planned):
            answers["yesterday_completed"] = len(yesterday_actuals) > 0 if yesterday_actuals else False
        return answers

    print("\n" + "-"*50 + "\n  MORNING CHECK\n" + "-"*50)
    if yesterday_planned and is_ai_generated(yesterday_planned):
        name = yesterday_planned.get("name","training")
        if yesterday_actuals:
            a = yesterday_actuals[0]
            dur = round((a.get("moving_time") or a.get("elapsed_time") or 0)/60)
            print(f"\nYesterday: {name} | Completed: {a.get('type','?')}, {dur}min, TSS {a.get('icu_training_load','?')}")
            q = input("How did it feel? (good/okay/heavy/too easy) [good]: ").strip() or "good"
            answers["yesterday_feeling"] = sanitize(q, 50)
            answers["yesterday_completed"] = True
        else:
            print(f"\nYesterday planned: {name} - no activity found.")
            r = input("Why? (sick/tired/lack of time/other): ").strip()
            answers["yesterday_missed_reason"] = sanitize(r, 100)
            answers["yesterday_completed"] = False

    time_label = existing_time or "no limit"
    entered_time = input(f"\nTime for training today? [{time_label}]: ").strip()
    answers["time_available"] = existing_time if not entered_time else _normalize_time_available(entered_time)

    entered_stress = input(f"Life stress (1-4) [{existing_stress}]: ").strip()
    try:
        answers["life_stress"] = max(1, min(4, int(entered_stress))) if entered_stress else existing_stress
    except Exception:
        answers["life_stress"] = existing_stress

    injury_label = existing_injury or "no"
    entered_injury = input(f"Pains/injuries? (no/describe) [{injury_label}]: ").strip()
    if not entered_injury:
        answers["injury_today"] = existing_injury
    elif entered_injury.lower() in ("no", "n", "nej"):
        answers["injury_today"] = None
    else:
        answers["injury_today"] = sanitize(entered_injury, 150)

    entered_note = input("other note to the coach (optional, '-' clears): ").strip()
    if entered_note == "":
        answers["athlete_note"] = existing_note
    elif entered_note == "-":
        answers["athlete_note"] = ""
    else:
        answers["athlete_note"] = sanitize(entered_note, 200)

    print("-"*50)
    return answers



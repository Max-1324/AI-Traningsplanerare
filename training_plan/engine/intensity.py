"""Hard sessions: how many a week gets and which kinds.

The baseline is the one most endurance research and coaching practice agree on:
two hard sessions a week, one around threshold and one at VO2max, the rest easy
(Seiler 2010; Stöggl & Sperlich 2014). More training time adds easy volume, not
more hard sessions, so the rule fits athletes of every level.

Around that baseline the count follows the athlete's own data, relative to their
own baseline (HRV) and fitness (form as % of CTL):

- 0 in recovery weeks; race and taper weeks keep their own handling.
- 1 when recovery looks poor (HRV below baseline, form below -30% of CTL,
  burnout risk, return to play, recently sick), when many key sessions were
  missed, or when the week has at most two training days.
- 3 when two a week have gone well for most of the last four weeks and the week
  has room for it (six training days and about ten hours).

``KEY_SESSIONS_PER_WEEK`` is only an upper limit.

The phase decides the format, not whether a kind appears: base weeks use tempo
and short VO2max intervals, build weeks classic threshold and longer VO2max
intervals. Without an annual plan or a race the emphasis alternates every
six weeks, so the VO2max format changes before it stops working.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, timedelta

MAX_HARD_SESSIONS = 3
# Optional upper limit; unset means the adaptive count decides (at most 3).
KEY_SESSIONS_CAP = max(0, min(int(os.getenv("KEY_SESSIONS_PER_WEEK") or MAX_HARD_SESSIONS), MAX_HARD_SESSIONS))
DEFAULT_HARD_SESSIONS = min(2, KEY_SESSIONS_CAP)

_THREE_MIN_DAYS = 6
_THREE_MIN_HOURS = 10.0
_FORM_LOW = -0.30       # form below -30% of CTL: high risk zone in intervals.icu
_FORM_OK_FOR_MORE = -0.20
_EMPHASIS_BLOCK_WEEKS = 6

HARD_CATEGORIES = {"threshold", "vo2", "ftp_test"}

# Session formats per emphasis (keys in WORKOUT_LIBRARY / the planner's generic sessions).
_VO2 = {"base": "vo2max_short", "build": "vo2max_intervals"}
_THRESHOLD = {"base": "tempo_sustained", "build": "threshold_intervals"}
_THIRD = {"base": "threshold_intervals", "build": "tempo_sustained"}
_FOCUS_TO_KIND = {
    "threshold": "threshold", "polarization": "threshold", "vo2": "vo2",
    "pacing": "tempo", "durability": "tempo", "fueling": "tempo",
}
_KIND_OF = {
    "vo2max_intervals": "vo2", "vo2max_short": "vo2",
    "threshold_intervals": "threshold", "tempo_sustained": "threshold",  # tempo counts as threshold work
}


@dataclass
class IntensitySignals:
    """Recovery and history signals, all relative to the athlete's own data."""
    hrv_state: str | None = None          # calculate_hrv: 7-day ln(rMSSD) vs own baseline
    ctl: float = 0.0
    tsb: float | None = None
    burnout: bool = False
    rtp_active: bool = False
    recently_sick: bool = False
    key_planned: int = 0                  # planned key sessions, last 28 days
    key_missed: int = 0
    hard_by_week: list[int] = field(default_factory=list)   # last completed weeks, oldest first


def _form_ratio(signals: IntensitySignals) -> float | None:
    if signals.tsb is None or signals.ctl < 10:
        return None
    return signals.tsb / signals.ctl


def hard_sessions_for_week(target, signals: IntensitySignals, *, trainable_days: int = 7,
                           hours: float | None = None) -> tuple[int, str]:
    """(hard sessions, reason) for one week. Race, taper and recovery weeks keep their own count."""
    if target.kind != "build":
        return target.max_key_sessions, ""

    form = _form_ratio(signals)
    lower = []
    if signals.hrv_state == "LOW":
        lower.append("HRV below your baseline")
    if form is not None and form < _FORM_LOW:
        lower.append(f"form {form:.0%} of CTL")
    if signals.burnout:
        lower.append("burnout risk")
    if signals.rtp_active:
        lower.append("return to play")
    if signals.recently_sick:
        lower.append("recently sick")
    if signals.key_planned >= 3 and signals.key_missed / signals.key_planned > 0.4:
        lower.append(f"{signals.key_missed}/{signals.key_planned} key sessions missed lately")
    if trainable_days <= 2:
        lower.append(f"only {trainable_days} training days")
    if lower:
        return min(1, KEY_SESSIONS_CAP), "1 hard session: " + ", ".join(lower)

    recent = signals.hard_by_week[-4:]
    handled_two = len(recent) == 4 and sum(1 for n in recent if n >= 2) >= 3   # one recovery week allowed
    roomy = trainable_days >= _THREE_MIN_DAYS and (hours or 0) >= _THREE_MIN_HOURS
    recovering_well = (signals.hrv_state in (None, "NORMAL", "HIGH")
                       and (form is None or form >= _FORM_OK_FOR_MORE))
    if handled_two and roomy and recovering_well and KEY_SESSIONS_CAP >= 3:
        return 3, "3 hard sessions: two a week have gone well for four weeks and the week has room"
    return DEFAULT_HARD_SESSIONS, ""


def hard_sessions_by_week(activities: list, today: date, classify, weeks: int = 4) -> list[int]:
    """Days with a hard session in each of the last `weeks` full calendar weeks, oldest first."""
    monday = today - timedelta(days=today.weekday())
    counts = []
    for i in range(weeks, 0, -1):
        start = (monday - timedelta(days=7 * i)).isoformat()
        end = (monday - timedelta(days=7 * (i - 1))).isoformat()
        days = {(a.get("start_date_local") or "")[:10] for a in activities
                if start <= (a.get("start_date_local") or "")[:10] < end and classify(a) in HARD_CATEGORIES}
        counts.append(len(days))
    return counts


def emphasis_for_phase(phase: str | None) -> str | None:
    """'base' or 'build' for a phase name (annual plan or race-driven), None if unknown."""
    name = (phase or "").lower()
    if any(word in name for word in ("build", "peak", "race", "taper", "specific", "speciali")):
        return "build"
    if any(word in name for word in ("base", "prep", "general", "transition", "recovery")):
        return "base"
    return None


def assign_emphasis(week_start: str, *, atp_phase: str | None = None, race_phase: str | None = None,
                    has_race: bool = False) -> str:
    """Session format for a week: from the annual plan, a coming race, or a six-week rotation."""
    emphasis = emphasis_for_phase(atp_phase)
    if emphasis:
        return emphasis
    if race_phase in ("Build", "Taper", "Race Week"):
        return "build"
    if has_race:
        return "base"
    block = (date.fromisoformat(week_start).toordinal() // 7) // _EMPHASIS_BLOCK_WEEKS
    return "base" if block % 2 == 0 else "build"


def key_session_types(n: int, *, emphasis: str = "build", week_start: str = "", kind: str = "build",
                      focus_areas: list | None = None, done_kinds: list | None = None) -> list[str]:
    """Workout keys for the week's key-session slots, in order (first slot first).

    Every week with two or more slots gets one VO2max and one threshold-type session.
    """
    if n <= 0:
        return []
    if kind in ("taper", "race"):
        types = ["threshold_intervals", "vo2max_intervals"]
    else:
        emphasis = emphasis if emphasis in _VO2 else "build"
        vo2, threshold = _VO2[emphasis], _THRESHOLD[emphasis]
        focus = next((_FOCUS_TO_KIND[a] for a in focus_areas or [] if a in _FOCUS_TO_KIND), None)
        if n == 1:
            if focus == "vo2":
                types = [vo2, threshold]
            elif focus in ("threshold", "tempo"):
                types = [threshold, vo2]
            else:
                # Alternate week by week so neither kind disappears.
                even = week_start and (date.fromisoformat(week_start).toordinal() // 7) % 2 == 0
                types = [vo2, threshold] if even else [threshold, vo2]
        else:
            third = _THIRD[emphasis]
            if focus == "tempo" and "tempo_sustained" not in (vo2, threshold):
                third = "tempo_sustained"
            types = [vo2, threshold, third]
    # Kinds already done this week go last, so the rest of the week gets the missing kind.
    done = set(done_kinds or [])
    if done:
        types = [t for t in types if _KIND_OF.get(t) not in done] + [t for t in types if _KIND_OF.get(t) in done]
    return types

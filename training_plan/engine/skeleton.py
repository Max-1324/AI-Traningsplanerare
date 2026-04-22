"""Weekly slot skeleton — pre-computes a FIXED/GUIDED/OPEN structure for the planning horizon.

The skeleton is computed from deterministic inputs (mesocycle, readiness, race
proximity, locked dates) and injected into the AI prompt as a structured
constraint table.  The AI is expected to:
  - Honour FIXED slots exactly (locked dates, illness, RTP).
  - Follow GUIDED suggestions unless an explicit training reason justifies the
    deviation (e.g. back-to-back intensity for a training-camp simulation).
    The rationale must appear in the session description so the reviewer can
    evaluate it.
  - Fill OPEN slots freely based on context.

This eliminates the most common class of HARD-EASY vetos: the AI placing two
intensity sessions on consecutive days without realising it, which then gets
corrected reactively by postprocess.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date as _date
from typing import Literal

SlotType = Literal["intensity", "easy", "rest_or_easy", "long_endurance"]
Tier = Literal["FIXED", "GUIDED", "OPEN"]


@dataclass
class DaySlot:
    date: str
    tier: Tier
    suggested_type: SlotType | None = None
    tss_range: tuple[int, int] | None = None
    reason: str = ""


def build_week_skeleton(
    dates: list[str],
    mesocycle: dict,
    readiness: dict,
    race_week: dict | None,
    locked_dates: set[str],
    rtp_status: dict | None = None,
) -> list[DaySlot]:
    """Return a DaySlot for every date in the planning horizon.

    Algorithm
    ---------
    1. Derive a per-week intensity budget from mesocycle week and readiness.
    2. Walk each date in order, maintaining state (last_was_intensity, weekly
       intensity count).
    3. Assign tier and suggested_type:
       - FIXED  → locked by manual workout, RTP protocol, or illness.
       - GUIDED → computed suggestion; AI should follow unless explicitly
                  justified.
       - OPEN   → AI has full freedom (supporting-volume days).
    4. Hard constraints from postprocess.py (hard-easy rule) become the
       DEFAULT rather than a retroactive correction.
    """
    is_deload = mesocycle.get("is_deload", False)
    readiness_score: float = (readiness or {}).get("score", 50)
    race_active = (race_week or {}).get("is_active", False)
    days_to_race: int = (race_week or {}).get("days_to_race", 999)
    is_rtp = (rtp_status or {}).get("is_active", False)

    # ── Maximum intensity sessions per week ───────────────────────────────────
    if is_deload or is_rtp or (race_active and days_to_race <= 3):
        max_intensity_per_week = 0  # pure easy/rest
    elif readiness_score < 45:
        max_intensity_per_week = 1
    elif readiness_score < 65:
        max_intensity_per_week = 2
    else:
        max_intensity_per_week = 3

    slots: list[DaySlot] = []
    intensity_by_week: dict[int, int] = {}
    last_was_intensity = False

    for date_str in dates:
        try:
            d = _date.fromisoformat(date_str)
        except ValueError:
            continue

        week_num = d.isocalendar()[1]
        dow = d.weekday()  # 0 = Monday, 6 = Sunday
        week_intensity = intensity_by_week.get(week_num, 0)
        can_be_intensity = (
            week_intensity < max_intensity_per_week and not last_was_intensity
        )

        # ── FIXED: locked by a manual workout ─────────────────────────────────
        if date_str in locked_dates:
            slots.append(
                DaySlot(
                    date=date_str,
                    tier="FIXED",
                    reason="manual workout already scheduled",
                )
            )
            last_was_intensity = False
            continue

        # ── FIXED: Return-to-Play protocol overrides the week ─────────────────
        if is_rtp:
            slots.append(
                DaySlot(
                    date=date_str,
                    tier="FIXED",
                    suggested_type="easy",
                    tss_range=(20, 55),
                    reason="Return-to-Play protocol: progressive easy build",
                )
            )
            last_was_intensity = False
            continue

        # ── Deload week or final race-week taper: everything easy/rest ─────────
        if is_deload or (race_active and days_to_race <= 3):
            if dow == 6:  # Sunday: allow a slightly longer easy ride
                slots.append(
                    DaySlot(
                        date=date_str,
                        tier="GUIDED",
                        suggested_type="easy",
                        tss_range=(50, 90),
                        reason="deload/race-week: easy long-ish endurance",
                    )
                )
            else:
                slots.append(
                    DaySlot(
                        date=date_str,
                        tier="GUIDED",
                        suggested_type="rest_or_easy",
                        tss_range=(0, 50),
                        reason="deload/race-week: rest or Z1/Z2 only",
                    )
                )
            last_was_intensity = False
            continue

        # ── Day after intensity: mandatory easy ───────────────────────────────
        if last_was_intensity:
            slots.append(
                DaySlot(
                    date=date_str,
                    tier="GUIDED",
                    suggested_type="easy",
                    tss_range=(30, 65),
                    reason="recovery day after intensity — hard-easy rule",
                )
            )
            last_was_intensity = False
            continue

        # ── Sunday: long endurance ────────────────────────────────────────────
        if dow == 6:
            slots.append(
                DaySlot(
                    date=date_str,
                    tier="GUIDED",
                    suggested_type="long_endurance",
                    tss_range=(100, 220),
                    reason="weekend long session slot",
                )
            )
            last_was_intensity = False
            continue

        # ── Monday: recover from weekend load ────────────────────────────────
        if dow == 0:
            slots.append(
                DaySlot(
                    date=date_str,
                    tier="GUIDED",
                    suggested_type="rest_or_easy",
                    tss_range=(0, 50),
                    reason="Monday recovery from weekend",
                )
            )
            last_was_intensity = False
            continue

        # ── Primary intensity slots: Tue / Thu / Sat ──────────────────────────
        if can_be_intensity and dow in (1, 3, 5):
            slots.append(
                DaySlot(
                    date=date_str,
                    tier="GUIDED",
                    suggested_type="intensity",
                    tss_range=(65, 115),
                    reason=(
                        f"primary intensity slot "
                        f"(week budget: {max_intensity_per_week}/week)"
                    ),
                )
            )
            intensity_by_week[week_num] = week_intensity + 1
            last_was_intensity = True
            continue

        # ── All remaining days: open to AI ────────────────────────────────────
        slots.append(
            DaySlot(
                date=date_str,
                tier="OPEN",
                reason="supporting volume or rest — AI decides based on load and context",
            )
        )
        last_was_intensity = False

    return slots


def format_skeleton_for_prompt(slots: list[DaySlot]) -> str:
    """Render the skeleton as a compact table for inclusion in the AI prompt."""
    if not slots:
        return ""

    lines = [
        "WEEKLY SLOT SKELETON (pre-computed structure):",
        "  FIXED = non-negotiable | GUIDED = strong suggestion | OPEN = AI decides freely",
        "  Override rule: GUIDED slots CAN be changed (e.g. back-to-back intensity for a",
        "  training-camp simulation), but ONLY with an explicit rationale in the session",
        "  description. The reviewer will penalise unexplained deviations.",
        "",
    ]
    for s in slots:
        type_str = (s.suggested_type or "open").upper().replace("_", " ")
        tss_str = f"  TSS {s.tss_range[0]}-{s.tss_range[1]}" if s.tss_range else ""
        lines.append(
            f"  {s.date} | {s.tier:<6} | {type_str:<16}{tss_str:<18} — {s.reason}"
        )

    return "\n".join(lines)

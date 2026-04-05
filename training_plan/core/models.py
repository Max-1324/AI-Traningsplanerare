import re
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

from training_plan.core.catalogs import VALID_TYPES


class WorkoutStep(BaseModel):
    duration_min: int = Field(ge=0)
    zone: str
    description: str


class StrengthStep(BaseModel):
    exercise: str
    sets: int = Field(ge=1)
    reps: str
    rest_sec: Optional[int] = None
    notes: Optional[str] = None


class ManualNutrition(BaseModel):
    date: str
    nutrition: str


class PlanDay(BaseModel):
    date: str
    title: str
    intervals_type: str = "Rest"
    duration_min: int = Field(default=0, ge=0)
    distance_km: float = 0.0
    description: str = ""
    nutrition: str = ""
    workout_steps: list[WorkoutStep] = Field(default_factory=list)
    strength_steps: list[StrengthStep] = Field(default_factory=list)
    slot: Literal["AM", "PM", "MAIN"] = "MAIN"
    vetoed: bool = False

    @field_validator("intervals_type")
    @classmethod
    def valid_sport(cls, value: str) -> str:
        return value if value in VALID_TYPES else "Rest"

    @field_validator("date")
    @classmethod
    def valid_date(cls, value: str) -> str:
        datetime.strptime(value, "%Y-%m-%d")
        return value

    @field_validator("strength_steps", mode="before")
    @classmethod
    def coerce_strength_steps(cls, value):
        if not isinstance(value, list):
            return []

        result = []
        for item in value:
            if not isinstance(item, dict):
                continue
            if "exercise" in item and "sets" in item and "reps" in item:
                result.append(item)
                continue

            desc = item.get("description", "") or ""
            sets_match = re.search(r"(\d+)\s*[x×]\s*(\d+(?:-\d+)?)", desc)
            if sets_match:
                result.append(
                    {
                        "exercise": desc.split(".")[0][:50] or "Övning",
                        "sets": int(sets_match.group(1)),
                        "reps": sets_match.group(2),
                        "rest_sec": 60,
                        "notes": desc,
                    }
                )
            else:
                result.append(
                    {
                        "exercise": desc[:50] if desc else "Övning",
                        "sets": 3,
                        "reps": "10-15",
                        "rest_sec": 60,
                        "notes": desc,
                    }
                )
        return result


class AIPlan(BaseModel):
    stress_audit: str
    summary: str
    yesterday_feedback: str = ""
    weekly_feedback: str = ""
    manual_workout_nutrition: list[ManualNutrition] = Field(default_factory=list)
    days: list[PlanDay]

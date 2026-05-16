from training_plan.core.common import *

def parse_plan(raw: str) -> AIPlan:
    clean = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    candidates = [clean]
    
    start_idx = clean.find("{")
    end_idx = clean.rfind("}")
    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        candidates.append(clean[start_idx:end_idx+1])
        
    for candidate in candidates:
        try:
            data = json.loads(candidate)
            # Om modellen returnerar en array, ta första elementet om det är ett objekt
            if isinstance(data, list):
                data = next((item for item in data if isinstance(item, dict)), None)
                if data is None:
                    continue
            # Remap common model alias: gemma/llama returnerar ibland "daily_plan" istället för "days"
            if "daily_plan" in data and "days" not in data:
                data["days"] = data.pop("daily_plan")
            # Rensa AI_TAG om den läckt in i textfält
            for field in ("yesterday_feedback", "weekly_feedback", "summary", "stress_audit"):
                if field in data and isinstance(data[field], str):
                    data[field] = data[field].replace(AI_TAG, "").strip()
                    
            # Säkerställ att 'reps' är en sträng (vissa LLMs returnerar int, t.ex. 20 istället för "20")
            if "days" in data and isinstance(data["days"], list):
                for day in data["days"]:
                    if "strength_steps" in day and isinstance(day["strength_steps"], list):
                        for step in day["strength_steps"]:
                            if "reps" in step and isinstance(step["reps"], int):
                                step["reps"] = str(step["reps"])
                                
            plan = AIPlan(**data)
            n_days = len(plan.days)
            n_steps = sum(len(d.workout_steps) for d in plan.days)
            log.info(f"✅ AI plan parsed and validated OK ({n_days} days, {n_steps} workout steps)")
            return plan
        except json.JSONDecodeError:
            continue
        except ValidationError as e:
            log.warning(f"Schema validation: {e}")
            try:
                if isinstance(data, dict):
                    data.setdefault("stress_audit", "Not calculated by AI")
                    data.setdefault("summary", "Plan generated")
                    data.setdefault("yesterday_feedback", "")
                    data.setdefault("weekly_feedback", "")
                    data.setdefault("days", [])
                    return AIPlan(**data)
            except Exception:
                pass
            continue
    preview = (raw or "")[:500]
    log.error("❌ Could not parse AI response into AIPlan JSON.")
    log.warning(f"Raw AI response (first 500 chars):\n{preview}")
    raise ValueError("AI response could not be parsed into a valid AIPlan.")


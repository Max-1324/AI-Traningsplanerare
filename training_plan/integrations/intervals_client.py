from training_plan.core.common import *
from training_plan.engine.calendar_context import is_context_event
from training_plan.engine.utils import is_race_event, race_priority

def icu_get(path, params=None):
    r = requests.get(f"{BASE}{path}", auth=AUTH, params=params or {}, timeout=15)
    r.raise_for_status()
    return r.json()

def fetch_athlete():
    return icu_get(f"/athlete/{ATHLETE_ID}")

def fetch_activities(days):
    return icu_get(f"/athlete/{ATHLETE_ID}/activities", {
        "oldest": (date.today() - timedelta(days=days)).isoformat(),
        "fields": ("name,type,start_date_local,distance,moving_time,elapsed_time,"
                   "icu_training_load,average_heartrate,"
                   "max_heartrate,icu_weighted_avg_watts,icu_intensity,"
                   "icu_zone_times,icu_hr_zone_times,perceived_exertion,feel"),
    })

def fetch_wellness(days):
    return icu_get(f"/athlete/{ATHLETE_ID}/wellness", {
        "oldest": (date.today() - timedelta(days=days)).isoformat(),
        "newest": date.today().isoformat(),
    })

def fetch_fitness(days):
    return fitness_from_wellness(fetch_wellness(days))

def fitness_from_wellness(wellness):
    """CTL/ATL/TSB per day from the wellness response (no extra API call needed)."""
    fitness = []
    for w in wellness:
        atl = w.get("icu_atl") or w.get("atl")
        ctl = w.get("icu_ctl") or w.get("ctl")
        if atl is not None and ctl is not None:
            fitness.append({
                "date": w.get("id", ""),
                "atl":  atl,
                "ctl":  ctl,
                "tsb":  ctl - atl,
            })
    return fitness

def fetch_planned_workouts(horizon):
    return icu_get(f"/athlete/{ATHLETE_ID}/events", {
        "oldest": date.today().isoformat(),
        "newest": (date.today() + timedelta(days=horizon)).isoformat(),
    })

def fetch_all_planned_events(days_back=28, days_forward=0):
    return icu_get(f"/athlete/{ATHLETE_ID}/events", {
        "oldest": (date.today() - timedelta(days=days_back)).isoformat(),
        "newest": (date.today() + timedelta(days=days_forward)).isoformat(),
    })

def fetch_calendar_context(days_back=60, days_ahead=35):
    """Annual training plan (PLAN/TARGET/ATP notes) and SICK/INJURED/HOLIDAY events around today.

    Looks back as well, so phase blocks and absences that started earlier are included.
    """
    evts = icu_get(f"/athlete/{ATHLETE_ID}/events", {
        "oldest": (date.today() - timedelta(days=days_back)).isoformat(),
        "newest": (date.today() + timedelta(days=days_ahead)).isoformat(),
    })
    return [e for e in evts if is_context_event(e)]

def fetch_races(days_ahead=180):
    try:
        evts = icu_get(f"/athlete/{ATHLETE_ID}/events", {
            "oldest": date.today().isoformat(),
            "newest": (date.today() + timedelta(days=days_ahead)).isoformat(),
        })
        return [e for e in evts if is_race_event(e)]
    except Exception:
        return []

def get_taper_config(races: list, today: date) -> dict:
    """Hittar nästa tävling och bestämmer taper-längd baserat på prioritet i namnet (A/B/C)."""
    future_races = []
    for race in races:
        start_date = (race.get("start_date_local") or "")[:10]
        try:
            race_date = datetime.strptime(start_date, "%Y-%m-%d").date()
        except ValueError:
            log.warning("Skipping race with invalid start_date_local: %s", race.get("start_date_local"))
            continue
        if race_date >= today:
            future_races.append(race)
    future_races.sort(key=lambda r: r.get("start_date_local", ""))
    if not future_races:
        return {"race": None, "taper_days": 14, "race_date": None}
    next_race = future_races[0]
    race_date_obj = datetime.strptime(next_race["start_date_local"][:10], "%Y-%m-%d").date()
    taper_days = {"A": 14, "B": 7, "C": 3}[race_priority(next_race)]
    return {"race": next_race, "taper_days": taper_days, "race_date": race_date_obj}

def fetch_yesterday_actual(activities):
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    return [a for a in activities if a.get("start_date_local","")[:10] == yesterday]


from training_plan.core.common import *

# Komplett Yr (Met.no) symbolkodstabell
YR_CODES = {
    "clearsky": "Klart", "fair": "Halvklart", "partlycloudy": "Växlande moln",
    "cloudy": "Mulet", "lightrainshowers": "Lätta regnskurar", "rainshowers": "Regnskurar",
    "heavyrainshowers": "Kraftiga regnskurar", "lightrainshowersandthunder": "Åskskurar",
    "rainshowersandthunder": "Åskskurar", "heavyrainshowersandthunder": "Kraftiga åskskurar",
    "lightrain": "Lätt regn", "rain": "Regn", "heavyrain": "Kraftigt regn",
    "lightrainandthunder": "Lätt regn/åska", "rainandthunder": "Regn och åska",
    "heavyrainandthunder": "Kraftigt regn/åska", "lightsleetshowers": "Lätta byar snöbl. regn",
    "sleetshowers": "Byar snöbl. regn", "heavysleetshowers": "Kraftiga byar snöbl. regn",
    "lightsleet": "Lätt snöblandat regn", "sleet": "Snöblandat regn", "heavysleet": "Kraft. snöbl. regn",
    "lightsnowshowers": "Lätta snöbyar", "snowshowers": "Snöbyar", "heavysnowshowers": "Kraftiga snöbyar",
    "lightsnow": "Lätt snöfall", "snow": "Snöfall", "heavysnow": "Kraftigt snöfall",
    "fog": "Dimma"
}

def fetch_weather(days):
    try:
       # Yr kräver en User-Agent för att tillåta anrop
        headers = {"User-Agent": f"AI-Traningsplanerare ({CONTACT_EMAIL})"}
        resp = requests.get(
            f"https://api.met.no/weatherapi/locationforecast/2.0/compact?lat={LAT}&lon={LON}",
            headers=headers, timeout=10
        )
        resp.raise_for_status()
        data = resp.json()

        timeseries = data.get("properties", {}).get("timeseries", [])

        hourly_by_date = {}
        for item in timeseries:
            # Korrekt tidszonskonvertering från UTC till Stockholm
            utc_dt = datetime.fromisoformat(item["time"])
            local_dt = utc_dt.astimezone(ZoneInfo("Europe/Stockholm"))
            d_str = local_dt.date().isoformat()
            hour = local_dt.hour
            if d_str not in hourly_by_date:
                hourly_by_date[d_str] = {
                    "all_temps": [], "all_precip": 0.0,
                    "am_temps": [], "am_precip": [], "am_codes": [],
                    "pm_temps": [], "pm_precip": [], "pm_codes": []
                }
            details = item.get("data", {}).get("instant", {}).get("details", {})
            temp = details.get("air_temperature")
            # Yr har 1h-intervaller de närmsta dagarna, sedan 6h-intervaller. Vi hanterar båda!
            next_data = item.get("data", {}).get("next_1_hours") or item.get("data", {}).get("next_6_hours") or {}
            precip = next_data.get("details", {}).get("precipitation_amount", 0.0)
            code = next_data.get("summary", {}).get("symbol_code", "")
            # Tvätta Yr-koden (ta bort _day, _night)
            clean_code = code.split("_")[0] if code else ""

            if temp is not None:
                hourly_by_date[d_str]["all_temps"].append(temp)
            if precip is not None:
                hourly_by_date[d_str]["all_precip"] += precip

            if 6 <= hour <= 11:
                if temp is not None: hourly_by_date[d_str]["am_temps"].append(temp)
                if precip is not None: hourly_by_date[d_str]["am_precip"].append(precip)
                if clean_code: hourly_by_date[d_str]["am_codes"].append(clean_code)
            elif 13 <= hour <= 18:
                if temp is not None: hourly_by_date[d_str]["pm_temps"].append(temp)
                if precip is not None: hourly_by_date[d_str]["pm_precip"].append(precip)
                if clean_code: hourly_by_date[d_str]["pm_codes"].append(clean_code)

        result = []
        target_dates = [(date.today() + timedelta(days=i)).isoformat() for i in range(days)]
        for dt in target_dates:
            day_data = hourly_by_date.get(dt, {})
            if not day_data or not day_data["all_temps"]:
                continue
            temp_max = round(max(day_data["all_temps"]), 1)
            temp_min = round(min(day_data["all_temps"]), 1)
            total_rain = round(day_data["all_precip"], 1)

            am_temps = day_data.get("am_temps", [])
            am_temp = round(sum(am_temps) / len(am_temps), 1) if am_temps else temp_min
            am_precip = day_data.get("am_precip", [])
            am_rain = round(sum(am_precip), 1) if am_precip else 0
            am_codes = day_data.get("am_codes", [])
            if am_codes:
                am_code = Counter(am_codes).most_common(1)[0][0]
            else:
                am_code = "unknown"
            am_desc = YR_CODES.get(am_code, am_code.capitalize() or "Unknown")
            if am_temp > 3 and "snow" in am_code and "sleet" not in am_code:
                am_desc = "Rain"

            pm_temps = day_data.get("pm_temps", [])
            pm_temp = round(sum(pm_temps) / len(pm_temps), 1) if pm_temps else temp_max
            pm_precip = day_data.get("pm_precip", [])
            pm_rain = round(sum(pm_precip), 1) if pm_precip else 0
            pm_codes = day_data.get("pm_codes", [])
            if pm_codes:
                pm_code = Counter(pm_codes).most_common(1)[0][0]
            else: 
                pm_code = "unknown"
            pm_desc = YR_CODES.get(pm_code, pm_code.capitalize() or "Unknown")
            if pm_temp > 3 and "snow" in pm_code and "sleet" not in pm_code:
                pm_desc = "Rain"

            if pm_temp > 3 and "snow" in pm_desc.lower():
                pm_desc = "Rain"

            result.append({
                "date": dt,
                "temp_max": temp_max,
                "temp_min": temp_min,
                "temp_morning": am_temp,
                "rain_morning_mm": am_rain,
                "desc_morning": am_desc,
                "weathercode_morning": am_code,
                "temp_afternoon": pm_temp,
                "rain_afternoon_mm": pm_rain,
                "desc": pm_desc,
                "weathercode": pm_code,
                "rain_mm": total_rain,
            })
        cache_payload = json.dumps({"fetched": date.today().isoformat(), "data": result})
        tmp_cache = CACHE_FILE.with_suffix(CACHE_FILE.suffix + ".tmp")
        tmp_cache.write_text(cache_payload)
        tmp_cache.replace(CACHE_FILE)
        return result
    except Exception as e:
        log.warning(f"Weather API (Yr) failed: {e}. Trying cache...")
        if CACHE_FILE.exists():
            try:
                cached = json.loads(CACHE_FILE.read_text())
                log.info(f"Using weather cache from {cached.get('fetched','?')}")
                return cached.get("data", [])
            except Exception as cache_error:
                log.warning(f"Weather cache could not be read: {cache_error}")
        log.warning("No weather cache. Continuing without weather data.")
        return []

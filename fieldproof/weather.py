"""Daily ERA5 rainfall and reference evapotranspiration (Open-Meteo), with 1991-2020 normals.

ERA5 is a ~25 km global reanalysis, so these values describe the area around a plot, not a rain
gauge in the field. ERA5 is published with a delay of about a week; observations whose rainfall
window is not complete yet are marked unavailable instead of being filled in.
"""
import json
import math
import time
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache

import httpx
import numpy as np

from .canonical import dec
from .config import CLIMATE_NORMAL_YEARS, OPEN_METEO_ARCHIVE, WEATHER_DIR

PROVIDER = "Open-Meteo Historical Weather API, ERA5 reanalysis (ECMWF / Copernicus C3S)"
START = date(CLIMATE_NORMAL_YEARS[0], 1, 1)
MIN_NORMAL_30D_MM = 10.0  # below this the season is climatologically dry; % departures are meaningless
MIN_NORMAL_60D_MM = 25.0


def weather_path(plot_id):
    return WEATHER_DIR / f"{plot_id}.json"


def fetch_weather(plot, end=None, tz="Asia/Kolkata", client=None, log=print):
    end = end or datetime.now(timezone.utc).date().isoformat()
    lon, lat = plot["centroid"]
    params = {
        # ERA5 cells are ~25 km; 0.01 degree rounding keeps the request coarse but cell-exact.
        "latitude": round(lat, 2),
        "longitude": round(lon, 2),
        "start_date": START.isoformat(),
        "end_date": end,
        "daily": "precipitation_sum,et0_fao_evapotranspiration",
        "timezone": tz,
        "models": "era5",
    }
    own = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(120, connect=15))
    try:
        for attempt in range(4):
            try:
                resp = client.get(OPEN_METEO_ARCHIVE, params=params)
                resp.raise_for_status()
                payload = resp.json()
                break
            except (httpx.HTTPError, ValueError):
                if attempt == 3:
                    raise
                time.sleep(3 * (attempt + 1))
    finally:
        if own:
            client.close()
    daily = payload["daily"]
    times, precip, et0 = daily["time"], daily["precipitation_sum"], daily["et0_fao_evapotranspiration"]
    if times[0] != START.isoformat() or len(times) != len(precip) or len(times) != len(et0):
        raise ValueError("unexpected Open-Meteo response shape")
    last = len(times)
    while last and (precip[last - 1] is None or et0[last - 1] is None):
        last -= 1
    out = {
        "plot_id": plot["plot_id"],
        "provider": PROVIDER,
        "source_url": "https://open-meteo.com/en/docs/historical-weather-api",
        "license": "Open-Meteo data CC BY 4.0; ERA5 under the Copernicus licence",
        "request": {k: params[k] for k in ("latitude", "longitude", "timezone", "models")},
        "grid_latitude": payload.get("latitude"),
        "grid_longitude": payload.get("longitude"),
        "units": {"precipitation_sum": "mm", "et0_fao_evapotranspiration": "mm"},
        "start": START.isoformat(),
        "end": times[last - 1],
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "precip_mm": precip[:last],
        "et0_mm": et0[:last],
    }
    WEATHER_DIR.mkdir(parents=True, exist_ok=True)
    weather_path(plot["plot_id"]).write_text(json.dumps(out, separators=(",", ":")) + "\n", encoding="utf-8")
    log(f"[{plot['plot_id']}] ERA5 daily {out['start']} .. {out['end']} at grid "
        f"{out['grid_latitude']}, {out['grid_longitude']}")
    return out


def load_weather(plot_id):
    path = weather_path(plot_id)
    return WeatherSeries(json.loads(path.read_text(encoding="utf-8"))) if path.exists() else None


def _same_day(year, d):
    try:
        return date(year, d.month, d.day)
    except ValueError:  # 29 February in a non-leap year
        return date(year, 2, 28)


class WeatherSeries:
    def __init__(self, doc):
        self.doc = doc
        self.start = date.fromisoformat(doc["start"])
        self.end = date.fromisoformat(doc["end"])
        self.precip = np.array([np.nan if v is None else v for v in doc["precip_mm"]], dtype=float)
        self.et0 = np.array([np.nan if v is None else v for v in doc["et0_mm"]], dtype=float)
        self.balance = self.precip - self.et0
        self._normal = lru_cache(maxsize=4096)(self._normal_uncached)

    def window_sum(self, series, end, days):
        i1 = (end - self.start).days + 1
        i0 = i1 - days
        if i0 < 0 or i1 > len(series):
            return None
        values = series[i0:i1]
        if np.isnan(values).any():
            return None
        return float(values.sum())

    def _normal_uncached(self, name, month, day, days):
        series = getattr(self, name)
        anchor = date(2000, month, day)  # leap year, so 29 February is representable
        sums = []
        for year in range(CLIMATE_NORMAL_YEARS[0], CLIMATE_NORMAL_YEARS[1] + 1):
            value = self.window_sum(series, _same_day(year, anchor), days)
            if value is not None:
                sums.append(value)
        return tuple(sums)

    def baseline(self, name, end, days):
        return self._normal(name, end.month, end.day, days)

    def features(self, observed_date):
        """Rainfall context for an acquisition: windows end the day before the overpass."""
        end = observed_date - timedelta(days=1)
        if end > self.end:
            return {"status": "unavailable", "reason": f"ERA5 available until {self.end.isoformat()}"}
        out = {"status": "available", "window_end_date": end.isoformat(), "normal_period": "1991-2020"}
        for days, minimum in ((30, MIN_NORMAL_30D_MM), (60, MIN_NORMAL_60D_MM)):
            rain = self.window_sum(self.precip, end, days)
            base = self.baseline("precip", end, days)
            if rain is None or len(base) < 25:
                return {"status": "unavailable", "reason": "incomplete daily record for the window"}
            normal = float(np.mean(base))
            out[f"rain_{days}d_mm"] = dec(rain, 1)
            out[f"rain_{days}d_normal_mm"] = dec(normal, 1)
            if normal >= minimum:
                out[f"rain_{days}d_departure_pct"] = dec((rain - normal) / normal * 100, 1)
            else:
                out[f"rain_{days}d_departure_pct"] = None
            if days == 60:
                out["rain_60d_percentile"] = dec(100 * sum(1 for v in base if v <= rain) / len(base), 1)
        bal = self.window_sum(self.balance, end, 60)
        bal_base = self.baseline("balance", end, 60)
        if bal is not None and bal_base:
            out["water_balance_60d_mm"] = dec(bal, 1)
            out["water_balance_60d_normal_mm"] = dec(float(np.mean(bal_base)), 1)
        out["departure_applicable"] = out["rain_60d_departure_pct"] is not None
        return out


def imd_category(departure_pct):
    """India Meteorological Department rainfall departure categories."""
    if departure_pct is None or (isinstance(departure_pct, float) and math.isnan(departure_pct)):
        return None
    d = round(float(departure_pct))  # IMD categories use whole percentages
    if d >= 60:
        return "large excess"
    if d >= 20:
        return "excess"
    if d >= -19:
        return "normal"
    if d >= -59:
        return "deficient"
    if d > -100:
        return "large deficient"
    return "no rain"

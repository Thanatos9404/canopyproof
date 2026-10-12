"""Deterministic synthetic plot history used by several tests (no network)."""
import math
import random
from datetime import date, timedelta

from fieldproof.canonical import dec

PLOT = {"plot_id": "TEST-01", "plot_key": "0x" + "ab" * 32, "geometry_hash": "0x" + "cd" * 32, "area_ha": "1.0000"}
DROUGHT = (date(2026, 8, 1), date(2026, 9, 30))


def ndvi_on(d):
    # One rainfed crop per year peaking in early September, bare soil otherwise.
    phase = (d.timetuple().tm_yday - 170) / 150 * math.pi
    return 0.2 + (0.6 * math.sin(phase) if 0 <= phase <= math.pi else 0.0)


def observations(start=date(2019, 1, 3), end=date(2026, 10, 1), step=8, seed=3):
    rnd = random.Random(seed)
    out, d, i = [], start, 0
    while d <= end:
        v = ndvi_on(d) + rnd.uniform(-0.02, 0.02)
        if DROUGHT[0] <= d <= DROUGHT[1]:
            v -= 0.35
        cloudy = i % 11 == 5
        unix = int((d - date(1970, 1, 1)).total_seconds()) + 5 * 3600
        out.append({
            "item_id": f"S2X_TEST_{d.strftime('%Y%m%d')}_L2A",
            "observed_at": f"{d.isoformat()}T05:00:00Z",
            "observed_unix": unix,
            "date": d.isoformat(),
            "platform": "sentinel-2b",
            "mgrs_tile": "43PGS",
            "processing_baseline": "05.11",
            "scene_cloud_cover_pct": "12.00",
            "assets": {"B04": "https://example.invalid/B04.tif"},
            "reflectance_scale_offset": {"B04": ["0.000100", "-0.100000"]},
            "grid": {"crs": "EPSG:32643", "window_transform_10m": ["10.00"], "shape_10m": [10, 10]},
            "quality": {"status": "insufficient" if cloudy else "clear", "valid_fraction": "0.2000" if cloudy else "0.9800"},
            "ndvi": None if cloudy else {"mean": dec(v)},
            "ndmi": None if cloudy else {"mean": dec(v - 0.3)},
            "pixel_digest": "sha256:" + "00" * 32,
        })
        d += timedelta(days=step)
        i += 1
    return out


class Weather:
    """Rainfall 50% below normal during the synthetic drought, normal otherwise."""

    def features(self, d):
        dep = "-50.0" if DROUGHT[0] <= d <= DROUGHT[1] + timedelta(days=10) else "5.0"
        return {"status": "available", "window_end_date": (d - timedelta(days=1)).isoformat(),
                "rain_60d_mm": "100.0", "rain_60d_normal_mm": "200.0", "rain_60d_departure_pct": dep}

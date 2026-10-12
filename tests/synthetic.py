"""Deterministic synthetic monitoring cell (no network): forest, a smoke episode, then a clearing."""
from datetime import date, timedelta

import numpy as np

from canopyproof.sentinel import to_i16

SITE = {"site_id": "TEST-01", "site_key": "0x" + "ab" * 32, "project_id": "TEST-PROJECT",
        "project_key": "0x" + "cd" * 32, "geometry_hash": "0x" + "ef" * 32, "area_ha": "100.00"}
SHAPE = (20, 20)
SMOKE = (date(2022, 8, 1), date(2022, 8, 12))  # two hazy looks
CLEARING = date(2023, 7, 1)  # the left half of the cell is cleared from this date on
BASELINE = ("2019-01-01", "2020-12-31")
MONTHS = [6, 7, 8, 9, 10]


def scenes(start=date(2019, 6, 1), end=date(2024, 10, 31), step=6, seed=5):
    rng = np.random.default_rng(seed)
    inside = np.ones(SHAPE, bool)
    obs, rasters = [], {"inside": inside}
    d, k = start, 0
    while d <= end:
        if d.month in MONTHS:
            ndvi = 0.78 + rng.normal(0, 0.02, SHAPE)
            nbr = 0.55 + rng.normal(0, 0.02, SHAPE)
            if SMOKE[0] <= d <= SMOKE[1]:
                ndvi -= 0.35  # haze: NDVI drops, NBR barely moves
                nbr -= 0.03
            if d >= CLEARING:
                ndvi[:, :10] = 0.18 + rng.normal(0, 0.02, (SHAPE[0], 10))
                nbr[:, :10] = 0.02 + rng.normal(0, 0.02, (SHAPE[0], 10))
            cloudy = k % 9 == 4
            item = f"S2X_TEST_{d.strftime('%Y%m%d')}_L2A"
            rasters[item] = (to_i16(np.where(cloudy, np.nan, ndvi)), to_i16(np.where(cloudy, np.nan, nbr)))
            unix = int((d - date(1970, 1, 1)).total_seconds()) + 5 * 3600
            obs.append({
                "item_id": item, "observed_at": f"{d.isoformat()}T05:00:00Z", "observed_unix": unix, "date": d.isoformat(),
                "platform": "sentinel-2b", "mgrs_tile": "10TFN", "processing_baseline": "05.11", "scene_cloud_cover_pct": "3.00",
                "assets": {"B04": "https://example.invalid/B04.tif"}, "reflectance_scale_offset": {"B04": ["0.000100", "-0.100000"]},
                "grid": {"crs": "EPSG:32610", "window_transform_20m": ["20.00", "0.00", "600000.00", "0.00", "-20.00", "4700000.00"],
                         "shape_20m": list(SHAPE)},
                "quality": {"status": "insufficient" if cloudy else "clear", "valid_fraction": "0.0000" if cloudy else "1.0000",
                            "snow_fraction": "0.0000"},
                "ndvi": None if cloudy else {"mean": f"{float(np.mean(ndvi)):.4f}"},
                "nbr": None if cloudy else {"mean": f"{float(np.mean(nbr)):.4f}"},
                "pixel_digest": "sha256:" + "00" * 32,
            })
            k += 1
        d += timedelta(days=step)
    return obs, rasters

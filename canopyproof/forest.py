"""Per-pixel forest change detection and integrity status for one monitoring cell.

After the baseline period every value is computed "as of" the acquisition: only that scene and
earlier ones are used, so a record never changes when later data arrive and back-tests are honest.
(Acquisitions inside the baseline period are compared with the baseline they belong to.)

1. Baseline. Clear acquisitions inside the project's baseline period give, per 20 m pixel and per
   calendar month, a seasonal median NDVI and NBR (+-61 days, so deciduous or snowy seasons compare
   like with like). Pixels whose baseline NDVI median is >= 0.45 form the forest mask.
2. Change. For each later clear acquisition: dNDVI = baseline - now and dNBR = baseline - now.
   A pixel is "disturbed" when dNBR >= 0.27 (lower bound of moderate burn severity, Key & Benson 2006),
   or when dNDVI >= 0.20 together with dNBR >= 0.15 (canopy loss that also exposes soil or char).
   Requiring the SWIR-based dNBR is what keeps wildfire smoke and haze, which depress NDVI but barely
   move NBR, from being read as loss. "Burn-like" additionally needs NBR <= 0.10 now.
3. Persistence. A pixel is "confirmed" once it is disturbed in two consecutive clear looks at least
   5 days apart. Confirmed loss is cumulative, because regrowing shrubs do not restore the carbon of a
   burned or cleared stand.
   Only acquisitions in the project's snow-free / leaf-on monitoring months, with no snow in the cell,
   are compared; others are recorded as insufficient data rather than guessed at.
4. Isolation Forest (scikit-learn), trained only on earlier years, scores how unusual the scene's
   change profile is. It is advisory: it can raise "watch" in the app, but the smart contract only
   uses the measured fractions.

Status: reversal_suspected when confirmed loss >= 5% of the cell's forest; watch when >= 2% is
disturbed, burn-like or confirmed (or the model flags an unusual drop); otherwise healthy.
Satellite change is evidence for review, not proof of carbon loss: harvest allowed under a
project's rules, storms, insect damage and phenology can all look like change.
"""
import warnings
from datetime import date

import numpy as np

from . import ANALYTICS_VERSION
from .canonical import dec
from .sentinel import from_i16

FOREST_NDVI = 0.45
D_NBR = 0.27
D_NDVI = 0.20
D_NBR_WITH_NDVI = 0.15
BURN_NBR_MAX = 0.10
MIN_VALID_FOREST = 0.6
MIN_BASELINE_SCENES = 6
MIN_CONFIRM_GAP_DAYS = 5
MAX_SNOW_FRACTION = 0.01
SEASON_DAYS = 61
PIXEL_HA = 0.04  # 20 m x 20 m
WATCH_BPS = 200
REVERSAL_BPS = 500
STATUS_CODES = {"insufficient_data": 0, "healthy": 1, "watch": 2, "reversal_suspected": 3}
LIMITATIONS = [
    "Satellite change detection is evidence for review, not proof of carbon loss.",
    "Harvest permitted by a project's rules, storms, insects, drought and phenology can all look like change.",
    "A 1 km monitoring cell samples a project; it does not measure the whole project or its carbon stock.",
    "Clouds, smoke and haze are masked imperfectly; persistence over two clear looks reduces, not removes, false alarms.",
    "The method says nothing about additionality, baselines or leakage, the main reasons credits have been overstated.",
]


def _doy_distance(a, b):
    d = abs(a - b) % 365.25
    return min(d, 365.25 - d)


def build_baseline(dates, ndvi, nbr, clear, start, end):
    """Seasonal per-pixel baseline. Returns dict or None when the baseline period is too thin."""
    idx = [i for i, d in enumerate(dates) if clear[i] and start <= d <= end]
    if len(idx) < MIN_BASELINE_SCENES:
        return None
    warnings.filterwarnings("ignore", message="All-NaN slice encountered")
    warnings.filterwarnings("ignore", message="Mean of empty slice")
    b_ndvi, b_nbr = ndvi[idx], nbr[idx]
    doys = np.array([dates[i].timetuple().tm_yday for i in idx], dtype=float)
    with np.errstate(all="ignore"):
        all_ndvi = np.nanmedian(b_ndvi, axis=0)
        all_nbr = np.nanmedian(b_nbr, axis=0)
        count = np.isfinite(b_ndvi).sum(axis=0)
    monthly = {}
    for m in range(1, 13):
        centre = date(2001, m, 15).timetuple().tm_yday
        sel = [k for k, d in enumerate(doys) if _doy_distance(d, centre) <= SEASON_DAYS]
        with np.errstate(all="ignore"):
            if sel:
                mn = np.nanmedian(b_ndvi[sel], axis=0)
                mb = np.nanmedian(b_nbr[sel], axis=0)
                enough = np.isfinite(b_ndvi[sel]).sum(axis=0) >= 2
            else:
                mn = mb = np.full(all_ndvi.shape, np.nan)
                enough = np.zeros(all_ndvi.shape, bool)
        monthly[m] = (np.where(enough, mn, all_ndvi), np.where(enough, mb, all_nbr))
    forest = (count >= 3) & (all_ndvi >= FOREST_NDVI)
    return {"monthly": monthly, "forest": forest, "scenes": len(idx), "all_ndvi": all_ndvi}


def isolation_scores(rows, min_train=25):
    """Isolation Forest trained on earlier years' change profiles, scoring the current year."""
    from sklearn.ensemble import IsolationForest

    feats = {i: r["features"] for i, r in enumerate(rows) if r.get("features") is not None}
    out = {}
    for year in sorted({rows[i]["date"].year for i in feats}):
        train = [feats[i] for i in feats if rows[i]["date"].year < year]
        score_idx = [i for i in feats if rows[i]["date"].year == year]
        if len(train) < min_train or not score_idx:
            continue
        model = IsolationForest(n_estimators=200, contamination=0.05, random_state=7)
        model.fit(np.array(train))
        X = np.array([feats[i] for i in score_idx])
        scores = -model.score_samples(X)
        flags = model.predict(X) == -1
        out.update({i: (float(s), bool(f)) for i, s, f in zip(score_idx, scores, flags)})
    return out


def classify(row):
    if row["status_basis"] is not None:
        return row["status_basis"], [row["status_reason"]]
    reasons = []
    if row["cumulative_bps"] >= REVERSAL_BPS:
        reasons.append(f"Confirmed loss covers {row['cumulative_bps'] / 100:.1f}% of the cell's baseline forest "
                       f"({row['confirmed_ha']:.1f} ha), above the 5% reversal threshold.")
        return "reversal_suspected", reasons
    if row["disturbed_bps"] >= WATCH_BPS:
        reasons.append(f"{row['disturbed_bps'] / 100:.1f}% of visible forest pixels changed sharply against the baseline.")
    if row["burn_bps"] >= WATCH_BPS:
        reasons.append(f"{row['burn_bps'] / 100:.1f}% of visible forest pixels look burned (dNBR >= 0.27, NBR <= 0.10).")
    if row["cumulative_bps"] >= WATCH_BPS:
        reasons.append(f"Confirmed loss so far: {row['cumulative_bps'] / 100:.1f}% of the cell's forest.")
    if row["ml_unusual"]:
        reasons.append("Isolation Forest marks this change profile as unusual for the cell (advisory).")
    if reasons:
        return "watch", reasons
    return "healthy", ["Forest pixels match the baseline within the change thresholds."]


def analyze_site(observations, rasters, baseline_start, baseline_end, monitoring_months=None):
    """Return (per-observation analytics in the same order, site summary)."""
    months = set(monitoring_months or range(1, 13))
    items = [o["item_id"] for o in observations]
    dates = [date.fromisoformat(o["date"]) for o in observations]
    inside = rasters["inside"]
    ndvi = np.stack([np.where(inside, from_i16(rasters[i][0]), np.nan) for i in items])
    nbr = np.stack([np.where(inside, from_i16(rasters[i][1]), np.nan) for i in items])
    clear = [o["quality"]["status"] == "clear" for o in observations]
    in_season = [d.month in months and float(o["quality"]["snow_fraction"]) <= MAX_SNOW_FRACTION
                 for d, o in zip(dates, observations)]
    usable = [c and s for c, s in zip(clear, in_season)]
    b0, b1 = date.fromisoformat(baseline_start), date.fromisoformat(baseline_end)
    base = build_baseline(dates, ndvi, nbr, usable, b0, b1)

    rows = []
    shape = inside.shape
    prev = np.zeros(shape, bool)
    seen = np.zeros(shape, bool)
    last_look = np.full(shape, -10_000, dtype=np.int64)
    confirmed = np.zeros(shape, bool)
    first_confirmed = np.full(shape, "", dtype=object)
    forest = base["forest"] if base else np.zeros(shape, bool)
    forest_px = int(forest.sum())
    for k, o in enumerate(observations):
        row = {"date": dates[k], "status_basis": None, "status_reason": None, "features": None, "ml_unusual": False,
               "disturbed_bps": 0, "persistent_bps": 0, "burn_bps": 0, "cumulative_bps": 0, "confirmed_ha": 0.0,
               "valid_forest": 0.0, "d_ndvi": None, "d_nbr": None, "in_baseline": b0 <= dates[k] <= b1}
        if base is None or forest_px == 0:
            row.update(status_basis="insufficient_data", status_reason="No usable baseline forest for this cell.")
            rows.append(row)
            continue
        if not clear[k]:
            row.update(status_basis="insufficient_data", status_reason="Too few cloud-free pixels in this acquisition.")
            rows.append(row)
            continue
        if not in_season[k]:
            row.update(status_basis="insufficient_data",
                       status_reason="Outside the snow-free monitoring season, or snow in the cell; not compared.")
            rows.append(row)
            continue
        bn, bb = base["monthly"][dates[k].month]
        valid = forest & np.isfinite(ndvi[k]) & np.isfinite(nbr[k]) & np.isfinite(bn) & np.isfinite(bb)
        vf = valid.sum() / forest_px
        row["valid_forest"] = vf
        if vf < MIN_VALID_FOREST:
            row.update(status_basis="insufficient_data", status_reason="Too few cloud-free forest pixels in this acquisition.")
            rows.append(row)
            continue
        with np.errstate(invalid="ignore"):
            d_ndvi = bn - ndvi[k]
            d_nbr = bb - nbr[k]
            disturbed = valid & ((d_nbr >= D_NBR) | ((d_ndvi >= D_NDVI) & (d_nbr >= D_NBR_WITH_NDVI)))
            burn = valid & (d_nbr >= D_NBR) & (nbr[k] <= BURN_NBR_MAX)
        today = dates[k].toordinal()
        persistent = disturbed & prev & seen & (today - last_look >= MIN_CONFIRM_GAP_DAYS)
        newly = persistent & ~confirmed
        first_confirmed[newly] = o["date"]
        confirmed |= persistent
        # A pixel's "previous look" only advances on looks far enough apart to be independent.
        advance = valid & (today - last_look >= MIN_CONFIRM_GAP_DAYS)
        prev = np.where(advance, disturbed, prev)
        last_look = np.where(advance, today, last_look)
        seen |= valid
        nv = int(valid.sum())
        row.update(
            disturbed_bps=int(round(10_000 * disturbed.sum() / nv)),
            persistent_bps=int(round(10_000 * persistent.sum() / nv)),
            burn_bps=int(round(10_000 * burn.sum() / nv)),
            cumulative_bps=int(round(10_000 * confirmed.sum() / forest_px)),
            confirmed_ha=float(confirmed.sum() * PIXEL_HA),
            d_ndvi=float(np.nanmean(d_ndvi[valid])),
            d_nbr=float(np.nanmean(d_nbr[valid])),
        )
        row["features"] = [row["d_ndvi"], row["d_nbr"], disturbed.sum() / nv, burn.sum() / nv, persistent.sum() / nv]
        rows.append(row)

    iso = isolation_scores(rows)
    import sklearn

    results = []
    for i, row in enumerate(rows):
        score = iso.get(i)
        row["ml_unusual"] = bool(score and score[1] and row["d_ndvi"] is not None and row["d_ndvi"] > 0.05)
        status, reasons = classify(row)
        measured = row["status_basis"] is None
        results.append({
            "version": ANALYTICS_VERSION,
            "status": status,
            "status_code": STATUS_CODES[status],
            "reasons": reasons,
            "baseline_period": [baseline_start, baseline_end],
            "monitoring_months": sorted(months),
            "in_baseline_period": row["in_baseline"],
            "baseline_scenes": base["scenes"] if base else 0,
            "forest_pixels": forest_px,
            "forest_ha": dec(forest_px * PIXEL_HA, 2),
            "valid_forest_fraction": dec(row["valid_forest"]),
            "disturbed_fraction": dec(row["disturbed_bps"] / 10_000) if measured else None,
            "persistent_fraction": dec(row["persistent_bps"] / 10_000) if measured else None,
            "burn_like_fraction": dec(row["burn_bps"] / 10_000) if measured else None,
            "cumulative_loss_fraction": dec(row["cumulative_bps"] / 10_000) if measured else None,
            "confirmed_loss_ha": dec(row["confirmed_ha"], 2) if measured else None,
            "mean_d_ndvi": dec(row["d_ndvi"]) if measured else None,
            "mean_d_nbr": dec(row["d_nbr"]) if measured else None,
            "isolation_forest": None if score is None else {
                "score": dec(score[0], 3), "unusual": score[1], "advisory_flag": row["ml_unusual"],
                "model": ("IsolationForest(n_estimators=200, contamination=0.05, random_state=7) trained on earlier "
                          f"years, scikit-learn {sklearn.__version__}"),
            },
            "thresholds": {"d_nbr": "0.27", "d_ndvi": "0.20", "d_nbr_with_d_ndvi": "0.15", "burn_nbr_max": "0.10", "forest_ndvi": "0.45",
                           "watch_fraction": "0.02", "reversal_fraction": "0.05", "persistence_looks": 2,
                           "persistence_min_gap_days": MIN_CONFIRM_GAP_DAYS, "max_snow_fraction": "0.01"},
            "fractions_bps": {"disturbed": row["disturbed_bps"], "persistent": row["persistent_bps"], "burn": row["burn_bps"],
                     "cumulative": row["cumulative_bps"]},
        })
    summary = {
        "forest_pixels": forest_px,
        "forest_ha": round(forest_px * PIXEL_HA, 2),
        "baseline_scenes": base["scenes"] if base else 0,
        "confirmed_ha": round(float(confirmed.sum() * PIXEL_HA), 2),
        "first_confirmed": first_confirmed,
        "forest_mask": forest,
    }
    return results, summary

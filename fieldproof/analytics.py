"""Explainable anomaly and drought-stress signals for one plot.

Every value is computed "as of" the acquisition: only data that existed on that date is used, so
a record never changes when later years arrive and historical back-tests are honest. Three layers,
all deterministic for a given input and library version:

1. Seasonal expectation. A two-harmonic regression of NDVI (and NDMI) on day-of-year, fitted on
   the plot's clear Sentinel-2 history from previous years, gives the value expected for that date.
   The residual is scaled by a robust spread (1.4826 x MAD) into a z-score.
2. Vegetation Condition Index (Kogan 1995). VCI = 100 x (NDVI_c - min) / (max - min), where NDVI_c
   is the maximum clear NDVI in the 31 days up to the acquisition and min/max are the same composite
   over this and earlier years of the plot's record. VCI below 35 is the classic drought threshold.
3. Isolation Forest (scikit-learn), an unsupervised machine-learning detector trained on earlier
   years, scores how unusual the combination [NDVI z, NDMI z, rainfall departure, VCI] is.

Vegetation signals are only interpreted when a crop is normally present on that date (expected
NDVI >= 0.35); otherwise a bare, fallow field would look like "stress". VCI is also withheld when
the plot's multi-year spread is under 0.15 NDVI, where it would amplify noise.

A transparent rule set then fuses the signals with the 60-day ERA5 rainfall departure into
`normal`, `watch` or `alert`. These are prompts for a field visit and evidence for programmes that
need a documented, reproducible trigger. They are not a diagnosis of disease, pests, nutrient
status or yield, which satellite indices cannot provide.
"""
import math
from datetime import date, timedelta

import numpy as np

from . import ANALYTICS_VERSION
from .canonical import dec
from .weather import imd_category

VCI_DROUGHT = 35.0  # Kogan (1995): VCI < 35 indicates drought stress
Z_DROP = -2.0  # residual at least two robust standard deviations below the seasonal expectation
RAIN_DEFICIENT = -20.0  # IMD: deficient rainfall is -20% to -59%
RAIN_LARGE_DEFICIENT = -60.0
COMPOSITE_DAYS = 30
MIN_VCI_YEARS = 5
MIN_VCI_RANGE = 0.15  # narrower multi-year spreads make VCI swing on noise
CROP_EXPECTED_NDVI = 0.35  # below this a crop is not normally present on that date (fallow/off-season)
MIN_FIT_POINTS = 20
MIN_FIT_YEARS = 3
SCALE_FLOOR = {"ndvi": 0.03, "ndmi": 0.03}
STRESS_CODES = {"insufficient_data": 0, "normal": 1, "watch": 2, "alert": 3}
LIMITATIONS = [
    "Satellite vegetation indices cannot diagnose disease, pests, nutrient status or yield.",
    "Low NDVI can also mean harvest, a fallow season, late sowing or a crop change. Verify on the ground.",
    "ERA5 rainfall is a ~25 km reanalysis, not a rain gauge in the field, and ignores irrigation.",
    "Plot boundaries are approximate and 10-20 m pixels mix the field with its edges.",
    "VCI and the seasonal model use only the plot's own record since 2019, a short baseline.",
    "Thresholds are literature defaults, not calibrated against observed crop losses.",
]


def _features(t):
    w = 2 * math.pi * t / 365.25
    return [1.0, math.cos(w), math.sin(w), math.cos(2 * w), math.sin(2 * w)]


def seasonal_residuals(rows, key):
    """Harmonic fit on previous years only. Returns {index: (expected, z)} where history suffices."""
    out = {}
    pts = [(i, r) for i, r in enumerate(rows) if r["clear"] and r[key] is not None]
    years = sorted({r["date"].year for _, r in pts})
    for year in years:
        train = [r for _, r in pts if r["date"].year < year]
        if len(train) < MIN_FIT_POINTS or len({r["date"].year for r in train}) < MIN_FIT_YEARS:
            continue
        X = np.array([_features(r["doy"]) for r in train])
        y = np.array([r[key] for r in train])
        coef, *_ = np.linalg.lstsq(X, y, rcond=None)
        resid = y - X @ coef
        scale = max(SCALE_FLOOR[key], 1.4826 * float(np.median(np.abs(resid - np.median(resid)))))
        for i, r in pts:
            if r["date"].year == year:
                expected = float(np.dot(_features(r["doy"]), coef))
                out[i] = (expected, (r[key] - expected) / scale)
    return out


def _same_day(year, d):
    try:
        return date(year, d.month, d.day)
    except ValueError:
        return date(year, 2, 28)


def vci_values(rows):
    """VCI per clear acquisition using 31-day maximum-value composites over this and earlier years.

    Returns {index: dict} with either a value or the reason VCI is not applicable.
    """
    clear = [r for r in rows if r["clear"] and r["ndvi"] is not None]
    by_year = {}
    for r in clear:
        by_year.setdefault(r["date"].year, []).append(r)
    out = {}
    for i, r in enumerate(rows):
        if not (r["clear"] and r["ndvi"] is not None):
            continue
        composites = {}
        for year, year_rows in by_year.items():
            if year > r["date"].year:
                continue  # as-of: never look at later years
            end = _same_day(year, r["date"])
            start = end - timedelta(days=COMPOSITE_DAYS)
            vals = [x["ndvi"] for x in year_rows if start <= x["date"] <= end]
            if vals:
                composites[year] = max(vals)
        current = composites.get(r["date"].year)
        earlier = [v for y, v in composites.items() if y != r["date"].year]
        if current is None or len(composites) < MIN_VCI_YEARS:
            out[i] = {"status": "insufficient_years", "years": len(composites)}
            continue
        if float(np.median(earlier)) < CROP_EXPECTED_NDVI:
            out[i] = {"status": "off_season", "years": len(composites)}
            continue
        lo, hi = min(composites.values()), max(composites.values())
        if hi - lo < MIN_VCI_RANGE:
            out[i] = {"status": "stable_history", "years": len(composites)}
            continue
        out[i] = {"status": "ok", "value": 100 * (current - lo) / (hi - lo), "years": len(composites),
                  "composite": current, "min": lo, "max": hi}
    return out


def isolation_scores(rows, ndvi_fit, ndmi_fit, vci, min_train=30):
    """Isolation Forest trained on earlier years' feature vectors, scoring the current year."""
    from sklearn.ensemble import IsolationForest

    feats = {}
    for i, r in enumerate(rows):
        if i in ndvi_fit and i in ndmi_fit:
            dep = r["rain60"]
            feats[i] = [
                ndvi_fit[i][1],
                ndmi_fit[i][1],
                0.0 if dep is None else max(-1.0, min(2.0, dep / 100)),
                vci[i]["value"] / 100 - 0.5 if vci.get(i, {}).get("status") == "ok" else 0.0,
            ]
    out = {}
    for year in sorted({rows[i]["date"].year for i in feats}):
        train = [feats[i] for i in feats if rows[i]["date"].year < year]
        score_idx = [i for i in feats if rows[i]["date"].year == year]
        if len(train) < min_train or not score_idx:
            continue
        model = IsolationForest(n_estimators=200, contamination=0.05, random_state=7)
        model.fit(np.array(train))
        X = np.array([feats[i] for i in score_idx])
        scores = -model.score_samples(X)  # higher = more unusual; flagged above the earlier years' 95th pct
        flags = model.predict(X) == -1
        out.update({i: (float(s), bool(f)) for i, s, f in zip(score_idx, scores, flags)})
    return out


def classify(clear, vci, z, rain_dep, ml_negative, crop_expected=True):
    """Transparent fusion rules. Returns (level, signals, reasons)."""
    if not clear:
        return "insufficient_data", {}, ["Too few cloud-free pixels inside the plot for this acquisition."]
    signals = {
        "vci_low": vci is not None and vci < VCI_DROUGHT,
        "ndvi_below_seasonal": crop_expected and z is not None and z <= Z_DROP,
        "rain_deficient": rain_dep is not None and rain_dep <= RAIN_DEFICIENT,
        "rain_large_deficient": rain_dep is not None and rain_dep <= RAIN_LARGE_DEFICIENT,
        "ml_unusual_low": bool(ml_negative),
    }
    reasons = []
    if signals["vci_low"]:
        reasons.append(f"Vegetation Condition Index {vci:.0f} is below {VCI_DROUGHT:.0f} for this time of year.")
    if signals["ndvi_below_seasonal"]:
        reasons.append(f"NDVI is {abs(z):.1f} robust SD below this plot's seasonal pattern.")
    if rain_dep is not None and rain_dep <= RAIN_DEFICIENT:
        reasons.append(f"60-day rainfall is {abs(rain_dep):.0f}% below the 1991-2020 normal ({imd_category(rain_dep)}).")
    if signals["ml_unusual_low"]:
        reasons.append("Isolation Forest marks this combination of signals as unusual for the plot.")
    veg = signals["vci_low"] or signals["ndvi_below_seasonal"]
    if veg and signals["rain_deficient"]:
        level = "alert"
    elif veg or signals["ml_unusual_low"]:
        level = "watch"
    else:
        level = "normal"
        if signals["rain_deficient"]:
            reasons.append("Rainfall is below normal but vegetation is within this plot's usual range.")
        elif crop_expected:
            reasons.append("Vegetation and rainfall signals are within this plot's usual range.")
        else:
            reasons.append("No crop is normally present on this date (off-season), so vegetation is not assessed.")
    return level, signals, reasons


def analyze(observations, weather):
    """Return one analytics dict per observation (same order) plus a summary."""
    rows = []
    for o in observations:
        d = date.fromisoformat(o["date"])
        wx = weather.features(d) if weather else {"status": "unavailable", "reason": "no weather data"}
        dep = wx.get("rain_60d_departure_pct") if wx.get("status") == "available" else None
        rows.append({
            "date": d,
            "doy": d.timetuple().tm_yday,
            "clear": o["quality"]["status"] == "clear",
            "ndvi": float(o["ndvi"]["mean"]) if o.get("ndvi") else None,
            "ndmi": float(o["ndmi"]["mean"]) if o.get("ndmi") else None,
            "rain60": float(dep) if dep is not None else None,
            "weather": wx,
        })
    ndvi_fit = seasonal_residuals(rows, "ndvi")
    ndmi_fit = seasonal_residuals(rows, "ndmi")
    vci = vci_values(rows)
    iso = isolation_scores(rows, ndvi_fit, ndmi_fit, vci)

    import sklearn

    results = []
    for i, r in enumerate(rows):
        v = dict(vci.get(i) or {})
        z = ndvi_fit[i][1] if i in ndvi_fit else None
        crop_expected = i in ndvi_fit and ndvi_fit[i][0] >= CROP_EXPECTED_NDVI
        if v.get("status") == "ok" and not crop_expected:
            v = {"status": "off_season", "years": v["years"]}  # harvest timing is not drought
        vci_value = v.get("value") if v.get("status") == "ok" else None
        score = iso.get(i)
        ml_negative = score is not None and score[1] and crop_expected and (z is not None and z <= -1.0)
        # Classify on the same rounded values that are committed on-chain, so the contract agrees.
        level, signals, reasons = classify(
            r["clear"],
            float(dec(vci_value, 1)) if vci_value is not None else None,
            float(dec(z, 2)) if z is not None else None,
            r["rain60"], ml_negative, crop_expected)
        results.append({
            "version": ANALYTICS_VERSION,
            "vci_pct": dec(vci_value, 1) if vci_value is not None else None,
            "vci_status": v.get("status", "not_clear"),
            "vci_years": v.get("years"),
            "vci_composite_ndvi": dec(v["composite"]) if vci_value is not None else None,
            "vci_min_max_ndvi": [dec(v["min"]), dec(v["max"])] if vci_value is not None else None,
            "ndvi_expected": dec(ndvi_fit[i][0]) if i in ndvi_fit else None,
            "ndvi_residual_z": dec(z, 2) if z is not None else None,
            "ndmi_expected": dec(ndmi_fit[i][0]) if i in ndmi_fit else None,
            "ndmi_residual_z": dec(ndmi_fit[i][1], 2) if i in ndmi_fit else None,
            "crop_expected": crop_expected if i in ndvi_fit else None,
            "isolation_forest": None if score is None else {
                "score": dec(score[0], 3), "unusual": score[1],
                "model": ("IsolationForest(n_estimators=200, contamination=0.05, random_state=7) trained on "
                          f"earlier years, scikit-learn {sklearn.__version__}"),
            },
            "rain_60d_imd_category": imd_category(r["rain60"]),
            "stress_level": level,
            "stress_code": STRESS_CODES[level],
            "signals": signals,
            "reasons": reasons,
            "thresholds": {"vci_drought": "35", "ndvi_z_drop": "-2.0", "rain_deficient_pct": "-20",
                           "crop_expected_ndvi": "0.35"},
            "weather": r["weather"],
        })
    return results

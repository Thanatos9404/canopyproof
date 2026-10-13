"""Independent check against Hansen et al. Global Forest Change (UMD/Google, CC BY 4.0), and change maps.

Hansen GFC v1.12 gives, per ~30 m pixel, the year tree cover was lost (2001-2024). It is produced by
a different team, sensor (Landsat) and method, so agreement is a meaningful sanity check. It is
annual and lags by months, which is exactly the gap continuous Sentinel-2 monitoring fills.
"""
import json
import math

import numpy as np

from .config import HANSEN_BASE, VALIDATION_DIR

YEARS = list(range(2019, 2025))


def tile_name(lon, lat):
    top = math.ceil(lat / 10) * 10
    left = math.floor(lon / 10) * 10
    return f"{abs(top):02d}{'N' if top >= 0 else 'S'}_{abs(left):03d}{'E' if left >= 0 else 'W'}"


def read_layer(layer, bounds, lon, lat):
    import rasterio
    from rasterio.windows import from_bounds

    url = f"{HANSEN_BASE}/Hansen_GFC-2024-v1.12_{layer}_{tile_name(lon, lat)}.tif"
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif"):
        with rasterio.open(url) as ds:
            win = from_bounds(*bounds, transform=ds.transform).round_offsets().round_lengths()
            return ds.read(1, window=win), ds.window_transform(win), url


def validate_site(site, observations_doc, summary, log=print):
    """Compare first-confirmation dates with Hansen loss years, pixel by pixel, inside the cell."""
    from rasterio.features import geometry_mask
    from rasterio.transform import rowcol
    from rasterio.warp import transform

    ring = site["geometry"]["coordinates"][0]
    xs, ys = [p[0] for p in ring], [p[1] for p in ring]
    lon, lat = site["centroid"]
    bounds = (min(xs), min(ys), max(xs), max(ys))
    loss, t, url = read_layer("lossyear", bounds, lon, lat)
    inside = geometry_mask([site["geometry"]], out_shape=loss.shape, transform=t, invert=True)

    grid = observations_doc["observations"][0]["grid"]
    from affine import Affine

    wt = Affine(*[float(v) for v in grid["window_transform_20m"]])
    first = summary["first_confirmed"]
    forest = summary["forest_mask"]
    rows_i, cols_i = np.nonzero(inside)
    centres_x = [t.c + (c + 0.5) * t.a for c in cols_i]
    centres_y = [t.f + (r + 0.5) * t.e for r in rows_i]
    ux, uy = transform("EPSG:4326", grid["crs"], centres_x, centres_y)
    rr, cc = rowcol(wt, ux, uy)
    h_px_ha = (abs(t.a) * 111_320 * math.cos(math.radians(lat))) * (abs(t.e) * 110_574) / 10_000

    both = ours = theirs = considered = 0
    for (r, c), (hr, hc) in zip(zip(rr, cc), zip(rows_i, cols_i)):
        if not (0 <= r < forest.shape[0] and 0 <= c < forest.shape[1]) or not forest[r, c]:
            continue
        considered += 1
        h = bool(19 <= loss[hr, hc] <= 24)
        o = bool(first[r, c] != "" and "2019-01-01" <= first[r, c] <= "2024-12-31")
        both += int(h and o)
        ours += int(o)
        theirs += int(h)
    years_hansen = {y: round(float(((loss == y - 2000) & inside).sum() * h_px_ha), 2) for y in YEARS}
    years_ours = {y: 0.0 for y in YEARS + [2025, 2026]}
    for d in first[first != ""]:
        y = int(d[:4])
        if y in years_ours:
            years_ours[y] += 0.04
    out = {
        "site_id": site["site_id"],
        "source": "Hansen et al. (2013) Global Forest Change v1.12 (2000-2024), UMD/Google, CC BY 4.0",
        "source_url": url,
        "method": ("Hansen ~30 m pixels inside the cell and inside CanopyProof's baseline forest mask. A Hansen pixel "
                   "counts as loss if its loss year is 2019-2024; a CanopyProof pixel counts if its loss was confirmed "
                   "(two consecutive disturbed looks) between 2019-01-01 and 2024-12-31."),
        "pixels_compared": considered,
        "hansen_loss_pixels": theirs,
        "canopyproof_confirmed_pixels": ours,
        "agreeing_pixels": both,
        "precision": round(both / ours, 3) if ours else None,
        "recall": round(both / theirs, 3) if theirs else None,
        "hansen_loss_ha_by_year": years_hansen,
        "canopyproof_confirmed_ha_by_first_year": {y: round(v, 2) for y, v in years_ours.items()},
    }
    VALIDATION_DIR.mkdir(parents=True, exist_ok=True)
    (VALIDATION_DIR / f"{site['site_id']}.json").write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8", newline="\n")
    log(f"  [{site['site_id']}] Hansen check: {considered} px, Hansen loss {theirs}, ours {ours}, agree {both} "
        f"(precision {out['precision']}, recall {out['recall']})")
    return out


def change_map(site, observations_doc, summary, out_dir, colour=(180, 83, 42)):
    """RGBA PNG of confirmed loss (terracotta) and the forest mask outline, with WGS84 bounds."""
    import rasterio
    from rasterio.warp import transform

    grid = observations_doc["observations"][0]["grid"]
    first, forest = summary["first_confirmed"], summary["forest_mask"]
    h, w = forest.shape
    rgba = np.zeros((4, h, w), dtype="uint8")
    lost = first != ""
    for band, value in enumerate(colour):
        rgba[band][lost] = value
    rgba[3][lost] = 215
    rgba[3][forest & ~lost] = 0
    t = [float(v) for v in grid["window_transform_20m"]]
    x0, y0 = t[2], t[5]
    x1, y1 = x0 + t[0] * w, y0 + t[4] * h
    lons, lats = transform(grid["crs"], "EPSG:4326", [x0, x1], [y1, y0])
    out_dir.mkdir(parents=True, exist_ok=True)
    png = out_dir / f"{site['site_id']}.png"
    with rasterio.open(png, "w", driver="PNG", width=w, height=h, count=4, dtype="uint8") as dst:
        dst.write(rgba)
    aux = png.with_suffix(".png.aux.xml")
    if aux.exists():
        aux.unlink()
    meta = {"site_id": site["site_id"], "bounds": [[lats[0], lons[0]], [lats[1], lons[1]]],
            "confirmed_pixels": int(lost.sum()), "forest_pixels": int(forest.sum()),
            "first_confirmed_by_year": {str(y): int(sum(1 for d in first[lost] if d.startswith(str(y))))
                                        for y in range(2019, 2027)}}
    png.with_suffix(".json").write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8", newline="\n")
    return meta

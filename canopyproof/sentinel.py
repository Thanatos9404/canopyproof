"""Sentinel-2 L2A monitoring-cell statistics from free, keyless Cloud-Optimized GeoTIFFs.

Source: Earth Search STAC API (Element 84), collection `sentinel-2-c1-l2a` (ESA Collection 1,
surface reflectance) on the AWS Open Data registry. Only the bytes around each monitoring cell are
read, through HTTP range requests.

Per acquisition and cell, on the native 20 m grid:
  * NDVI = (B08 - B04) / (B08 + B04), with B04 and B08 averaged 2x2 from 10 m
  * NBR  = (B08 - B12) / (B08 + B12), the Normalized Burn Ratio (B12 is native 20 m)
  * a Scene Classification (SCL) mask that keeps land-surface classes 2 (dark area), 4 (vegetation),
    5 (not vegetated) and 7 (unclassified) and drops no-data, saturated, cloud shadow, water, cloud,
    cirrus and snow. Fresh burn scars are often labelled 2 or 7 by Sen2Cor, so masking those would
    hide the very change this tool looks for; persistence checks absorb the extra noise.
  * a pixel digest (SHA-256 of the masked index rasters) so anyone can recompute and compare.
The masked rasters are cached as int16 (x10^4) for per-pixel change detection (forest.py).
"""
import hashlib
import io
import json
import math
import os
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone

import httpx
import numpy as np

from . import PROCESSING_VERSION
from .canonical import dec
from .config import CHIPS_DIR, OBSERVATIONS_DIR, RASTERS_DIR, S2_COLLECTION, STAC_API

GDAL_ENV = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif",
    "GDAL_HTTP_MAX_RETRY": "4",
    "GDAL_HTTP_RETRY_DELAY": "1",
    "GDAL_HTTP_MULTIRANGE": "YES",
    "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
    "VSI_CACHE": "TRUE",
}
for _k, _v in GDAL_ENV.items():
    os.environ.setdefault(_k, _v)

VALID_SCL = (2, 4, 5, 7)  # dark area, vegetation, not vegetated, unclassified (burn scars land in 2/7)
CLOUD_SHADOW_SCL = (3, 8, 9, 10)  # cloud shadow, cloud medium/high probability, thin cirrus
WATER_SCL = (6,)
SNOW_SCL = (11,)
MIN_VALID_FRACTION = 0.6
MIN_VALID_PIXELS = 50
NODATA_I16 = -32768
ASSETS = {"red": "B04", "nir": "B08", "swir22": "B12", "scl": "SCL"}
METHOD = {
    "processing_version": PROCESSING_VERSION,
    "source": "Earth Search STAC, sentinel-2-c1-l2a (ESA Collection 1, L2A surface reflectance)",
    "grid": "native 20 m Sentinel-2 grid; B04/B08 averaged 2x2 from 10 m",
    "ndvi": "(B08-B04)/(B08+B04)",
    "nbr": "(B08-B12)/(B08+B12)",
    "reflectance": "DN * scale + offset from STAC raster:bands (Collection 1: 0.0001, -0.1)",
    "valid_scl_classes": [2, 4, 5, 7],
    "pixel_rule": "20 m pixel centre inside the monitoring cell",
    "min_valid_fraction": "0.60",
}


class SceneError(RuntimeError):
    pass


# ----------------------------------------------------------------------------- STAC search

def search_items(geometry, start, end, max_cloud=70, client=None, log=None):
    """All Sentinel-2 C1 L2A items intersecting the cell, oldest first."""
    own = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(60, connect=15))
    body = {
        "collections": [S2_COLLECTION],
        "intersects": geometry,
        "datetime": f"{start}T00:00:00Z/{end}T23:59:59Z",
        "limit": 200,
        "query": {"eo:cloud_cover": {"lte": max_cloud}},
    }
    items, url, method = [], f"{STAC_API}/search", "POST"
    try:
        for _page in range(100):
            for attempt in range(4):
                try:
                    resp = client.post(url, json=body) if method == "POST" else client.get(url)
                    if resp.status_code in (429, 502, 503, 504):
                        raise httpx.HTTPStatusError("retryable", request=resp.request, response=resp)
                    resp.raise_for_status()
                    page = resp.json()
                    break
                except (httpx.HTTPError, ValueError):
                    if attempt == 3:
                        raise
                    time.sleep(2 * (attempt + 1))
            items.extend(page.get("features", []))
            nxt = next((link for link in page.get("links", []) if link.get("rel") == "next"), None)
            if not nxt:
                break
            url = nxt["href"]
            method = nxt.get("method", "GET").upper()
            if method == "POST":
                body = {**body, **nxt.get("body", {})} if nxt.get("merge") else nxt.get("body", body)
    finally:
        if own:
            client.close()
    if log:
        log(f"  STAC: {len(items)} items with scene cloud cover <= {max_cloud}%")
    return sorted(items, key=lambda it: it["properties"]["datetime"])


def preferred_tile(items):
    """Use one MGRS tile per cell so every acquisition samples the same pixel grid."""
    counts = Counter(it["properties"].get("grid:code") for it in items)
    if not counts:
        return None
    best = max(counts.values())
    return sorted(code for code, n in counts.items() if n == best)[0]


def select_items(items, today=None, history_per_month=3, recent_days=400):
    """Keep every recent acquisition and the clearest few per month for the multi-year record."""
    today = today or datetime.now(timezone.utc).date()
    tile = preferred_tile(items)
    items = [it for it in items if it["properties"].get("grid:code") == tile]
    by_day = {}
    for it in items:  # duplicates of the same acquisition (reprocessing): keep the latest update
        key = it["properties"]["datetime"][:10]
        if key not in by_day or it["properties"].get("updated", "") > by_day[key]["properties"].get("updated", ""):
            by_day[key] = it
    cutoff = (today - timedelta(days=recent_days)).isoformat()
    recent, history = [], defaultdict(list)
    for key, it in by_day.items():
        (recent.append(it) if key >= cutoff else history[key[:7]].append(it))
    chosen = list(recent)
    for month_items in history.values():
        month_items.sort(key=lambda it: (it["properties"].get("eo:cloud_cover", 100), it["properties"]["datetime"]))
        chosen.extend(month_items[:history_per_month])
    return sorted(chosen, key=lambda it: it["properties"]["datetime"]), tile


# ----------------------------------------------------------------------------- pixel maths

def scale_offset(item, asset_key):
    bands = item["assets"][asset_key].get("raster:bands") or [{}]
    scale = bands[0].get("scale")
    offset = bands[0].get("offset")
    if scale is None:
        scale = 0.0001
    if offset is None:
        baseline = item["properties"].get("s2:processing_baseline", "00.00")
        offset = -0.1 if baseline >= "04.00" else 0.0
    return float(scale), float(offset)


def to_i16(index):
    out = np.full(index.shape, NODATA_I16, dtype="<i2")
    ok = np.isfinite(index)
    out[ok] = np.rint(np.clip(index[ok], -1, 1) * 10_000).astype("<i2")
    return out


def from_i16(arr):
    out = arr.astype("float64") / 10_000
    out[arr == NODATA_I16] = np.nan
    return out


def compute_cell(red_dn, nir_dn, swir_dn, scl, inside, scales):
    """Pure function over aligned arrays: 10 m red/nir, 20 m swir22/scl, and the 20 m cell mask."""
    h, w = scl.shape
    if red_dn.shape != (2 * h, 2 * w) or nir_dn.shape != red_dn.shape or swir_dn.shape != scl.shape:
        raise SceneError("10 m and 20 m windows are not aligned")
    (rs, ro), (ns, no), (ss, so) = scales["red"], scales["nir"], scales["swir22"]

    def agg(dn, s, o):
        ok = (dn.reshape(h, 2, w, 2) > 0).all(axis=(1, 3))
        return (dn.astype("float64") * s + o).reshape(h, 2, w, 2).mean(axis=(1, 3)), ok

    red, red_ok = agg(red_dn, rs, ro)
    nir, nir_ok = agg(nir_dn, ns, no)
    swir = swir_dn.astype("float64") * ss + so
    cell_px = int(inside.sum())
    if cell_px == 0:
        raise SceneError("monitoring cell covers no 20 m pixel centres")
    ok = (inside & np.isin(scl, VALID_SCL) & red_ok & nir_ok & (swir_dn > 0)
          & (red >= 0) & (nir >= 0) & (swir >= 0) & ((red + nir) > 1e-6) & ((nir + swir) > 1e-6))
    with np.errstate(divide="ignore", invalid="ignore"):
        ndvi = np.where(ok, (nir - red) / (nir + red), np.nan)
        nbr = np.where(ok, (nir - swir) / (nir + swir), np.nan)
    valid = int(ok.sum())
    frac = valid / cell_px

    def share(classes):
        return dec(float((inside & np.isin(scl, classes)).sum()) / cell_px)

    quality = {
        "status": "clear" if frac >= MIN_VALID_FRACTION and valid >= MIN_VALID_PIXELS else "insufficient",
        "cell_pixels_20m": cell_px,
        "valid_pixels_20m": valid,
        "valid_fraction": dec(frac),
        "cloud_shadow_fraction": share(CLOUD_SHADOW_SCL),
        "water_fraction": share(WATER_SCL),
        "snow_fraction": share(SNOW_SCL),
        "nodata_fraction": share((0,)),
    }
    ndvi_i16, nbr_i16 = to_i16(ndvi), to_i16(nbr)
    digest = hashlib.sha256(ndvi_i16.tobytes() + nbr_i16.tobytes())

    def stats(v):
        v = v[np.isfinite(v)]
        if v.size < MIN_VALID_PIXELS:
            return None
        return {"mean": dec(v.mean()), "median": dec(np.median(v)), "p10": dec(np.percentile(v, 10)),
                "p90": dec(np.percentile(v, 90))}

    return {"quality": quality, "ndvi": stats(ndvi), "nbr": stats(nbr), "pixel_digest": digest,
            "ndvi_i16": ndvi_i16, "nbr_i16": nbr_i16}


# ----------------------------------------------------------------------------- scene reads

def _window_for(geom_utm, transform20, pad=1):
    from rasterio.windows import Window

    xs = [p[0] for p in geom_utm["coordinates"][0]]
    ys = [p[1] for p in geom_utm["coordinates"][0]]
    col0 = math.floor((min(xs) - transform20.c) / transform20.a) - pad
    col1 = math.ceil((max(xs) - transform20.c) / transform20.a) + pad
    row0 = math.floor((transform20.f - max(ys)) / -transform20.e) - pad
    row1 = math.ceil((transform20.f - min(ys)) / -transform20.e) + pad
    return Window(col0, row0, col1 - col0, row1 - row0)


def process_item(item, geometry):
    """Read the cell window of one acquisition; returns stats plus the int16 index rasters."""
    import rasterio
    from rasterio.features import geometry_mask
    from rasterio.warp import transform_geom
    from rasterio.windows import Window

    assets = item["assets"]
    with rasterio.Env(**GDAL_ENV):
        with rasterio.open(assets["scl"]["href"]) as ds:
            crs = ds.crs
            t20 = ds.transform
            geom_utm = transform_geom("EPSG:4326", crs, geometry)
            w20 = _window_for(geom_utm, t20)
            scl = ds.read(1, window=w20, boundless=True, fill_value=0)
            wt20 = ds.window_transform(w20)
        with rasterio.open(assets["swir22"]["href"]) as ds:
            if ds.transform != t20:
                raise SceneError("SWIR grid differs from SCL grid")
            swir = ds.read(1, window=w20, boundless=True, fill_value=0)
        w10 = Window(w20.col_off * 2, w20.row_off * 2, w20.width * 2, w20.height * 2)
        bands10 = {}
        for key in ("red", "nir"):
            with rasterio.open(assets[key]["href"]) as ds:
                if (ds.transform.c, ds.transform.f, ds.transform.a) != (t20.c, t20.f, t20.a / 2):
                    raise SceneError("10 m grid is not aligned with the 20 m grid")
                bands10[key] = ds.read(1, window=w10, boundless=True, fill_value=0)
    inside = geometry_mask([geom_utm], out_shape=scl.shape, transform=wt20, invert=True)
    scales = {k: scale_offset(item, k) for k in ("red", "nir", "swir22")}
    result = compute_cell(bands10["red"], bands10["nir"], swir, scl, inside, scales)

    grid = {"crs": crs.to_string(), "window_transform_20m": [dec(v, 2) for v in tuple(wt20)[:6]],
            "shape_20m": list(scl.shape)}
    digest = result.pop("pixel_digest")
    digest.update(json.dumps(grid, sort_keys=True).encode())
    props = item["properties"]
    observed = datetime.fromisoformat(props["datetime"].replace("Z", "+00:00")).astimezone(timezone.utc)
    return {
        "item_id": item["id"],
        "observed_at": observed.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "observed_unix": int(observed.timestamp()),
        "date": observed.date().isoformat(),
        "platform": props.get("platform"),
        "mgrs_tile": props.get("grid:code", "").replace("MGRS-", ""),
        "processing_baseline": props.get("s2:processing_baseline"),
        "scene_cloud_cover_pct": dec(props.get("eo:cloud_cover"), 2),
        "assets": {ASSETS[k]: assets[k]["href"] for k in ("red", "nir", "swir22", "scl")},
        "reflectance_scale_offset": {ASSETS[k]: [dec(s, 6), dec(o, 6)] for k, (s, o) in scales.items()},
        "grid": grid,
        **result,
        "inside": inside,
        "pixel_digest": "sha256:" + digest.hexdigest(),
    }


# ----------------------------------------------------------------------------- per-site fetch

def observations_path(site_id):
    return OBSERVATIONS_DIR / f"{site_id}.json"


def raster_path(site_id):
    return RASTERS_DIR / f"{site_id}.npz"


def load_observations(site_id):
    path = observations_path(site_id)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def load_rasters(site_id):
    """{"inside": bool[h,w], item_id: (ndvi_i16, nbr_i16)} from the compressed cache."""
    path = raster_path(site_id)
    if not path.exists():
        return None
    with np.load(path) as z:
        out = {"inside": z["inside"]}
        for key in z.files:
            if key.endswith("__ndvi"):
                item = key[:-6]
                out[item] = (z[key], z[item + "__nbr"])
    return out


def save_rasters(site_id, rasters):
    RASTERS_DIR.mkdir(parents=True, exist_ok=True)
    arrays = {"inside": rasters["inside"]}
    for item, value in rasters.items():
        if item == "inside":
            continue
        arrays[item + "__ndvi"], arrays[item + "__nbr"] = value
    buf = io.BytesIO()
    np.savez_compressed(buf, **arrays)
    raster_path(site_id).write_bytes(buf.getvalue())


def fetch_site(site, start, end, workers=8, max_cloud=70, history_per_month=3, recent_days=400, log=print):
    existing = load_observations(site["site_id"]) or {}
    known = {o["item_id"]: o for o in existing.get("observations", [])}
    rasters = load_rasters(site["site_id"]) or {}
    log(f"[{site['site_id']}] searching {start} .. {end}")
    items = search_items(site["geometry"], start, end, max_cloud=max_cloud, log=log)
    selected, tile = select_items(items, today=date.fromisoformat(end), history_per_month=history_per_month,
                                  recent_days=recent_days)
    todo = [it for it in selected if it["id"] not in known or it["id"] not in rasters]
    log(f"  tile {tile}: {len(selected)} acquisitions selected, {len(todo)} new to read")
    failures = done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(process_item, it, site["geometry"]): it for it in todo}
        for fut in as_completed(futures):
            it = futures[fut]
            try:
                obs = fut.result()
            except Exception as exc:  # network hiccup or unexpected scene: skip, never fabricate
                failures += 1
                log(f"  ! {it['id']}: {type(exc).__name__}: {str(exc)[:120]}")
                continue
            inside = obs.pop("inside")
            ndvi, nbr = obs.pop("ndvi_i16"), obs.pop("nbr_i16")
            if "inside" in rasters and rasters["inside"].shape != inside.shape:
                failures += 1
                log(f"  ! {it['id']}: grid shape changed, skipped")
                continue
            rasters["inside"] = inside
            rasters[it["id"]] = (ndvi, nbr)
            known[it["id"]] = obs
            done += 1
            if done % 25 == 0:
                log(f"  {done}/{len(todo)} read")
    observations = sorted(known.values(), key=lambda o: o["observed_at"])
    out = {
        "site_id": site["site_id"],
        "geometry_hash": site["geometry_hash"],
        "stac_api": STAC_API,
        "collection": S2_COLLECTION,
        "mgrs_tile": tile,
        "method": METHOD,
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "observations": observations,
    }
    OBSERVATIONS_DIR.mkdir(parents=True, exist_ok=True)
    observations_path(site["site_id"]).write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8", newline="\n")
    save_rasters(site["site_id"], rasters)
    clear = sum(1 for o in observations if o["quality"]["status"] == "clear")
    log(f"  saved {len(observations)} acquisitions ({clear} clear), {failures} failed reads")
    return out


def make_chip(site, item, pad_m=300, name=None):
    """True-colour PNG around the cell for the web app, plus its WGS84 bounds."""
    import rasterio
    from rasterio.warp import transform, transform_geom
    from rasterio.windows import Window

    with rasterio.Env(**GDAL_ENV):
        with rasterio.open(item["assets"]["visual"]["href"]) as ds:
            geom = transform_geom("EPSG:4326", ds.crs, site["geometry"])
            xs = [p[0] for p in geom["coordinates"][0]]
            ys = [p[1] for p in geom["coordinates"][0]]
            t = ds.transform
            col0 = math.floor((min(xs) - pad_m - t.c) / t.a)
            col1 = math.ceil((max(xs) + pad_m - t.c) / t.a)
            row0 = math.floor((t.f - max(ys) - pad_m) / -t.e)
            row1 = math.ceil((t.f - min(ys) + pad_m) / -t.e)
            win = Window(col0, row0, col1 - col0, row1 - row0)
            rgb = ds.read(window=win, boundless=True, fill_value=0)
            wt = ds.window_transform(win)
            x0, y0 = wt.c, wt.f
            x1, y1 = x0 + wt.a * win.width, y0 + wt.e * win.height
            lons, lats = transform(ds.crs, "EPSG:4326", [x0, x1], [y1, y0])
    CHIPS_DIR.mkdir(parents=True, exist_ok=True)
    stem = name or site["site_id"]
    png = CHIPS_DIR / f"{stem}.png"
    with rasterio.open(png, "w", driver="PNG", width=rgb.shape[2], height=rgb.shape[1], count=3, dtype="uint8") as out:
        out.write(rgb)
    aux = png.with_suffix(".png.aux.xml")
    if aux.exists():
        aux.unlink()
    meta = {"site_id": site["site_id"], "item_id": item["id"], "date": item["properties"]["datetime"][:10],
            "bounds": [[lats[0], lons[0]], [lats[1], lons[1]]],
            "attribution": "Contains modified Copernicus Sentinel data"}
    png.with_suffix(".json").write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8", newline="\n")
    return meta

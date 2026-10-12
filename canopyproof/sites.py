"""Monitoring cells and carbon projects: canonical geometry, on-chain keys, land-claim cells, area."""
import json
import math

from eth_abi import encode as abi_encode
from eth_hash.auto import keccak

from .canonical import canonical_bytes, dec
from .config import PROJECTS_FILE, SITES_FILE

LAND_GRID_DEG = 0.01  # ~1.1 km land-claim grid used by the on-chain double-claim check


def canonical_ring(coordinates):
    """Closed, counter-clockwise ring starting at the lowest (lon, lat) vertex, 6 decimals (~0.1 m).

    Rotated or reversed copies of the same polygon therefore produce the same hash.
    """
    ring = [[round(float(x), 6), round(float(y), 6)] for x, y in coordinates]
    if ring[0] == ring[-1]:
        ring = ring[:-1]
    if len(ring) < 3 or len({tuple(p) for p in ring}) != len(ring):
        raise ValueError("a polygon needs at least three distinct vertices")
    signed = sum(ring[i][0] * ring[(i + 1) % len(ring)][1] - ring[(i + 1) % len(ring)][0] * ring[i][1]
                 for i in range(len(ring)))
    if signed == 0:
        raise ValueError("degenerate polygon")
    if signed < 0:
        ring.reverse()
    start = min(range(len(ring)), key=lambda i: tuple(ring[i]))
    ring = ring[start:] + ring[:start]
    return ring + [ring[0]]


def geometry_bytes(geometry):
    if geometry.get("type") != "Polygon" or len(geometry.get("coordinates", [])) != 1:
        raise ValueError("monitoring cells must be single-ring Polygons")
    ring = canonical_ring(geometry["coordinates"][0])
    return canonical_bytes({"type": "Polygon", "coordinates": [[[f"{x:.6f}", f"{y:.6f}"] for x, y in ring]]})


def geometry_hash(geometry) -> str:
    return "0x" + keccak(geometry_bytes(geometry)).hex()


def site_key(geometry, salt_hex: str) -> str:
    """Salted commitment to a monitoring cell: keccak256(abi.encode("canopyproof.site/1", geometryHash, salt))."""
    salt = bytes.fromhex(salt_hex[2:] if salt_hex.startswith("0x") else salt_hex)
    if len(salt) != 32:
        raise ValueError("salt must be 32 bytes")
    gh = bytes.fromhex(geometry_hash(geometry)[2:])
    return "0x" + keccak(abi_encode(["string", "bytes32", "bytes32"], ["canopyproof.site/1", gh, salt])).hex()


def project_key(project_id: str) -> str:
    """Public registry identifiers (e.g. ACR273) hash to the key used on-chain."""
    return "0x" + keccak(abi_encode(["string", "string"], ["canopyproof.project/1", project_id])).hex()


def land_cell_key(lat_idx: int, lon_idx: int) -> str:
    return "0x" + keccak(abi_encode(["string", "int32", "int32"], ["canopyproof.cell/1", lat_idx, lon_idx])).hex()


def land_cells(geometry, grid=LAND_GRID_DEG):
    """Grid cells (floor(lat/grid), floor(lon/grid)) touched by the polygon's bounding box."""
    ring = geometry["coordinates"][0]
    xs, ys = [p[0] for p in ring], [p[1] for p in ring]
    cells = []
    for i in range(math.floor(min(ys) / grid), math.floor(max(ys) / grid) + 1):
        for j in range(math.floor(min(xs) / grid), math.floor(max(xs) / grid) + 1):
            cells.append({"lat_idx": i, "lon_idx": j, "key": land_cell_key(i, j)})
    return cells


def geodesic_area_m2(ring):
    """Spherical-excess area on the WGS84 authalic sphere; adequate for kilometre-sized cells."""
    radius = 6371007.2
    pts = [(math.radians(x), math.radians(y)) for x, y in ring]
    if pts[0] == pts[-1]:
        pts = pts[:-1]
    total = 0.0
    for i in range(len(pts)):
        lon1, lat1 = pts[i]
        lon2, lat2 = pts[(i + 1) % len(pts)]
        total += (lon2 - lon1) * (2 + math.sin(lat1) + math.sin(lat2))
    return abs(total) * radius * radius / 2


def centroid(ring):
    pts = ring[:-1] if ring[0] == ring[-1] else ring
    return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)


def utm_epsg(lon, lat):
    zone = int((lon + 180) // 6) + 1
    return (32600 if lat >= 0 else 32700) + zone


def square_cell(lon, lat, size_m=1000.0):
    """A size_m x size_m square in local UTM around (lon, lat), returned as a WGS84 polygon."""
    from rasterio.warp import transform

    epsg = f"EPSG:{utm_epsg(lon, lat)}"
    (x,), (y,) = transform("EPSG:4326", epsg, [lon], [lat])
    h = size_m / 2
    xs, ys = [x - h, x + h, x + h, x - h], [y + h, y + h, y - h, y - h]
    lons, lats = transform(epsg, "EPSG:4326", xs, ys)
    ring = [[round(a, 6), round(b, 6)] for a, b in zip(lons, lats)]
    return {"type": "Polygon", "coordinates": [ring + [ring[0]]]}


def load_sites(path=SITES_FILE):
    data = json.loads(path.read_text(encoding="utf-8"))
    sites = []
    for feature in data["features"]:
        props = feature["properties"]
        geometry = feature["geometry"]
        ring = canonical_ring(geometry["coordinates"][0])
        geometry = {"type": "Polygon", "coordinates": [ring]}
        sites.append({
            **props,
            "geometry": geometry,
            "geometry_hash": geometry_hash(geometry),
            "site_key": site_key(geometry, props["salt"]),
            "project_key": project_key(props["project_id"]),
            "area_ha": dec(geodesic_area_m2(ring) / 10_000, 2),
            "centroid": centroid(ring),
            "land_cells": land_cells(geometry),
        })
    return sites


def get_site(site_id, sites=None):
    for site in sites or load_sites():
        if site["site_id"] == site_id:
            return site
    raise KeyError(f"unknown site {site_id}")


def load_projects(path=PROJECTS_FILE, sites=None):
    """Projects with their credit batches and the monitoring cells that sample them."""
    data = json.loads(path.read_text(encoding="utf-8"))
    sites = sites or load_sites()
    out = []
    for p in data["projects"]:
        own = [s for s in sites if s["project_id"] == p["project_id"]]
        out.append({**p, "project_key": project_key(p["project_id"]), "sites": own})
    return out

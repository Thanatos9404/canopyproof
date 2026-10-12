"""Sample plots: canonical geometry, privacy-preserving plot keys and area."""
import json
import math

from eth_abi import encode as abi_encode
from eth_hash.auto import keccak

from .canonical import canonical_bytes, dec
from .config import PLOTS_FILE


def canonical_ring(coordinates):
    """Closed, counter-clockwise ring starting at the lowest (lon, lat) vertex, 6 decimals (~0.1 m).

    Rotated or reversed copies of the same polygon therefore produce the same hash.
    """
    ring = [[round(float(x), 6), round(float(y), 6)] for x, y in coordinates]
    if ring[0] == ring[-1]:
        ring = ring[:-1]
    if len(ring) < 3 or len({tuple(p) for p in ring}) != len(ring):
        raise ValueError("a plot needs at least three distinct vertices")
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
        raise ValueError("plots must be single-ring Polygons")
    ring = canonical_ring(geometry["coordinates"][0])
    # Coordinates as fixed 6-decimal strings keep the canonical form float-free.
    return canonical_bytes({"type": "Polygon", "coordinates": [[[f"{x:.6f}", f"{y:.6f}"] for x, y in ring]]})


def geometry_hash(geometry) -> str:
    return "0x" + keccak(geometry_bytes(geometry)).hex()


def plot_key(geometry, salt_hex: str) -> str:
    """Salted commitment used on-chain instead of coordinates.

    plotKey = keccak256(abi.encode("fieldproof.plot/1", geometryHash, salt)). Without the salt,
    nobody can confirm a guessed boundary; with it, the farmer can prove which field a record is about.
    """
    salt = bytes.fromhex(salt_hex[2:] if salt_hex.startswith("0x") else salt_hex)
    if len(salt) != 32:
        raise ValueError("salt must be 32 bytes")
    gh = bytes.fromhex(geometry_hash(geometry)[2:])
    return "0x" + keccak(abi_encode(["string", "bytes32", "bytes32"], ["fieldproof.plot/1", gh, salt])).hex()


def geodesic_area_m2(ring):
    """Spherical-excess area on the WGS84 authalic sphere; adequate for field-sized polygons."""
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


def load_plots(path=PLOTS_FILE):
    data = json.loads(path.read_text(encoding="utf-8"))
    plots = []
    for feature in data["features"]:
        props = feature["properties"]
        geometry = feature["geometry"]
        ring = canonical_ring(geometry["coordinates"][0])
        plots.append({
            **props,
            "geometry": {"type": "Polygon", "coordinates": [ring]},
            "geometry_hash": geometry_hash(geometry),
            "plot_key": plot_key(geometry, props["salt"]),
            "area_ha": dec(geodesic_area_m2(ring) / 10_000, 4),
            "centroid": centroid(ring),
        })
    return plots


def get_plot(plot_id, plots=None):
    for plot in plots or load_plots():
        if plot["plot_id"] == plot_id:
            return plot
    raise KeyError(f"unknown plot {plot_id}")

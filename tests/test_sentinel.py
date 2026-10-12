import numpy as np
import pytest

from fieldproof.sentinel import SceneError, compute_indices, select_items

SCALES = {"red": (0.0001, -0.1), "nir": (0.0001, -0.1), "swir16": (0.0001, -0.1)}


def scene(h20=4, w20=4, red=1100, nir=4100, swir=2100, scl=4):
    return {
        "red_dn": np.full((2 * h20, 2 * w20), red, dtype="uint16"),
        "nir_dn": np.full((2 * h20, 2 * w20), nir, dtype="uint16"),
        "swir_dn": np.full((h20, w20), swir, dtype="uint16"),
        "scl": np.full((h20, w20), scl, dtype="uint8"),
        "inside10": np.ones((2 * h20, 2 * w20), dtype=bool),
        "inside20": np.ones((h20, w20), dtype=bool),
        "scales": SCALES,
    }


def test_indices_apply_the_collection1_offset():
    out = compute_indices(**scene())
    # reflectance: red 0.01, nir 0.31, swir 0.11
    assert out["ndvi"]["mean"] == "0.9375"  # (0.31-0.01)/(0.32)
    assert out["ndmi"]["mean"] == "0.4762"  # (0.31-0.11)/(0.42)
    assert out["quality"]["status"] == "clear"
    assert out["quality"]["valid_fraction"] == "1.0000"


def test_clouds_shadows_and_water_are_masked():
    s = scene()
    s["scl"][:2, :] = 9  # cloud, high probability, top half
    s["scl"][2, :] = 6  # water
    out = compute_indices(**s)
    q = out["quality"]
    assert q["cloud_shadow_fraction"] == "0.5000"
    assert q["water_fraction"] == "0.2500"
    assert q["valid_fraction"] == "0.2500"
    assert q["status"] == "insufficient"
    assert out["ndvi"]["mean"] == "0.9375"  # stats still describe the few valid pixels


def test_only_pixels_inside_the_plot_count():
    s = scene()
    s["inside10"][:, :4] = False
    s["red_dn"][:, :4] = 9000  # bright pixels outside the plot must not affect the result
    out = compute_indices(**s)
    assert out["quality"]["plot_pixels_10m"] == 32
    assert out["ndvi"]["mean"] == "0.9375"


def test_nodata_and_alignment_errors():
    s = scene()
    s["red_dn"][:] = 0
    out = compute_indices(**s)
    assert out["quality"]["status"] == "insufficient" and out["ndvi"] is None
    bad = scene()
    bad["swir_dn"] = np.ones((3, 4), dtype="uint16")
    with pytest.raises(SceneError):
        compute_indices(**bad)


def test_pixel_digest_is_deterministic_and_sensitive():
    a = compute_indices(**scene())["pixel_digest"].hexdigest()
    assert a == compute_indices(**scene())["pixel_digest"].hexdigest()
    s = scene()
    s["nir_dn"][0, 0] = 4000
    assert compute_indices(**s)["pixel_digest"].hexdigest() != a


def item(day, tile="MGRS-43PGS", cloud=10.0):
    return {"id": f"S2_{tile}_{day}", "properties": {"datetime": f"{day}T05:00:00Z", "grid:code": tile,
                                                    "eo:cloud_cover": cloud, "updated": "2026-01-01"}}


def test_selection_keeps_one_tile_recent_scenes_and_clearest_history():
    from datetime import date

    items = [item("2026-09-01"), item("2026-09-06"), item("2026-09-06", tile="MGRS-43PGT"),
             item("2020-03-02", cloud=50), item("2020-03-12", cloud=5), item("2020-03-22", cloud=20),
             item("2020-03-27", cloud=1), item("2020-04-01")]
    chosen, tile = select_items(items, today=date(2026, 10, 1), history_per_month=2, recent_days=100)
    days = [c["properties"]["datetime"][:10] for c in chosen]
    assert tile == "MGRS-43PGS"
    assert days == ["2020-03-12", "2020-03-27", "2020-04-01", "2026-09-01", "2026-09-06"]

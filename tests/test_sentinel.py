import numpy as np
import pytest

from canopyproof.sentinel import SceneError, compute_cell, from_i16, select_items, to_i16

SCALES = {"red": (0.0001, -0.1), "nir": (0.0001, -0.1), "swir22": (0.0001, -0.1)}


def scene(h=4, w=4, red=1100, nir=4100, swir=2100, scl=4):
    return {
        "red_dn": np.full((2 * h, 2 * w), red, dtype="uint16"),
        "nir_dn": np.full((2 * h, 2 * w), nir, dtype="uint16"),
        "swir_dn": np.full((h, w), swir, dtype="uint16"),
        "scl": np.full((h, w), scl, dtype="uint8"),
        "inside": np.ones((h, w), dtype=bool),
        "scales": SCALES,
    }


@pytest.fixture(autouse=True)
def few_pixels(monkeypatch):
    import canopyproof.sentinel as s

    monkeypatch.setattr(s, "MIN_VALID_PIXELS", 1)


def test_indices_apply_the_collection1_offset():
    out = compute_cell(**scene())
    # reflectance: red 0.01, nir 0.31, swir22 0.11
    assert out["ndvi"]["mean"] == "0.9375"  # (0.31-0.01)/0.32
    assert out["nbr"]["mean"] == "0.4762"  # (0.31-0.11)/0.42
    assert out["quality"]["status"] == "clear" and out["quality"]["valid_fraction"] == "1.0000"


def test_clouds_water_and_snow_are_masked_but_dark_burn_scars_are_kept():
    sc = scene()
    sc["scl"][0, :] = 9  # cloud
    sc["scl"][1, :] = 6  # water
    sc["scl"][2, :] = 2  # "dark area": often a fresh burn scar
    sc["scl"][3, :2] = 11  # snow
    out = compute_cell(**sc)
    q = out["quality"]
    assert q["cloud_shadow_fraction"] == "0.2500" and q["water_fraction"] == "0.2500" and q["snow_fraction"] == "0.1250"
    assert q["valid_fraction"] == "0.3750" and q["status"] == "insufficient"
    assert np.isfinite(from_i16(out["ndvi_i16"])[2]).all()


def test_only_pixels_inside_the_cell_count():
    sc = scene()
    sc["inside"][:, :2] = False
    sc["red_dn"][:, :4] = 9000  # bright pixels outside the cell must not affect the result
    out = compute_cell(**sc)
    assert out["quality"]["cell_pixels_20m"] == 8 and out["ndvi"]["mean"] == "0.9375"


def test_alignment_errors_and_digest():
    bad = scene()
    bad["swir_dn"] = np.ones((3, 4), dtype="uint16")
    with pytest.raises(SceneError):
        compute_cell(**bad)
    a = compute_cell(**scene())["pixel_digest"].hexdigest()
    assert a == compute_cell(**scene())["pixel_digest"].hexdigest()
    changed = scene()
    changed["nir_dn"][0, 0] = 4000
    assert compute_cell(**changed)["pixel_digest"].hexdigest() != a


def test_int16_round_trip():
    v = np.array([[0.5, np.nan], [-1.2, 0.12345]])
    back = from_i16(to_i16(v))
    assert back[0, 0] == 0.5 and np.isnan(back[0, 1]) and back[1, 0] == -1.0 and back[1, 1] == 0.1234


def item(day, tile="MGRS-10TFN", cloud=10.0):
    return {"id": f"S2_{tile}_{day}", "properties": {"datetime": f"{day}T05:00:00Z", "grid:code": tile,
                                                    "eo:cloud_cover": cloud, "updated": "2026-01-01"}}


def test_selection_keeps_one_tile_recent_scenes_and_clearest_history():
    from datetime import date

    items = [item("2026-09-01"), item("2026-09-06"), item("2026-09-06", tile="MGRS-10TFM"),
             item("2020-03-02", cloud=50), item("2020-03-12", cloud=5), item("2020-03-22", cloud=20),
             item("2020-03-27", cloud=1), item("2020-04-01")]
    chosen, tile = select_items(items, today=date(2026, 10, 1), history_per_month=2, recent_days=100)
    assert tile == "MGRS-10TFN"
    assert [c["properties"]["datetime"][:10] for c in chosen] == [
        "2020-03-12", "2020-03-27", "2020-04-01", "2026-09-01", "2026-09-06"]

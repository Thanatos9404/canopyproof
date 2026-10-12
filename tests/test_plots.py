import pytest

from fieldproof.plots import canonical_ring, geodesic_area_m2, geometry_hash, load_plots, plot_key

SQUARE = [[77.0, 14.0], [77.0009, 14.0], [77.0009, 14.0009], [77.0, 14.0009], [77.0, 14.0]]
SALT = "0x" + "11" * 32


def test_rotated_or_reversed_rings_hash_the_same():
    g = {"type": "Polygon", "coordinates": [SQUARE]}
    rotated = {"type": "Polygon", "coordinates": [SQUARE[2:-1] + SQUARE[:2] + [SQUARE[2]]]}
    reversed_ = {"type": "Polygon", "coordinates": [list(reversed(SQUARE))]}
    assert geometry_hash(g) == geometry_hash(rotated) == geometry_hash(reversed_)


def test_plot_key_depends_on_salt():
    g = {"type": "Polygon", "coordinates": [SQUARE]}
    assert plot_key(g, SALT) != plot_key(g, "0x" + "22" * 32)
    with pytest.raises(ValueError):
        plot_key(g, "0x1234")


def test_area_of_a_small_square():
    # 0.0009 degrees is about 100 m at 14 N, so roughly 0.94 ha.
    area = geodesic_area_m2(canonical_ring(SQUARE))
    assert 9_000 < area < 10_000


def test_degenerate_polygons_are_rejected():
    with pytest.raises(ValueError):
        canonical_ring([[0, 0], [1, 1], [0, 0]])
    with pytest.raises(ValueError):
        canonical_ring([[0, 0], [1, 1], [2, 2], [0, 0]])


def test_sample_plots_load_with_keys_and_plausible_areas():
    plots = load_plots()
    assert len(plots) == 4
    for p in plots:
        assert p["plot_key"].startswith("0x") and len(p["plot_key"]) == 66
        assert 0.5 < float(p["area_ha"]) < 3

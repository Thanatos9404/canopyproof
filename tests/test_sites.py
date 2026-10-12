import pytest

from canopyproof.sites import (canonical_ring, geodesic_area_m2, geometry_hash, land_cells, load_projects, load_sites,
                               project_key, site_key, square_cell)

SQUARE = [[-121.0, 42.0], [-120.99, 42.0], [-120.99, 42.009], [-121.0, 42.009], [-121.0, 42.0]]
SALT = "0x" + "11" * 32


def test_rotated_or_reversed_rings_hash_the_same():
    g = {"type": "Polygon", "coordinates": [SQUARE]}
    rotated = {"type": "Polygon", "coordinates": [SQUARE[2:-1] + SQUARE[:2] + [SQUARE[2]]]}
    reversed_ = {"type": "Polygon", "coordinates": [list(reversed(SQUARE))]}
    assert geometry_hash(g) == geometry_hash(rotated) == geometry_hash(reversed_)


def test_keys_depend_on_salt_and_project_id():
    g = {"type": "Polygon", "coordinates": [SQUARE]}
    assert site_key(g, SALT) != site_key(g, "0x" + "22" * 32)
    assert project_key("ACR273") != project_key("ACR274")
    with pytest.raises(ValueError):
        site_key(g, "0x1234")


def test_square_cell_is_one_square_kilometre():
    cell = square_cell(-120.91, 42.808, 1000)
    assert 99.0 < geodesic_area_m2(cell["coordinates"][0]) / 10_000 < 101.0


def test_land_cells_cover_the_bounding_box():
    cells = land_cells({"type": "Polygon", "coordinates": [SQUARE]})
    assert {(c["lat_idx"], c["lon_idx"]) for c in cells} == {(4200, -12100), (4200, -12099)}
    assert len({c["key"] for c in cells}) == 2


def test_degenerate_polygons_are_rejected():
    with pytest.raises(ValueError):
        canonical_ring([[0, 0], [1, 1], [0, 0]])
    with pytest.raises(ValueError):
        canonical_ring([[0, 0], [1, 1], [2, 2], [0, 0]])


def test_sample_sites_and_projects_load():
    sites = load_sites()
    assert {s["site_id"] for s in sites} == {"KE-FIRE-01", "KE-STAND-01", "ACR189-01", "AMZ-FRONTIER-01"}
    for s in sites:
        assert s["site_key"].startswith("0x") and len(s["site_key"]) == 66
        assert 99 < float(s["area_ha"]) < 101 and s["land_cells"]
    projects = {p["project_id"]: p for p in load_projects(sites=sites)}
    assert len(projects["ACR273"]["sites"]) == 2
    assert projects["DEMO-AMZ"]["real_project"] is False

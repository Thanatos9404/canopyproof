from collections import Counter
from datetime import date

from fieldproof.analytics import analyze, classify
from synthetic import DROUGHT, Weather, observations


def by_date(obs, results):
    return {date.fromisoformat(o["date"]): r for o, r in zip(obs, results)}


def test_synthetic_drought_is_flagged_and_normal_seasons_are_not():
    obs = observations()
    res = by_date(obs, analyze(obs, Weather()))
    drought = [r for d, r in res.items() if DROUGHT[0] <= d <= DROUGHT[1] and r["stress_level"] != "insufficient_data"]
    assert drought and all(r["stress_level"] == "alert" for r in drought[1:])
    assert any(r["vci_pct"] is not None and float(r["vci_pct"]) < 35 for r in drought)
    previous_season = [r for d, r in res.items() if date(2025, 8, 1) <= d <= date(2025, 9, 30)]
    assert Counter(r["stress_level"] for r in previous_season)["alert"] == 0


def test_off_season_bare_soil_is_not_stress():
    obs = observations()
    res = by_date(obs, analyze(obs, Weather()))
    april = [r for d, r in res.items() if d.year >= 2023 and d.month == 4 and r["stress_level"] != "insufficient_data"]
    assert april and all(r["stress_level"] == "normal" for r in april)
    assert all(r["crop_expected"] is False for r in april)


def test_results_are_as_of_the_acquisition_no_look_ahead():
    obs = observations()
    full = analyze(obs, Weather())
    cut = next(i for i, o in enumerate(obs) if o["date"] >= "2025-07-01")
    prefix = analyze(obs[:cut], Weather())
    assert prefix == full[:cut]


def test_cloudy_acquisitions_are_insufficient_data():
    obs = observations()
    res = analyze(obs, Weather())
    for o, r in zip(obs, res):
        if o["quality"]["status"] != "clear":
            assert r["stress_level"] == "insufficient_data" and r["stress_code"] == 0


def test_classify_rules():
    assert classify(True, 20.0, -0.5, -30.0, False)[0] == "alert"
    assert classify(True, 20.0, -0.5, 5.0, False)[0] == "watch"
    assert classify(True, 60.0, -2.5, -25.0, False)[0] == "alert"
    assert classify(True, 60.0, -2.5, -25.0, False, crop_expected=False)[0] == "normal"
    assert classify(True, 60.0, 0.1, -80.0, False)[0] == "normal"  # rain alone is context, not stress
    assert classify(True, None, None, None, True)[0] == "watch"
    assert classify(False, 10.0, -5.0, -90.0, True)[0] == "insufficient_data"

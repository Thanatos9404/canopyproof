from collections import Counter
from datetime import date

from canopyproof.forest import analyze_site
from synthetic import BASELINE, CLEARING, MONTHS, SMOKE, scenes


def run(obs=None, rasters=None):
    if obs is None:
        obs, rasters = scenes()
    return obs, analyze_site(obs, rasters, *BASELINE, monitoring_months=MONTHS)


def test_clearing_becomes_a_suspected_reversal_after_two_looks():
    obs, (res, summary) = run()
    by_date = {o["date"]: r for o, r in zip(obs, res)}
    after = [(d, r) for d, r in by_date.items() if d >= CLEARING.isoformat() and r["status"] != "insufficient_data"]
    assert after[0][1]["status"] == "watch"  # one look is not enough
    assert after[1][1]["status"] == "reversal_suspected"
    assert float(after[-1][1]["cumulative_loss_fraction"]) == 0.5
    assert summary["confirmed_ha"] == 200 * 0.04


def test_smoke_and_baseline_years_are_not_loss():
    obs, (res, _) = run()
    before = [r for o, r in zip(obs, res) if o["date"] < CLEARING.isoformat() and r["status"] != "insufficient_data"]
    assert before and all(r["cumulative_loss_fraction"] == "0.0000" for r in before)
    hazy = [r for o, r in zip(obs, res) if SMOKE[0].isoformat() <= o["date"] <= SMOKE[1].isoformat()
            and r["status"] != "insufficient_data"]
    assert hazy and all(r["disturbed_fraction"] == "0.0000" for r in hazy)


def test_cloudy_and_off_season_scenes_are_insufficient():
    obs, (res, _) = run()
    for o, r in zip(obs, res):
        if o["quality"]["status"] != "clear":
            assert r["status"] == "insufficient_data" and r["status_code"] == 0
    assert Counter(r["status"] for r in res)["insufficient_data"] > 0


def test_results_are_as_of_each_acquisition():
    obs, rasters = scenes()
    _, (full, _) = run(obs, rasters)
    cut = next(i for i, o in enumerate(obs) if o["date"] >= "2023-08-15")
    _, (prefix, _) = run(obs[:cut], rasters)
    assert prefix == full[:cut]


def test_no_baseline_means_no_claims():
    obs, rasters = scenes(start=date(2023, 6, 1))
    _, (res, summary) = run(obs, rasters)
    assert summary["forest_pixels"] == 0
    assert all(r["status"] == "insufficient_data" for r in res)

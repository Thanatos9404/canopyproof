from datetime import date, timedelta

from fieldproof.weather import WeatherSeries, imd_category


def series(days_dry_at_end=0, end=date(2026, 10, 6)):
    start = date(1991, 1, 1)
    n = (end - start).days + 1
    precip = [2.0] * n
    for i in range(days_dry_at_end):
        precip[n - 1 - i] = 0.0
    return WeatherSeries({"start": start.isoformat(), "end": end.isoformat(), "precip_mm": precip, "et0_mm": [4.0] * n})


def test_normal_conditions():
    f = series().features(date(2026, 9, 15))
    assert f["status"] == "available"
    assert f["rain_60d_mm"] == "120.0" and f["rain_60d_normal_mm"] == "120.0"
    assert f["rain_60d_departure_pct"] == "0.0"
    assert f["rain_60d_percentile"] == "100.0"
    assert f["water_balance_60d_mm"] == "-120.0"


def test_dry_spell_is_a_deficit():
    w = series(days_dry_at_end=30)
    f = w.features(date(2026, 10, 7))  # window ends 6 Oct, the last 30 of 60 days are dry
    assert f["rain_60d_departure_pct"] == "-50.0"
    assert f["rain_30d_departure_pct"] == "-100.0"
    assert f["rain_60d_percentile"] == "0.0"


def test_unavailable_beyond_the_record():
    w = series()
    assert w.features(date(2026, 10, 8))["status"] == "unavailable"
    assert w.features(date(2026, 10, 7))["status"] == "available"


def test_dry_season_departure_is_not_applicable():
    start, end = date(1991, 1, 1), date(2026, 10, 6)
    n = (end - start).days + 1
    w = WeatherSeries({"start": start.isoformat(), "end": end.isoformat(), "precip_mm": [0.1] * n, "et0_mm": [5.0] * n})
    f = w.features(date(2026, 3, 1))
    assert f["rain_60d_departure_pct"] is None and f["departure_applicable"] is False


def test_imd_categories():
    assert imd_category(None) is None
    assert imd_category(-100) == "no rain"
    assert imd_category(-60) == "large deficient"
    assert imd_category(-59.6) == "large deficient"
    assert imd_category(-20) == "deficient"
    assert imd_category(-19.4) == "normal"
    assert imd_category(25) == "excess"
    assert imd_category(75) == "large excess"

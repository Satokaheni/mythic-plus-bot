"""Tests for the price-watch state, detection, and formatting logic."""

from watchlist import format_gold, median, percentile


def test_format_gold_full_denominations():
    assert format_gold(11234) == "1g 12s 34c"


def test_format_gold_drops_empty_leading_units():
    assert format_gold(1100) == "11s"
    assert format_gold(10000) == "1g"


def test_format_gold_zero():
    assert format_gold(0) == "0c"


def test_percentile_linear_interpolation():
    assert percentile([1, 2, 3, 4], 25) == 1.75
    assert percentile([1, 2, 3, 4], 50) == 2.5


def test_percentile_single_value():
    assert percentile([500], 25) == 500.0


def test_median_matches_p50():
    assert median([1, 2, 3, 4]) == 2.5


from datetime import datetime, timezone

from watchlist import Watch, evaluate


def _watch(percentile=35.0):
    return Watch(item_id=21877, label="Netherweave Cloth", percentile=percentile)


def test_evaluate_insufficient_history():
    sig = evaluate(1000, 500, [1000, 900, 1100], _watch())  # only 3 days < MIN_HISTORY_DAYS
    assert sig.enough_history is False
    assert sig.fired is False


def test_evaluate_fires_on_dip_into_low_band():
    # 14 flat days at 1000, then a dip to 700 — 700 is below the p35 band.
    history = [1000] * 13 + [800]
    sig = evaluate(700, 500, history, _watch())
    assert sig.enough_history is True
    assert sig.fired is True
    assert sig.median == 1000


def test_evaluate_does_not_fire_when_price_is_typical():
    history = [1000] * 14
    sig = evaluate(1000, 500, history, _watch())
    assert sig.enough_history is True
    assert sig.fired is False


def test_evaluate_uses_only_last_window_days():
    # 30 days: first 16 are cheap noise, last 14 are expensive. Window is the last 14.
    history = [100] * 16 + [1000] * 14
    sig = evaluate(950, 500, history, _watch())
    assert sig.median == 1000  # noise outside the window is ignored

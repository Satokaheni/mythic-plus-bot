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

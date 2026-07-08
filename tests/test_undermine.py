"""Tests for the Undermine Exchange API client parsers."""

from undermine import NowResult, _parse_daily, _parse_now


def test_parse_now_extracts_price_and_quantity():
    data = {"result": {"lastSeen": "2026-07-08T15:33:06Z", "price": 1100, "quantity": 446461, "auctions": []}}
    assert _parse_now(data) == NowResult(price=1100, quantity=446461)


def test_parse_now_returns_none_when_not_listed():
    # An item not currently for sale has no "price" key, only a seen timestamp.
    data = {"result": {"lastSeen": "2026-07-08T15:33:06Z"}}
    assert _parse_now(data) is None


def test_parse_daily_returns_prices_in_order():
    data = {
        "result": {
            "daily": [
                {"day": "2022-09-04", "price": 1800, "quantity": 644421},
                {"day": "2022-09-05", "price": 1100, "quantity": 727189},
                {"day": "2022-09-06", "price": 900, "quantity": 708004},
            ]
        }
    }
    assert _parse_daily(data) == [1800, 1100, 900]


def test_parse_daily_handles_empty():
    assert _parse_daily({"result": {}}) == []

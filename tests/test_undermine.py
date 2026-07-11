"""Tests for the Undermine Exchange API client parsers."""

import asyncio

from undermine import NowResult, _get_json, _parse_daily, _parse_now


def test_parse_now_extracts_price_and_quantity():
    data = {"result": {"lastSeen": "2026-07-08T15:33:06Z", "price": 1100, "quantity": 446461, "auctions": []}}
    assert _parse_now(data) == NowResult(price=1100, quantity=446461)


def test_parse_now_returns_none_when_not_listed():
    # An item not currently for sale has no "price" key, only a seen timestamp.
    data = {"result": {"lastSeen": "2026-07-08T15:33:06Z"}}
    assert _parse_now(data) is None


def test_parse_now_captures_auction_ladder_sorted():
    data = {
        "result": {
            "price": 1100,
            "quantity": 500,
            "auctions": [
                {"price": 1300, "quantity": 5},
                {"price": 1100, "quantity": 43},
                {"price": 1200, "quantity": 2},
            ],
        }
    }
    nr = _parse_now(data)
    assert nr.price == 1100
    assert nr.auctions == ((1100, 43), (1200, 2), (1300, 5))  # (price, quantity), cheapest first


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


class _FakeResp:
    """Minimal async context manager mimicking aiohttp's response object."""

    def __init__(self, status=200, json_exc=None):
        self.status = status
        self._json_exc = json_exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def json(self):
        if self._json_exc:
            raise self._json_exc
        return {}


class _FakeSession:
    """get() returns a resp context manager, or raises the given exception."""

    def __init__(self, resp=None, get_exc=None):
        self._resp = resp
        self._get_exc = get_exc

    def get(self, url, headers=None):
        if self._get_exc:
            raise self._get_exc
        return self._resp


def test_get_json_returns_none_on_timeout():
    # asyncio.TimeoutError is not a subclass of aiohttp.ClientError — must be
    # caught explicitly so a slow request degrades to None instead of raising.
    session = _FakeSession(get_exc=asyncio.TimeoutError())
    assert asyncio.run(_get_json(session, "/x")) is None


def test_get_json_returns_none_on_bad_json():
    # json.JSONDecodeError/UnicodeDecodeError (ValueError subclasses) from a
    # corrupt/truncated 200 body must also degrade to None, not raise.
    session = _FakeSession(resp=_FakeResp(status=200, json_exc=ValueError("bad json")))
    assert asyncio.run(_get_json(session, "/x")) is None


def test_api_key_read_lazily(monkeypatch):
    # bot.py imports undermine before calling load_dotenv(), so the key must
    # be read at call time, not captured as a module-level global at import time.
    import undermine
    monkeypatch.setenv("UNDERMINE_API_KEY", "LAZY123")
    assert undermine._api_key() == "LAZY123"


def test_region_defaults_to_us(monkeypatch):
    import undermine
    monkeypatch.delenv("UNDERMINE_REGION", raising=False)
    assert undermine._region() == "us"


def test_region_read_lazily(monkeypatch):
    import undermine
    monkeypatch.setenv("UNDERMINE_REGION", "eu")
    assert undermine._region() == "eu"

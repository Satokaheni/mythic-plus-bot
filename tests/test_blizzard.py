"""Tests for the pure parsers of the Blizzard Game Data client."""

from blizzard import _parse_auctions, _parse_item_info, _parse_realm_index, _parse_realm_name, _parse_token


def test_parse_token():
    assert _parse_token({"access_token": "abc", "token_type": "bearer", "expires_in": 86399}) == ("abc", 86399)


def test_parse_realm_index_extracts_ids():
    data = {"connected_realms": [
        {"href": "https://us.api.blizzard.com/data/wow/connected-realm/121?namespace=dynamic-us"},
        {"href": "https://us.api.blizzard.com/data/wow/connected-realm/1146?namespace=dynamic-us"},
    ]}
    assert _parse_realm_index(data) == [121, 1146]


def test_parse_auctions_returns_list():
    data = {"auctions": [{"id": 1, "item": {"id": 111}, "buyout": 5000, "quantity": 1}]}
    assert _parse_auctions(data) == data["auctions"]
    assert _parse_auctions({}) == []


def test_parse_item_info_recipe_flag():
    recipe = {"name": "Recipe: Widget", "item_class": {"id": 9, "name": "Recipe"}}
    assert _parse_item_info(recipe) == __import__("blizzard").ItemInfo("Recipe: Widget", True)
    mount = {"name": "Reins of Something", "item_class": {"id": 15, "name": "Miscellaneous"}}
    info = _parse_item_info(mount)
    assert info.name == "Reins of Something" and info.is_recipe is False


def test_parse_realm_name():
    assert _parse_realm_name({"realms": [{"name": "Illidan"}, {"name": "Other"}]}) == "Illidan"
    assert _parse_realm_name({"realms": []}) == ""

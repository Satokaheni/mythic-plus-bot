"""Tests for the pure parsers of the Blizzard Game Data client."""

from blizzard import (
    _parse_auctions,
    _parse_equipment,
    _parse_item_info,
    _parse_realm_index,
    _parse_realm_name,
    _parse_token,
    _parse_token_price,
)


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


def test_parse_token_price():
    data = {"_links": {}, "last_updated_timestamp": 1756328236000, "price": 2758280000}
    assert _parse_token_price(data) == (2758280000, 1756328236000)


def test_parse_token_price_missing_price_returns_none():
    assert _parse_token_price({"_links": {}, "last_updated_timestamp": 1756328236000}) is None
    assert _parse_token_price({}) is None


def test_parse_equipment_reads_enchants_and_sockets():
    data = {
        "equipped_items": [
            {
                "slot": {"type": "HEAD"},
                "name": "Baleful Grave-Knight's Casque",
                "item": {"id": 246001},
                "item_class": {"id": 4, "name": "Armor"},
                "enchantments": [
                    {
                        "display_string": "Enchanted: Enchant Helm |A:Professions-ChatIcon-Quality-12-Tier2:20:20|a",
                        "enchantment_id": 7991,
                        "enchantment_slot": {"id": 0, "type": "PERMANENT"},
                    }
                ],
                "sockets": [
                    {"socket_type": {"type": "PRISMATIC"}, "item": {"id": 240983, "name": "Eversong Diamond"}},
                    {"socket_type": {"type": "PRISMATIC"}},
                ],
            }
        ]
    }
    items = _parse_equipment(data)
    assert len(items) == 1
    item = items[0]
    assert item.slot == "HEAD"
    assert item.item_id == 246001
    assert item.item_class_id == 4
    assert item.sockets == (240983, None)
    assert len(item.enchants) == 1
    assert item.enchants[0].slot_type == "PERMANENT"
    assert item.enchants[0].enchantment_id == 7991


def test_parse_equipment_tolerates_missing_and_malformed_entries():
    data = {
        "equipped_items": [
            "not a dict",
            {"name": "no slot key"},
            {"slot": {"type": "BACK"}, "name": "Cloak", "item": {"id": 5}},
        ]
    }
    items = _parse_equipment(data)
    assert len(items) == 1
    assert items[0].slot == "BACK"
    assert items[0].enchants == ()
    assert items[0].sockets == ()
    assert _parse_equipment({}) == []


def test_parse_item_info_reads_quality():
    data = {"name": "Flawless Deadly Peridot", "item_class": {"id": 3}, "quality": {"type": "EPIC"}}
    info = _parse_item_info(data)
    assert info.name == "Flawless Deadly Peridot"
    assert info.is_recipe is False
    assert info.quality == "EPIC"


def test_parse_item_info_quality_defaults_to_empty():
    info = _parse_item_info({"name": "Mystery Item", "item_class": {"id": 9}})
    assert info.is_recipe is True
    assert info.quality == ""

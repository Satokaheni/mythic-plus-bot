"""Tests for the auction-snipe state, detection, and formatting logic."""

from snipelist import PET_ITEM_ID, apply_anti_spam, best_price_for, cheapest, parse_gold


def test_parse_gold_whole_and_decimal():
    assert parse_gold("100") == 1_000_000        # 100g -> copper
    assert parse_gold("2.5") == 25_000           # 2.5g -> copper


def test_parse_gold_rejects_bad_input():
    assert parse_gold("abc") is None
    assert parse_gold("0") is None
    assert parse_gold("-5") is None


def test_parse_gold_rejects_non_finite():
    assert parse_gold("nan") is None
    assert parse_gold("inf") is None
    assert parse_gold("-inf") is None
    assert parse_gold("1e400") is None  # overflows float() to inf


def test_best_price_for_item_picks_cheapest_buyout():
    auctions = [
        {"item": {"id": 111}, "buyout": 5000, "quantity": 1},
        {"item": {"id": 111}, "buyout": 3000, "quantity": 2},
        {"item": {"id": 222}, "buyout": 10, "quantity": 1},
    ]
    assert best_price_for(auctions, "item", 111) == (3000, 2)


def test_best_price_for_ignores_bid_only_and_missing():
    auctions = [
        {"item": {"id": 111}, "bid": 100, "quantity": 1},   # no buyout
        {"item": {"id": 111}, "buyout": 0, "quantity": 1},  # zero buyout
    ]
    assert best_price_for(auctions, "item", 111) is None


def test_best_price_for_pet_matches_species():
    auctions = [
        {"item": {"id": PET_ITEM_ID, "pet_species_id": 3022}, "buyout": 9000, "quantity": 1},
        {"item": {"id": PET_ITEM_ID, "pet_species_id": 9999}, "buyout": 10, "quantity": 1},
        {"item": {"id": 3022}, "buyout": 1, "quantity": 1},  # a normal item, not the pet
    ]
    assert best_price_for(auctions, "pet", 3022) == (9000, 1)


def test_cheapest_across_realms():
    assert cheapest({101: (5000, 1), 102: (3000, 4), 103: (8000, 2)}) == (3000, 4, 102)
    assert cheapest({}) is None


def test_apply_anti_spam_state_machine():
    assert apply_anti_spam(True, "armed") == (True, "alerted")    # fire -> DM once
    assert apply_anti_spam(True, "alerted") == (False, "alerted")  # stay silent
    assert apply_anti_spam(False, "alerted") == (False, "armed")   # re-arm
    assert apply_anti_spam(False, "armed") == (False, "armed")


from datetime import datetime, timezone

from snipelist import Snipe, Snipelist, Subscriber


def test_subscribe_creates_one_record_many_subscribers():
    sl = Snipelist()
    sl.subscribe(1, "item", 111, 5000, "Widget", is_recipe=False)
    sl.subscribe(2, "item", 111, 8000, "Widget", is_recipe=False)
    assert len(sl.all()) == 1                       # one record
    snipe = sl.get("item", 111)
    assert set(snipe.subscribers) == {1, 2}         # two subscribers
    assert snipe.subscribers[1].target_copper == 5000
    assert snipe.subscribers[2].target_copper == 8000
    assert sl.watched_keys() == [("item", 111)]     # deduped to one fetch key


def test_resubscribe_updates_own_target_in_place():
    sl = Snipelist()
    sl.subscribe(1, "item", 111, 5000, "Widget", is_recipe=False)
    sl.subscribe(1, "item", 111, 4000, "Widget", is_recipe=False)
    assert sl.get("item", 111).subscribers[1].target_copper == 4000
    assert len(sl.get("item", 111).subscribers) == 1


def test_unsubscribe_removes_only_caller_keeps_others():
    sl = Snipelist()
    sl.subscribe(1, "item", 111, 5000, "Widget", is_recipe=False)
    sl.subscribe(2, "item", 111, 8000, "Widget", is_recipe=False)
    assert sl.unsubscribe(1, "item", 111) is True
    snipe = sl.get("item", 111)
    assert set(snipe.subscribers) == {2}            # record survives for #2
    assert sl.unsubscribe(1, "item", 111) is False  # already gone


def test_unsubscribe_last_deletes_record():
    sl = Snipelist()
    sl.subscribe(2, "item", 111, 8000, "Widget", is_recipe=False)
    assert sl.unsubscribe(2, "item", 111) is True
    assert sl.get("item", 111) is None
    assert sl.all() == []


def test_for_owner_only_returns_subscribed():
    sl = Snipelist()
    sl.subscribe(1, "item", 111, 5000, "A", is_recipe=False)
    sl.subscribe(2, "item", 222, 5000, "B", is_recipe=False)
    assert [s.key_id for s in sl.for_owner(1)] == [111]


def test_save_load_round_trip(tmp_path):
    path = str(tmp_path / "snipes.json")
    sl = Snipelist()
    sl.subscribe(1, "item", 111, 5000, "Widget", is_recipe=True)
    sl.subscribe(2, "pet", 3022, 9000, "Critter", is_recipe=False)
    snipe = sl.get("item", 111)
    snipe.banker_state = "alerted"
    snipe.last_realm, snipe.last_price = 121, 4200
    sl.save(path)

    loaded = Snipelist.load(path)
    a = loaded.get("item", 111)
    assert a.is_recipe is True
    assert a.banker_state == "alerted"
    assert a.last_realm == 121 and a.last_price == 4200
    assert a.subscribers[1].target_copper == 5000
    assert loaded.get("pet", 3022).subscribers[2].target_copper == 9000


def test_load_missing_file_is_empty():
    assert Snipelist.load("does_not_exist_snipes.json").all() == []


def test_load_skips_malformed_entry(tmp_path):
    import json
    path = str(tmp_path / "snipes.json")
    valid = Snipe("item", 111, "Widget", subscribers={1: Subscriber(5000)}).to_dict()
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "snipes": [{"junk": True}, valid]}, f)
    sl = Snipelist.load(path)
    assert len(sl.all()) == 1 and sl.get("item", 111) is not None

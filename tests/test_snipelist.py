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

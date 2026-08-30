"""Tests for the price-watch state, detection, and formatting logic."""

from watchlist import Watch, Signal, Watchlist, auto_tune, bulk_price, evaluate, format_alert, format_gold, format_watch_line, in_alert_window, median, parse_watch_command, percentile, process_signal, suggest_buy


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


from datetime import datetime, timedelta, timezone


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


def _now():
    return datetime(2026, 7, 8, tzinfo=timezone.utc)


def test_process_signal_alerts_once_on_entry():
    w = _watch()
    sig = Signal(fired=True, enough_history=True, price=700, median=1000, low_band=800, quantity=5)
    assert process_signal(w, sig, _now()) is True
    assert w.state == "alerted"
    assert len(w.alert_history) == 1


def test_process_signal_silent_while_still_low():
    w = _watch()
    w.state = "alerted"
    sig = Signal(fired=True, enough_history=True, price=700, median=1000, low_band=800, quantity=5)
    assert process_signal(w, sig, _now()) is False
    assert w.alert_history == []


def test_process_signal_rearms_above_median():
    w = _watch()
    w.state = "alerted"
    sig = Signal(fired=False, enough_history=True, price=1100, median=1000, low_band=800, quantity=5)
    assert process_signal(w, sig, _now()) is False
    assert w.state == "idle"


def test_process_signal_stays_alerted_between_low_band_and_median():
    w = _watch()
    w.state = "alerted"
    sig = Signal(fired=False, enough_history=True, price=900, median=1000, low_band=800, quantity=5)
    assert process_signal(w, sig, _now()) is False
    assert w.state == "alerted"


def test_process_signal_ignores_insufficient_history():
    w = _watch()
    sig = Signal(fired=False, enough_history=False, price=700, median=0.0, low_band=0.0, quantity=5)
    assert process_signal(w, sig, _now()) is False
    assert w.state == "idle"


def test_auto_tune_loosens_when_starved():
    now = _now()
    w = Watch(item_id=1, label="x", percentile=35.0, added_at=now - timedelta(days=30))
    auto_tune(w, now)
    assert w.percentile == 40.0  # +5, no alerts ever
    assert w.last_adjusted_at == now


def test_auto_tune_skips_new_watch():
    now = _now()
    w = Watch(item_id=1, label="x", percentile=35.0, added_at=now - timedelta(days=2))
    auto_tune(w, now)
    assert w.percentile == 35.0  # too young to loosen


def test_auto_tune_tightens_when_flooded():
    now = _now()
    w = Watch(item_id=1, label="x", percentile=35.0, added_at=now - timedelta(days=30))
    w.alert_history = [now - timedelta(days=1), now - timedelta(days=2)]  # 2 in last 7 days
    auto_tune(w, now)
    assert w.percentile == 34.0  # -1


def test_auto_tune_respects_24h_interval():
    now = _now()
    w = Watch(item_id=1, label="x", percentile=35.0, added_at=now - timedelta(days=30))
    w.last_adjusted_at = now - timedelta(hours=5)
    auto_tune(w, now)
    assert w.percentile == 35.0  # too soon, unchanged


def test_auto_tune_clamps_to_max():
    now = _now()
    w = Watch(item_id=1, label="x", percentile=48.0, added_at=now - timedelta(days=30))
    auto_tune(w, now)
    assert w.percentile == 50.0  # 48 + 5 clamped to 50


def test_auto_tune_clamps_to_min():
    now = _now()
    w = Watch(item_id=1, label="x", percentile=10.0, added_at=now - timedelta(days=30))
    w.alert_history = [now - timedelta(days=1), now - timedelta(days=2)]
    auto_tune(w, now)
    assert w.percentile == 10.0  # already at floor


def test_add_creates_and_updates():
    wl = Watchlist()
    w = wl.add(21877, "Netherweave Cloth")
    assert w.item_id == 21877
    # Adding the same id again updates the label, not a duplicate.
    wl.add(21877, "Netherweave (renamed)")
    assert len(wl.all()) == 1
    assert wl.get(21877).label == "Netherweave (renamed)"


def test_remove():
    wl = Watchlist()
    wl.add(1, "a")
    assert wl.remove(1) is True
    assert wl.remove(1) is False
    assert wl.all() == []


def test_save_load_round_trip(tmp_path):
    path = str(tmp_path / "watches.json")
    wl = Watchlist()
    w = wl.add(21877, "Netherweave Cloth")
    w.percentile = 42.0
    w.state = "alerted"
    w.alert_history = [datetime(2026, 7, 1, tzinfo=timezone.utc)]
    w.last_adjusted_at = datetime(2026, 7, 2, tzinfo=timezone.utc)
    wl.save(path)

    loaded = Watchlist.load(path)
    lw = loaded.get(21877)
    assert lw.label == "Netherweave Cloth"
    assert lw.percentile == 42.0
    assert lw.state == "alerted"
    assert lw.alert_history == [datetime(2026, 7, 1, tzinfo=timezone.utc)]
    assert lw.last_adjusted_at == datetime(2026, 7, 2, tzinfo=timezone.utc)


def test_load_missing_file_is_empty():
    assert Watchlist.load(str("does_not_exist_watches.json")).all() == []


def test_load_skips_malformed_entry_keeps_valid(tmp_path):
    import json
    path = str(tmp_path / "watches.json")
    valid = {
        "item_id": 21877,
        "label": "Cloth",
        "percentile": 35.0,
        "state": "idle",
        "alert_history": [],
        "last_adjusted_at": None,
        "added_at": datetime(2026, 7, 1, tzinfo=timezone.utc).isoformat(),
    }
    malformed = {"label": "no item_id or added_at"}  # from_dict will KeyError
    # Malformed entry listed FIRST: under the old whole-loop try/except, an
    # exception on this entry aborted the loop before the valid entry (which
    # comes after) was ever processed, dropping it too. Ordering this way
    # actually exercises the "one bad entry drops everything after it" bug.
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "watches": [malformed, valid]}, f)
    wl = Watchlist.load(path)
    assert len(wl.all()) == 1
    assert wl.get(21877) is not None


def test_load_whole_file_corruption_is_empty(tmp_path):
    path = str(tmp_path / "watches.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write("{not valid json")
    assert Watchlist.load(path).all() == []


def test_format_alert_contains_key_facts():
    w = _watch()
    sig = Signal(fired=True, enough_history=True, price=700, median=1000.0, low_band=800.0, quantity=1234)
    text = format_alert(w, sig)
    assert "Netherweave Cloth" in text
    assert "21877" in text
    assert "30% below" in text  # (1 - 700/1000) * 100
    assert "1,234" in text
    assert "wowhead.com/item=21877" in text


def test_format_watch_line_insufficient_data():
    w = _watch()
    line = format_watch_line(w, None)
    assert "insufficient data" in line
    assert "21877" in line


def test_format_watch_line_with_signal():
    w = _watch()
    sig = Signal(fired=False, enough_history=True, price=1000, median=1000.0, low_band=800.0, quantity=5)
    line = format_watch_line(w, sig)
    assert "Netherweave Cloth" in line
    assert "p35" in line


def test_suggest_buy_walks_ladder_up_to_ceiling():
    # Buy the two cheap tiers (<= ceiling 150); the 200-priced tier is above the ceiling.
    units, cost = suggest_buy([(100, 50), (120, 30), (200, 100)], 150, 10000)
    assert (units, cost) == (80, 8600)


def test_suggest_buy_stops_at_budget():
    # Budget 8000: buys all 50 at 100 (5000), then 25 of the 120 tier (3000) -> budget exhausted.
    units, cost = suggest_buy([(100, 50), (120, 100)], 200, 8000)
    assert (units, cost) == (75, 8000)


def test_suggest_buy_stops_at_ceiling():
    units, cost = suggest_buy([(100, 50), (300, 50)], 200, 100000)
    assert (units, cost) == (50, 5000)


def test_suggest_buy_empty_or_unaffordable():
    assert suggest_buy([], 200, 10000) == (0, 0)
    assert suggest_buy([(100, 50)], 200, 50) == (0, 0)  # budget below one unit


def test_in_alert_window():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    cst = ZoneInfo("America/Chicago")
    assert in_alert_window(datetime(2026, 7, 11, 10, 0, tzinfo=cst)) is True
    assert in_alert_window(datetime(2026, 7, 11, 23, 30, tzinfo=cst)) is True
    assert in_alert_window(datetime(2026, 7, 11, 15, 0, tzinfo=cst)) is True
    assert in_alert_window(datetime(2026, 7, 11, 4, 0, tzinfo=cst)) is False
    assert in_alert_window(datetime(2026, 7, 11, 9, 59, tzinfo=cst)) is False
    assert in_alert_window(datetime(2026, 7, 11, 0, 0, tzinfo=cst)) is False


def test_format_alert_includes_budget_buy_line():
    w = _watch()
    sig = Signal(
        fired=True, enough_history=True, price=700, median=1000.0, low_band=800.0, quantity=1234,
        auctions=((700, 10), (750, 20), (900, 50)),
    )
    text = format_alert(w, sig, budget_copper=100000)
    # ladder <=800: 10 @700 + 20 @750 = 30 units for 22000 copper; the 900 tier is above low band.
    assert "Buy up to" in text
    assert "30" in text


def test_format_alert_no_budget_line_when_zero():
    w = _watch()
    sig = Signal(fired=True, enough_history=True, price=700, median=1000.0, low_band=800.0, quantity=5,
                 auctions=((700, 10),))
    assert "Buy up to" not in format_alert(w, sig)  # default budget_copper=0 -> no buy line


# ---------------------------------------------------------------------------
# bulk_price — VWAP to fill a target quantity by walking the auction ladder
# ---------------------------------------------------------------------------


def test_bulk_price_exact_fill_single_tier():
    fillable, vwap, avail = bulk_price(((100, 100),), 100)
    assert fillable is True
    assert vwap == 100.0
    assert avail == 100


def test_bulk_price_vwap_blends_tiers():
    # 20 @ 700 + 80 @ 1000 = 14000 + 80000 = 94000 over 100 units -> 940 VWAP.
    fillable, vwap, avail = bulk_price(((700, 20), (1000, 500)), 100)
    assert fillable is True
    assert vwap == 940.0
    assert avail == 520


def test_bulk_price_not_fillable_when_too_thin():
    # Only 20 units listed, but we want 100 -> no bulk opportunity.
    fillable, vwap, avail = bulk_price(((700, 20),), 100)
    assert fillable is False
    assert avail == 20


def test_bulk_price_boundary_listed_equals_target():
    fillable, vwap, avail = bulk_price(((700, 60), (900, 40)), 100)
    assert fillable is True
    # 60*700 + 40*900 = 42000 + 36000 = 78000 / 100 = 780.
    assert vwap == 780.0
    assert avail == 100


def test_bulk_price_empty_ladder():
    fillable, vwap, avail = bulk_price((), 100)
    assert fillable is False
    assert avail == 0


def test_bulk_price_target_one_is_cheapest_lot():
    fillable, vwap, avail = bulk_price(((700, 20), (1000, 500)), 1)
    assert fillable is True
    assert vwap == 700.0  # a single unit at the cheapest price


# ---------------------------------------------------------------------------
# evaluate — bulk-aware detection + depth gate
# ---------------------------------------------------------------------------


def test_evaluate_suppresses_thin_cheap_lot():
    # The regression this whole change targets. Low band sits at 800: the raw 700 floor
    # is below it (legacy would fire), but filling 100 units costs 940 VWAP — above the
    # band — so the bulk-aware detector correctly stays quiet.
    history = [800] * 14
    sig = evaluate(700, 520, history, _watch(), auctions=((700, 20), (1000, 500)), target_qty=100)
    assert sig.enough_history is True
    assert sig.fired is False
    assert sig.price == 940  # watched price is the bulk VWAP, not the 700 floor


def test_evaluate_fires_on_genuinely_deep_cheap_ladder():
    # 200 units available at 700; filling 100 costs 700 VWAP, below the ~1000 band.
    history = [1000] * 14
    sig = evaluate(700, 200, history, _watch(), auctions=((700, 200),), target_qty=100)
    assert sig.fired is True
    assert sig.price == 700


def test_evaluate_depth_gate_blocks_when_not_fillable():
    history = [1000] * 14
    sig = evaluate(700, 20, history, _watch(), auctions=((700, 20),), target_qty=100)
    assert sig.fired is False
    assert sig.fillable is False
    assert sig.units_available == 20
    assert sig.target_qty == 100


def test_evaluate_without_ladder_keeps_legacy_min_price_behavior():
    # No auctions passed -> degrade to the old cheapest-lot behavior (no depth gate).
    history = [1000] * 13 + [800]
    sig = evaluate(700, 500, history, _watch())
    assert sig.fired is True
    assert sig.fillable is True
    assert sig.price == 700


# ---------------------------------------------------------------------------
# Watch.target_qty — config + persistence
# ---------------------------------------------------------------------------


def test_watch_target_qty_round_trips(tmp_path):
    path = str(tmp_path / "watches.json")
    wl = Watchlist()
    wl.add(21877, "Cloth", target_qty=250)
    wl.save(path)
    lw = Watchlist.load(path).get(21877)
    assert lw.target_qty == 250


def test_legacy_watch_without_target_qty_loads_as_none(tmp_path):
    import json
    path = str(tmp_path / "watches.json")
    legacy = {
        "item_id": 21877, "label": "Cloth", "percentile": 35.0, "state": "idle",
        "alert_history": [], "last_adjusted_at": None,
        "added_at": datetime(2026, 7, 1, tzinfo=timezone.utc).isoformat(),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "watches": [legacy]}, f)
    assert Watchlist.load(path).get(21877).target_qty is None


def test_add_sets_target_qty_and_updates_in_place():
    wl = Watchlist()
    wl.add(1, "a", target_qty=100)
    assert wl.get(1).target_qty == 100
    # Re-watching with a new target updates it in place.
    wl.add(1, "a", target_qty=250)
    assert wl.get(1).target_qty == 250
    # Re-watching WITHOUT a target leaves the existing one untouched.
    wl.add(1, "renamed")
    assert wl.get(1).target_qty == 250
    assert wl.get(1).label == "renamed"


# ---------------------------------------------------------------------------
# parse_watch_command — !watch argument grammar (incl. -x flag)
# ---------------------------------------------------------------------------


def test_parse_watch_single_with_label():
    assert parse_watch_command(["12345", "Rousing", "Fire"]) == {
        "kind": "single", "item_id": 12345, "label": "Rousing Fire", "target_qty": None,
    }


def test_parse_watch_single_no_label():
    assert parse_watch_command(["12345"]) == {
        "kind": "single", "item_id": 12345, "label": None, "target_qty": None,
    }


def test_parse_watch_multi_item():
    assert parse_watch_command(["100", "200", "300"]) == {
        "kind": "multi", "items": [(100, None), (200, None), (300, None)],
    }


def test_parse_watch_multi_item_with_per_item_targets():
    # Each -x binds to the item id immediately before it.
    assert parse_watch_command(["212283", "-x100", "212284", "-x200"]) == {
        "kind": "multi", "items": [(212283, 100), (212284, 200)],
    }


def test_parse_watch_multi_item_mixed_targets():
    # Glued -x, and an item with no target (falls back to the global default -> None here).
    assert parse_watch_command(["100", "-x50", "200"]) == {
        "kind": "multi", "items": [(100, 50), (200, None)],
    }


def test_parse_watch_multi_item_rejects_stray_label():
    # A free-text label is ambiguous across several items -> error.
    assert parse_watch_command(["100", "-x50", "200", "Netherweave"])["kind"] == "error"


def test_parse_watch_x_flag_glued():
    assert parse_watch_command(["12345", "-x200", "Rousing", "Fire"]) == {
        "kind": "single", "item_id": 12345, "label": "Rousing Fire", "target_qty": 200,
    }


def test_parse_watch_x_flag_glued_no_label():
    assert parse_watch_command(["12345", "-x200"]) == {
        "kind": "single", "item_id": 12345, "label": None, "target_qty": 200,
    }


def test_parse_watch_spaced_x_is_error():
    # The quantity must be glued to -x. A spaced `-x 200` in the multi-item form would
    # silently swallow the next item id as the quantity, so it's rejected outright.
    assert parse_watch_command(["12345", "-x", "200"])["kind"] == "error"


def test_parse_watch_spaced_x_does_not_eat_next_item():
    # Reproduces the reported bug: `243734 -x 245880 -x200` — the spaced -x has no glued
    # number, so `245880` must NOT be consumed as 243734's quantity. It's an error, not a
    # silent misparse that drops item 245880.
    result = parse_watch_command(["243734", "-x", "245880", "-x200"])
    assert result["kind"] == "error"
    assert result.get("reason") == "bad_flag"


def test_parse_watch_bad_x_is_error():
    assert parse_watch_command(["12345", "-x"])["kind"] == "error"
    assert parse_watch_command(["12345", "-xabc"])["kind"] == "error"
    assert parse_watch_command(["12345", "-x0"])["kind"] == "error"


def test_parse_watch_empty_or_nonnumeric_is_error():
    assert parse_watch_command([])["kind"] == "error"
    assert parse_watch_command(["Rousing", "Fire"])["kind"] == "error"


# ---------------------------------------------------------------------------
# formatters — bulk-aware display
# ---------------------------------------------------------------------------


def test_format_watch_line_insufficient_depth():
    w = _watch()
    sig = Signal(
        fired=False, enough_history=True, price=700, median=1000.0, low_band=800.0,
        quantity=20, fillable=False, units_available=20, target_qty=100,
    )
    line = format_watch_line(w, sig)
    assert "insufficient depth" in line
    assert "20" in line and "100" in line


def test_format_alert_shows_bulk_target_in_line():
    w = _watch()
    sig = Signal(
        fired=True, enough_history=True, price=940, median=1000.0, low_band=800.0,
        quantity=520, fillable=True, units_available=520, target_qty=100,
    )
    text = format_alert(w, sig)
    assert format_gold(940) in text  # the bulk VWAP, not a thin floor
    assert "100" in text  # the target quantity is surfaced

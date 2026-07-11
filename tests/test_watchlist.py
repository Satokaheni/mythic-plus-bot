"""Tests for the price-watch state, detection, and formatting logic."""

from watchlist import Watch, Signal, Watchlist, auto_tune, evaluate, format_alert, format_gold, format_watch_line, in_alert_window, median, percentile, process_signal, suggest_buy


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

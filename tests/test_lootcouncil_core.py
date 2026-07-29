"""Tests for lootcouncil.models and lootcouncil.config — pure helpers, no network."""

import json

import pytest

from lootcouncil.config import DEFAULT_ROLE_WEIGHTS, Config, ConfigError
from lootcouncil.models import (
    DPS,
    HEALER,
    TANK,
    Character,
    RawMetrics,
    character_key,
    normalize_role,
    server_slug,
)

# ---------------------------------------------------------------------------
# models
# ---------------------------------------------------------------------------


def test_server_slug_strips_apostrophes_and_spaces():
    assert server_slug("Mal'Ganis") == "malganis"
    assert server_slug("Aerie Peak") == "aeriepeak"
    assert server_slug("Illidan") == "illidan"


def test_character_key_is_lowercase_and_joins_name_and_realm():
    assert character_key("Thrall", "Mal'Ganis") == "thrall-malganis"


def test_character_key_property_matches_helper():
    char = Character(name="Thrall", realm="Mal'Ganis", role=TANK, class_name="Shaman")
    assert char.key == character_key("Thrall", "Mal'Ganis")


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Tank", TANK),
        ("tank", TANK),
        ("Healer", HEALER),
        ("Heal", HEALER),
        ("DPS", DPS),
        ("Melee", DPS),
        ("Ranged", DPS),
        ("", DPS),
        (None, DPS),
        ("something-unknown", DPS),
    ],
)
def test_normalize_role(raw, expected):
    assert normalize_role(raw) == expected


def test_raw_metrics_averages_guard_against_zero_division():
    empty = RawMetrics()
    assert empty.parse_avg == 0.0
    assert empty.tmi_avg == 0.0

    filled = RawMetrics(parse_total=180.0, parse_count=2, tmi_total=10.0, tmi_count=4)
    assert filled.parse_avg == 90.0
    assert filled.tmi_avg == 2.5


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------


def test_config_defaults_match_spec():
    cfg = Config()
    assert cfg.difficulty == 5
    assert cfg.weight_upgrade == 0.6
    assert cfg.weight_performance == 0.4
    assert cfg.report_lookback_days == 60
    assert cfg.cache_ttl_hours == 24
    assert cfg.min_fights == 3
    assert cfg.guild_region == "us"


def test_default_role_weight_rows_sum_to_one():
    for role, weights in DEFAULT_ROLE_WEIGHTS.items():
        assert round(sum(weights.values()), 6) == 1.0, f"{role} weights must sum to 1.0"


def test_dps_leans_on_parse_and_tank_does_not():
    assert DEFAULT_ROLE_WEIGHTS[DPS]["parse"] == 0.60
    assert DEFAULT_ROLE_WEIGHTS[TANK]["parse"] == 0.15
    assert DEFAULT_ROLE_WEIGHTS[HEALER]["parse"] == 0.20


def test_load_returns_defaults_when_file_absent(tmp_path):
    cfg = Config.load(str(tmp_path / "nope.json"))
    assert cfg.difficulty == 5
    assert cfg.guild_name == ""


def test_load_overrides_from_json(tmp_path):
    path = tmp_path / "loot_config.json"
    path.write_text(
        json.dumps({"guild_name": "Wipefest", "season_zone_id": 42, "weight_upgrade": 0.8}),
        encoding="utf-8",
    )
    cfg = Config.load(str(path))
    assert cfg.guild_name == "Wipefest"
    assert cfg.season_zone_id == 42
    assert cfg.weight_upgrade == 0.8
    assert cfg.weight_performance == 0.4  # untouched key keeps its default


def test_load_merges_role_weights_partially(tmp_path):
    path = tmp_path / "loot_config.json"
    path.write_text(json.dumps({"role_weights": {"dps": {"parse": 0.5}}}), encoding="utf-8")
    cfg = Config.load(str(path))
    assert cfg.role_weights[DPS]["parse"] == 0.5
    assert cfg.role_weights[DPS]["deaths"] == 0.20  # sibling key preserved
    assert cfg.role_weights[TANK]["parse"] == 0.15  # other roles untouched


def test_load_ignores_unknown_keys(tmp_path):
    path = tmp_path / "loot_config.json"
    path.write_text(json.dumps({"not_a_setting": 1, "difficulty": 4}), encoding="utf-8")
    cfg = Config.load(str(path))
    assert cfg.difficulty == 4
    assert not hasattr(cfg, "not_a_setting")


def test_load_does_not_mutate_module_level_defaults(tmp_path):
    path = tmp_path / "loot_config.json"
    path.write_text(json.dumps({"role_weights": {"dps": {"parse": 0.99}}}), encoding="utf-8")
    Config.load(str(path))
    assert DEFAULT_ROLE_WEIGHTS[DPS]["parse"] == 0.60


def test_validate_lists_every_missing_required_setting():
    with pytest.raises(ConfigError) as exc:
        Config().validate()
    message = str(exc.value)
    assert "guild_name" in message
    assert "guild_server_slug" in message
    assert "season_zone_id" in message


def test_validate_passes_when_required_present():
    cfg = Config(guild_name="Wipefest", guild_server_slug="malganis", season_zone_id=42)
    cfg.validate()  # must not raise


def test_secret_properties_read_exact_env_var_names(monkeypatch):
    monkeypatch.setenv("wow_audit_api_key", "wa-key")
    monkeypatch.setenv("WARCRAFT_LOGS_API_CLIENT_ID", "cid")
    monkeypatch.setenv("WARCRAFT_LOGS_API_CLIENT_SECRET", "secret")
    cfg = Config()
    assert cfg.wowaudit_key == "wa-key"
    assert cfg.wcl_client_id == "cid"
    assert cfg.wcl_client_secret == "secret"


def test_secret_properties_default_to_empty(monkeypatch):
    monkeypatch.delenv("wow_audit_api_key", raising=False)
    assert Config().wowaudit_key == ""

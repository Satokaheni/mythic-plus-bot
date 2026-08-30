"""Tests for the WoWAudit and Warcraft Logs response parsers — captured shapes, no network."""

import pytest

from lootcouncil.models import DPS, HEALER, TANK
from lootcouncil.warcraftlogs import (
    _parse_fights,
    _parse_recent_reports,
    _parse_table_entries,
    _parse_zone_rankings,
)
from lootcouncil.wowaudit import _parse_roster, _parse_wishlists

# ---------------------------------------------------------------------------
# WoWAudit — roster
# ---------------------------------------------------------------------------

ROSTER_PAYLOAD = {
    "characters": [
        {"id": 1, "name": "Thrall", "realm": "Mal'Ganis", "class": "Shaman", "role": "Healer", "rank": 0, "blizzard_id": 111},
        {"id": 2, "name": "Grom", "realm": "Aerie Peak", "class": "Warrior", "role": "Tank", "rank": 1, "blizzard_id": 222},
        {"id": 3, "name": "Sylvanas", "realm": "Illidan", "class": "Hunter", "role": "Ranged", "rank": 2, "blizzard_id": 333},
    ]
}


def test_parse_roster_maps_fields_and_normalizes_roles():
    chars = _parse_roster(ROSTER_PAYLOAD)
    assert len(chars) == 3
    by_name = {c.name: c for c in chars}
    assert by_name["Thrall"].role == HEALER
    assert by_name["Grom"].role == TANK
    assert by_name["Sylvanas"].role == DPS  # "Ranged" collapses to dps
    assert by_name["Thrall"].key == "thrall-malganis"
    assert by_name["Grom"].key == "grom-aeriepeak"
    assert by_name["Thrall"].class_name == "Shaman"
    assert by_name["Grom"].blizzard_id == 222


def test_parse_roster_accepts_a_bare_list():
    chars = _parse_roster(ROSTER_PAYLOAD["characters"])
    assert len(chars) == 3


def test_parse_roster_skips_rows_missing_name_or_realm():
    chars = _parse_roster({"characters": [{"name": "NoRealm"}, {"realm": "NoName"}, {}]})
    assert chars == []


def test_parse_roster_handles_empty_payload():
    assert _parse_roster({}) == []
    assert _parse_roster({"characters": None}) == []


# ---------------------------------------------------------------------------
# WoWAudit — wishlists
# ---------------------------------------------------------------------------

# Path confirmed during the design spike:
# characters[].instances[].difficulties[].wishlist.encounters[].items[]
WISHLIST_PAYLOAD = {
    "characters": [
        {
            "name": "Thrall",
            "realm": "Mal'Ganis",
            "instances": [
                {
                    "name": "Sporefall",
                    "difficulties": [
                        {
                            "difficulty": "Mythic",
                            "wishlist": {
                                "encounters": [
                                    {
                                        "name": "Rotmire",
                                        "items": [
                                            {"id": 215147, "name": "Vibrant Shard", "percentage": 4.2, "absolute": 1800, "spec": "Restoration"},
                                            {"id": 215148, "name": "Dull Shard", "percentage": 0.3, "absolute": 90, "spec": "Restoration"},
                                        ],
                                    }
                                ]
                            },
                        },
                        {
                            "difficulty": "Heroic",
                            "wishlist": {
                                "encounters": [
                                    {"name": "Rotmire", "items": [{"id": 215147, "percentage": 1.1, "absolute": 400, "spec": "Restoration"}]}
                                ]
                            },
                        },
                    ],
                }
            ],
        },
        {
            "name": "Grom",
            "realm": "Aerie Peak",
            "instances": [
                {
                    "difficulties": [
                        {
                            "difficulty": "Mythic",
                            "wishlist": {
                                "encounters": [
                                    {
                                        "items": [
                                            # Alternate shape: per-spec list instead of flat fields.
                                            {"id": 215147, "specs": [
                                                {"spec": "Protection", "percentage": 2.0, "absolute": 700},
                                                {"spec": "Fury", "percentage": 6.5, "absolute": 2400},
                                            ]}
                                        ]
                                    }
                                ]
                            },
                        }
                    ]
                }
            ],
        },
    ]
}


def test_parse_wishlists_indexes_by_item_difficulty_and_character():
    wl = _parse_wishlists(WISHLIST_PAYLOAD)
    assert 215147 in wl
    mythic = wl[215147]["Mythic"]
    assert set(mythic) == {"thrall-malganis", "grom-aeriepeak"}
    assert mythic["thrall-malganis"].percentage == 4.2
    assert mythic["thrall-malganis"].absolute == 1800
    assert mythic["thrall-malganis"].spec == "Restoration"


def test_parse_wishlists_keeps_difficulties_separate():
    wl = _parse_wishlists(WISHLIST_PAYLOAD)
    assert wl[215147]["Heroic"]["thrall-malganis"].percentage == 1.1
    assert "grom-aeriepeak" not in wl[215147]["Heroic"]


def test_parse_wishlists_picks_best_spec_from_a_specs_list():
    wl = _parse_wishlists(WISHLIST_PAYLOAD)
    grom = wl[215147]["Mythic"]["grom-aeriepeak"]
    assert grom.percentage == 6.5  # the Fury line wins on percentage
    assert grom.spec == "Fury"


def test_parse_wishlists_returns_empty_for_the_end_of_season_case():
    # Wishlists were empty during the design spike; this must not raise.
    assert _parse_wishlists({"characters": []}) == {}
    assert _parse_wishlists({}) == {}
    assert _parse_wishlists({"characters": [{"name": "Thrall", "realm": "Illidan", "instances": []}]}) == {}


def test_parse_wishlists_skips_items_without_an_id():
    payload = {
        "characters": [
            {
                "name": "Thrall",
                "realm": "Illidan",
                "instances": [
                    {"difficulties": [{"difficulty": "Mythic", "wishlist": {"encounters": [{"items": [{"percentage": 5.0}]}]}}]}
                ],
            }
        ]
    }
    assert _parse_wishlists(payload) == {}


# ---------------------------------------------------------------------------
# Regression tests — defensive parsing for malformed data
# ---------------------------------------------------------------------------


def test_parse_roster_survives_none_in_characters_list():
    """_parse_roster should skip None elements in the characters list."""
    payload = {"characters": [None, {"name": "Valid", "realm": "Illidan", "class": "Shaman"}]}
    chars = _parse_roster(payload)
    assert len(chars) == 1
    assert chars[0].name == "Valid"


def test_parse_wishlists_survives_none_in_characters_list():
    """_parse_wishlists should skip None elements in the characters list."""
    payload = {
        "characters": [
            None,
            {
                "name": "Thrall",
                "realm": "Mal'Ganis",
                "instances": [
                    {
                        "difficulties": [
                            {"difficulty": "Mythic", "wishlist": {"encounters": [{"items": [{"id": 215147, "percentage": 5.0}]}]}}
                        ]
                    }
                ],
            },
        ]
    }
    wl = _parse_wishlists(payload)
    assert 215147 in wl
    assert wl[215147]["Mythic"]["thrall-malganis"].percentage == 5.0


def test_parse_wishlists_survives_none_in_nested_lists():
    """_parse_wishlists should skip None in instances, difficulties, encounters, and items."""
    payload = {
        "characters": [
            {
                "name": "Thrall",
                "realm": "Mal'Ganis",
                "instances": [
                    None,
                    {
                        "difficulties": [
                            None,
                            {
                                "difficulty": "Mythic",
                                "wishlist": {
                                    "encounters": [
                                        None,
                                        {
                                            "items": [
                                                None,
                                                {"id": 215147, "percentage": 5.0},
                                            ]
                                        },
                                    ]
                                },
                            },
                        ]
                    },
                ],
            }
        ]
    }
    wl = _parse_wishlists(payload)
    assert 215147 in wl
    assert wl[215147]["Mythic"]["thrall-malganis"].percentage == 5.0


def test_parse_wishlists_skips_characters_missing_name_or_realm():
    """_parse_wishlists should skip character entries missing name or realm."""
    payload = {
        "characters": [
            {"realm": "Mal'Ganis", "instances": []},  # missing name
            {"name": "Thrall", "instances": []},  # missing realm
            {"name": "Valid", "realm": "Valid", "instances": []},  # valid but no instances
        ]
    }
    wl = _parse_wishlists(payload)
    assert wl == {}


def test_best_upgrade_handles_empty_specs_list():
    """_best_upgrade should handle items with an empty specs list."""
    payload = {
        "characters": [
            {
                "name": "Thrall",
                "realm": "Mal'Ganis",
                "instances": [
                    {
                        "difficulties": [
                            {
                                "difficulty": "Mythic",
                                "wishlist": {
                                    "encounters": [
                                        {
                                            "items": [
                                                {"id": 215147, "specs": []},  # empty specs list
                                            ]
                                        }
                                    ]
                                },
                            }
                        ]
                    }
                ],
            }
        ]
    }
    wl = _parse_wishlists(payload)
    assert wl == {}  # Empty specs list means no upgrade info


def test_best_upgrade_handles_none_in_specs_and_non_numeric_values():
    """_best_upgrade should skip None in specs and coerce non-numeric values to 0.0."""
    payload = {
        "characters": [
            {
                "name": "Thrall",
                "realm": "Mal'Ganis",
                "instances": [
                    {
                        "difficulties": [
                            {
                                "difficulty": "Mythic",
                                "wishlist": {
                                    "encounters": [
                                        {
                                            "items": [
                                                {
                                                    "id": 215147,
                                                    "specs": [
                                                        None,
                                                        {"spec": "Protection", "percentage": "N/A", "absolute": 700},
                                                        {"spec": "Fury", "percentage": 6.5, "absolute": "invalid"},
                                                    ],
                                                }
                                            ]
                                        }
                                    ]
                                },
                            }
                        ]
                    }
                ],
            }
        ]
    }
    wl = _parse_wishlists(payload)
    assert 215147 in wl
    # Should pick Fury (percentage 6.5) over Protection (percentage "N/A" -> 0.0)
    fury = wl[215147]["Mythic"]["thrall-malganis"]
    assert fury.percentage == 6.5
    assert fury.spec == "Fury"
    # Invalid absolute value becomes 0.0
    assert fury.absolute == 0.0


# ---------------------------------------------------------------------------
# Warcraft Logs — parsers
# ---------------------------------------------------------------------------

ZONE_RANKINGS_PAYLOAD = {
    "data": {
        "characterData": {
            "character": {
                "zoneRankings": {
                    "bestPerformanceAverage": 88.5,
                    "medianPerformanceAverage": 74.25,
                    "rankings": [
                        {"encounter": {"id": 3159, "name": "Rotmire"}, "rankPercent": 91.0},
                        {"encounter": {"id": 3160, "name": "Second"}, "rankPercent": 61.0},
                        {"encounter": {"id": 3161, "name": "Third"}, "rankPercent": None},
                    ],
                    "metric": "dps",
                }
            }
        }
    }
}


def test_parse_zone_rankings_averages_only_real_percents():
    parsed = _parse_zone_rankings(ZONE_RANKINGS_PAYLOAD)
    assert parsed.metric == "dps"
    assert parsed.per_encounter == {3159: 91.0, 3160: 61.0}  # the None entry is dropped
    assert parsed.average == pytest.approx(76.0)


def test_parse_zone_rankings_handles_missing_character():
    parsed = _parse_zone_rankings({"data": {"characterData": {"character": None}}})
    assert parsed.per_encounter == {}
    assert parsed.average == 0.0
    assert parsed.metric == ""


def test_parse_recent_reports_extracts_codes():
    payload = {
        "data": {
            "characterData": {
                "character": {
                    "recentReports": {
                        "data": [
                            {"code": "abc123", "zone": {"name": "Sporefall"}, "startTime": 1700000000000},
                            {"code": "def456", "zone": None, "startTime": 1700100000000},
                        ]
                    }
                }
            }
        }
    }
    refs = _parse_recent_reports(payload)
    assert [r.code for r in refs] == ["abc123", "def456"]
    assert refs[0].zone_name == "Sporefall"
    assert refs[1].zone_name == ""
    assert refs[0].start_time == 1700000000000


def test_parse_fights_filters_to_the_requested_difficulty():
    payload = {
        "data": {
            "reportData": {
                "report": {
                    "fights": [
                        {"id": 1, "name": "Rotmire", "encounterID": 3159, "difficulty": 5, "kill": True},
                        {"id": 2, "name": "Rotmire", "encounterID": 3159, "difficulty": 4, "kill": False},
                        {"id": 3, "name": "Trash", "encounterID": 0, "difficulty": 5, "kill": False},
                    ]
                }
            }
        }
    }
    fights = _parse_fights(payload, difficulty=5)
    assert [f.id for f in fights] == [1]  # difficulty 4 and encounterID 0 both excluded


def test_parse_table_entries_reads_the_nested_entries_list():
    payload = {
        "data": {
            "reportData": {
                "report": {
                    "table": {
                        "data": {
                            "entries": [
                                {"name": "Thrall", "total": 100},
                                {"name": "Grom", "total": 200},
                            ]
                        }
                    }
                }
            }
        }
    }
    entries = _parse_table_entries(payload)
    assert [e["name"] for e in entries] == ["Thrall", "Grom"]


def test_parse_table_entries_accepts_a_bare_list_and_empty_shapes():
    bare = {"data": {"reportData": {"report": {"table": {"data": [{"name": "Thrall"}]}}}}}
    assert _parse_table_entries(bare) == [{"name": "Thrall"}]
    assert _parse_table_entries({}) == []
    assert _parse_table_entries({"data": {"reportData": {"report": {"table": {"data": {}}}}}}) == []

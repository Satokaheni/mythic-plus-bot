"""CLI: rank who should receive a dropped item.

Usage: python loot.py <itemId> [--difficulty 5] [--refresh] [--weights 0.6,0.4]
"""

import argparse
import logging
import sys
from typing import List, Tuple

from dotenv import load_dotenv

from lootcouncil.config import Config
from lootcouncil.performance import PerformanceAnalyzer
from lootcouncil.ranker import LootRanker, LootResult
from lootcouncil.warcraftlogs import WarcraftLogsClient
from lootcouncil.wowaudit import WowAuditClient

logger = logging.getLogger("lootcouncil")

COMPONENT_ORDER = ("parse", "deaths", "damage", "utility", "survivability")


def parse_weights(text: str) -> Tuple[float, float]:
    parts = text.split(",")
    if len(parts) != 2:
        raise ValueError("--weights must look like 0.6,0.4")
    try:
        upgrade, performance = float(parts[0]), float(parts[1])
    except ValueError:
        raise ValueError("--weights must be two numbers, e.g. 0.6,0.4")
    return upgrade, performance


def _rows_to_lines(rows, columns) -> List[str]:
    widths = [len(c) for c in columns]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    header = "  ".join(c.ljust(widths[i]) for i, c in enumerate(columns))
    sep = "  ".join("-" * widths[i] for i in range(len(columns)))
    body = ["  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)) for row in rows]
    return [header, sep] + body


def format_loot_table(result: LootResult) -> str:
    lines = [
        f"Loot ranking for item {result.item_id} (difficulty {result.difficulty})",
        f"Weights: upgrade {result.weight_upgrade} / performance {result.weight_performance}",
        "",
    ]
    if result.is_empty:
        lines.append("No candidates — nobody has this item on a wishlist for that difficulty.")
        lines.append("(If the season just started, droptimizers may not be uploaded yet.)")
        return "\n".join(lines)

    for role, rows in result.by_role.items():
        lines.append(f"== {role.upper()} ==")
        table = [
            [
                str(i + 1),
                row.name + (" *" if row.low_confidence else ""),
                row.spec,
                f"{row.upgrade_pct:.2f}%",
                f"{row.performance:.3f}",
                f"{row.loot_score:.3f}",
            ]
            for i, row in enumerate(rows)
        ]
        lines.extend(_rows_to_lines(table, ["#", "Character", "Spec", "Upgrade", "Perf", "Score"]))
        lines.append("")

    if any(r.low_confidence for rows in result.by_role.values() for r in rows):
        lines.append("* low confidence — too few logged fights to score reliably")
    return "\n".join(lines)


def format_performance_table(result: LootResult) -> str:
    lines = ["Performance ranking (within role)", ""]
    if result.is_empty:
        lines.append("No candidates to score.")
        return "\n".join(lines)

    for role, rows in result.performance_by_role.items():
        lines.append(f"== {role.upper()} ==")
        table = [
            [str(i + 1), row.name, f"{row.performance:.3f}"]
            + [f"{row.components.get(c, 0.0):.2f}" for c in COMPONENT_ORDER]
            for i, row in enumerate(rows)
        ]
        lines.extend(
            _rows_to_lines(table, ["#", "Character", "Perf"] + [c.title() for c in COMPONENT_ORDER])
        )
        lines.append("")
    return "\n".join(lines)


def main(argv=None) -> int:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Rank who should receive a dropped item.")
    parser.add_argument("item_id", type=int, help="the dropped item's ID")
    parser.add_argument("--difficulty", type=int, default=None, help="WCL difficulty (5=Mythic, 4=Heroic)")
    parser.add_argument("--refresh", action="store_true", help="re-pull Warcraft Logs instead of using the cache")
    parser.add_argument("--weights", type=str, default=None, help="upgrade,performance e.g. 0.6,0.4")
    args = parser.parse_args(argv)

    cfg = Config.load()
    if args.weights:
        try:
            cfg.weight_upgrade, cfg.weight_performance = parse_weights(args.weights)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    try:
        cfg.validate()
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    wcl = WarcraftLogsClient(cfg)
    ranker = LootRanker(cfg, WowAuditClient(cfg), PerformanceAnalyzer(cfg, wcl))
    try:
        result = ranker.rank(args.item_id, args.difficulty, refresh=args.refresh)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(format_loot_table(result))
    print()
    print(format_performance_table(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""WoW Token sell-alert state, ratchet detection, and display formatting.

Unlike watchlist.py (which hunts commodity *lows* with an adaptive percentile band),
this is a fixed-threshold *sell* signal: the owner names a gold price, and the bot
reports when the market reaches it. While the price stays above the threshold,
further alerts fire only on new highs at least STEP_COPPER above the last one.
"""

import json
import logging
import os
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("discord")

TOKEN_FILE = "token_watch.json"
COPPER_PER_GOLD = 10_000
# Minimum rise above the last alerted price before re-alerting while above threshold.
STEP_COPPER = 10_000 * COPPER_PER_GOLD  # 10,000g


@dataclass
class TokenWatch:
    """The owner's token sell threshold and ratchet position. All prices in copper."""

    threshold: Optional[int] = None   # None -> alerts disabled
    last_alert: Optional[int] = None  # None -> armed (no alert outstanding)

    def to_dict(self) -> dict:
        return {"threshold": self.threshold, "last_alert": self.last_alert}

    @classmethod
    def from_dict(cls, d: dict) -> "TokenWatch":
        threshold = d.get("threshold")
        last_alert = d.get("last_alert")
        return cls(
            threshold=int(threshold) if threshold is not None else None,
            last_alert=int(last_alert) if last_alert is not None else None,
        )

    def save(self, path: str = TOKEN_FILE) -> None:
        data = {"version": 1, "token_watch": self.to_dict()}
        tmp_path = f"{path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, path)

    @classmethod
    def load(cls, path: str = TOKEN_FILE) -> "TokenWatch":
        """Load state, or return a disabled default. Never raises — a missing or
        corrupt file must not stop the bot from starting."""
        if not os.path.exists(path):
            return cls()
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return cls.from_dict(data.get("token_watch", {}))
        except (json.JSONDecodeError, ValueError, TypeError, AttributeError, OSError, OverflowError) as exc:
            logger.warning("Error loading %s: %s. Token alerts start disabled.", path, exc)
            return cls()


def evaluate(watch: TokenWatch, price: int) -> Optional[int]:
    """Return the price to report, or None for silence. Does NOT mutate `watch`.

    Fires on the first crossing above the threshold, then only on new highs at
    least STEP_COPPER above the last alerted price. Because `last_alert` only ever
    moves upward while the price stays above the threshold, a dip-and-reclimb is
    silent until it genuinely beats the last reported high.

    The caller is responsible for committing `last_alert` — and only after the DM
    has actually been delivered.
    """
    if watch.threshold is None:
        return None
    if price < watch.threshold:
        return None
    if watch.last_alert is None:
        return price
    if price >= watch.last_alert + STEP_COPPER:
        return price
    return None


def should_rearm(watch: TokenWatch, price: int) -> bool:
    """True when the price has fallen back below the threshold and the ratchet should reset.

    Guarded on `last_alert` being set so a price resting quietly below the threshold
    does not rewrite the state file on every poll.
    """
    return watch.threshold is not None and watch.last_alert is not None and price < watch.threshold

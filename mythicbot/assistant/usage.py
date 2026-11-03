"""Per-day assistant call counter, persisted to assistant.json (atomic write)."""

import json
import os

PATH = "assistant.json"


class DailyUsage:
    def __init__(self, day: str = "", count: int = 0, cost_usd: float = 0.0) -> None:
        self.day, self.count, self.cost_usd = day, count, cost_usd

    @classmethod
    def load(cls, path: str = PATH) -> "DailyUsage":
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return cls(str(data["day"]), int(data["count"]), float(data["cost_usd"]))
        except (OSError, ValueError, KeyError, TypeError):
            return cls()

    def save(self, path: str = PATH) -> None:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"day": self.day, "count": self.count, "cost_usd": round(self.cost_usd, 6)}, f)
        os.replace(tmp, path)

    def _roll(self, today: str) -> None:
        if self.day != today:
            self.day, self.count, self.cost_usd = today, 0, 0.0

    def allow(self, today: str, cap: int) -> bool:
        self._roll(today)
        return self.count < cap

    def record(self, today: str, cost: float) -> None:
        self._roll(today)
        self.count += 1
        self.cost_usd += cost

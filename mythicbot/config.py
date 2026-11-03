"""Runtime configuration: environment variables, IDs, and tunables. Imported once at startup."""

import logging
import os
from logging.handlers import RotatingFileHandler
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from mythicbot.assistant.claude_runner import parse_command


def _configure_logging() -> None:
    """Log to a rotating file (for headless deploys) and the console.

    The file is capped so it can't fill a Raspberry Pi's SD card. Tunable via env:
    LOG_FILE (default bot.log), LOG_LEVEL (default INFO).
    """
    level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
    log_file = os.getenv("LOG_FILE", "bot.log")
    fmt = logging.Formatter("%(asctime)s %(levelname)-8s %(name)s %(message)s", "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(level)
    # ~5 MB per file, 3 rotated backups -> at most ~20 MB on disk.
    file_handler = RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    console_handler = logging.StreamHandler()
    for handler in (file_handler, console_handler):
        handler.setFormatter(fmt)
        root.addHandler(handler)


load_dotenv(".env")
_configure_logging()


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


CLIENT_ID = _require_env("CLIENT_KEY")
AVAIL_CHANNEL_ID = int(_require_env("AVAIL_CHANNEL_ID"))
KEY_CHANNEL_ID = int(_require_env("KEY_CHANNEL_ID"))
GUILD_ID = int(_require_env("GUILD_ID"))
TANK_ID = int(_require_env("TANK_ROLE_ID"))
HEALER_ID = int(_require_env("HEALER_ROLE_ID"))
DPS_ID = int(_require_env("DPS_ROLE_ID"))
COORDINATOR_ID = int(_require_env("COORDINATOR_ID"))
BANKER_ID = int(_require_env("BANKER_ID"))
# Gold budget used to size price-watch buy suggestions (default 100,000 gold).
BANKER_BUDGET_COPPER = int(os.getenv("BANKER_BUDGET_GOLD", "100000")) * 10000
# Default bulk order size the price-watch buy signal targets (per-item overridable via `!watch -x`).
BANKER_BULK_QTY = int(os.getenv("BANKER_BULK_QTY", "100"))
GEAR_AUDIT_CONCURRENCY = 5  # Parallel Blizzard profile fetches; the roster is ~30 characters.
# Blizzard Game Data API — server-specific auction sniper (see snipes.json).
BLIZZ_CLIENT_ID = _require_env("BLIZZ_CLIENT_ID")
BLIZZ_CLIENT_SECRET = _require_env("BLIZZ_CLIENT_SECRET")
os.environ.setdefault("BLIZZ_REGION", "us")
SNIPE_SWEEP_MINUTES = 30
# Blizzard refreshes the WoW Token price roughly every 20 minutes.
TOKEN_POLL_MINUTES = 20
MYTHIC_PLUS_ID = int(_require_env("MYTHIC_PLUS_ID"))
ADMINS = [int(id_str) for id_str in _require_env("ADMIN_ID").split(",") if id_str.strip().isdigit()]
ELEVATED_IDS = {COORDINATOR_ID} | set(ADMINS)
_CST = ZoneInfo("America/Chicago")
BOT_VERSION = "1.11.0"
ASSISTANT_ENABLED = os.getenv("ASSISTANT_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")
ASSISTANT_USER_IDS = frozenset(
    int(x) for x in os.getenv("ASSISTANT_USER_IDS", "").split(",") if x.strip().isdigit()
)
CLAUDE_CMD = parse_command(os.getenv("CLAUDE_BIN", "claude"))
ASSISTANT_MODEL = os.getenv("ASSISTANT_MODEL", "haiku")
ASSISTANT_TIMEOUT = float(os.getenv("ASSISTANT_TIMEOUT", "60"))
ASSISTANT_DAILY_CAP = int(os.getenv("ASSISTANT_DAILY_CAP", "100"))
ASSISTANT_WORK_DIR = os.getenv("ASSISTANT_WORK_DIR", os.path.expanduser("~/.mpb-assistant/work"))
ASSISTANT_CONFIG_DIR = os.getenv("ASSISTANT_CONFIG_DIR", os.path.expanduser("~/.mpb-assistant/config"))
CLAUDE_CODE_OAUTH_TOKEN = os.getenv("CLAUDE_CODE_OAUTH_TOKEN", "")

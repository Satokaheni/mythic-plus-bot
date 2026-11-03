"""Validate Claude's structured output (untrusted) and turn it into a bot command string."""

import unicodedata
from dataclasses import dataclass
from pathlib import Path

READ_COMMANDS = frozenset({"keys", "snipes", "watches", "token", "help"})
WRITE_COMMANDS = frozenset({"snipe", "snipepet", "unsnipe", "watch", "unwatch", "tokenalert"})
FLOW_COMMANDS = frozenset({"key"})
ALL_COMMANDS = READ_COMMANDS | WRITE_COMMANDS | FLOW_COMMANDS
TEMPLATE_PATH = Path(__file__).with_name("system_prompt.md")

USAGE = {
    "keys": "!keys — DM me my scheduled runs",
    "snipes": "!snipes — list my auction snipes",
    "watches": "!watches — list watched commodities (banker only)",
    "token": "!token — current WoW Token price and alert state (banker only)",
    "help": "!help — list every bot command",
    "snipe": "!snipe <itemId> <maxGold> [label] — alert when an item is under maxGold on any realm",
    "snipepet": "!snipepet <speciesId> <maxGold> [label] — same, for a battle pet species",
    "unsnipe": "!unsnipe <id> [id ...] — stop sniping items or pets",
    "watch": "!watch <itemId> [-x<qty>] [label] — watch a commodity price (banker only)",
    "unwatch": "!unwatch <itemId> [itemId ...] — stop watching (banker only)",
    "tokenalert": "!tokenalert <gold>|off — DM when the token price rises above gold (banker only)",
    "key": "!key — start a Mythic+ key request (opens a DM form)",
}

MAX_ARGS = 20
MAX_ARG_LEN = 100
_FORBIDDEN = ("\n", "\r", "<@", "@everyone", "@here", "`")
_FORBIDDEN_CATEGORIES = ("Cc", "Cf", "Zl", "Zp")  # control, format (zero-width, bidi), line/paragraph separators

SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["answer", "command"]},
        "text": {"type": "string"},
        "command": {"type": "string", "enum": sorted(ALL_COMMANDS)},
        "args": {"type": "array", "items": {"type": "string"}, "maxItems": MAX_ARGS},
        "summary": {"type": "string"},
    },
    "required": ["kind"],
    "additionalProperties": False,
}


class IntentError(ValueError):
    pass


@dataclass(frozen=True)
class Intent:
    kind: str
    text: str = ""
    command: str = ""
    args: tuple = ()
    summary: str = ""

    @property
    def needs_confirm(self) -> bool:
        return self.kind == "command" and self.command in WRITE_COMMANDS


def validate(obj) -> Intent:
    if not isinstance(obj, dict):
        raise IntentError("intent is not an object")
    kind = obj.get("kind")
    if kind == "answer":
        text = obj.get("text")
        if not isinstance(text, str) or not text.strip():
            raise IntentError("answer without text")
        return Intent(kind="answer", text=text.strip())
    if kind == "command":
        command = obj.get("command")
        if command not in ALL_COMMANDS:
            raise IntentError(f"command not allowed: {command!r}")
        args = obj.get("args", [])
        if not isinstance(args, list) or len(args) > MAX_ARGS:
            raise IntentError("args must be a list of at most 20 strings")
        clean = []
        for arg in args:
            if not isinstance(arg, str):
                raise IntentError("non-string arg")
            arg = arg.strip()
            if not arg:
                continue
            if (len(arg) > MAX_ARG_LEN or arg.startswith("!") or any(bad in arg for bad in _FORBIDDEN)
                    or any(unicodedata.category(ch) in _FORBIDDEN_CATEGORIES for ch in arg)):
                raise IntentError(f"unsafe arg: {arg[:40]!r}")
            clean.append(arg)
        summary = obj.get("summary") or ""
        if not isinstance(summary, str):
            raise IntentError("summary must be a string")
        return Intent(kind="command", command=command, args=tuple(clean), summary=summary.strip()[:200])
    raise IntentError(f"unknown kind: {kind!r}")


def build_command(intent: Intent) -> str:
    if intent.kind != "command":
        raise IntentError("not a command intent")
    return " ".join(["!" + intent.command, *intent.args])


def render_system_prompt(template_path: Path) -> str:
    catalog = "\n".join(f"- {USAGE[name]}" for name in sorted(USAGE))
    return template_path.read_text(encoding="utf-8").replace("{{COMMANDS}}", catalog)

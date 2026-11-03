# Headless Claude Assistant — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the owner talk to the bot in plain language (1:1 DM, or an @mention elsewhere); the bot runs an isolated `claude -p` call that either answers or names one bot command, which the bot runs through the real command pipeline under the owner's identity (writes after a Confirm click).

**Architecture:** A new `Assistant` cog listens to `on_message` alongside `commands.Bot`'s command processing. A pure trigger predicate decides whether to handle a message. `assistant/claude_runner.py` runs one locked-down `claude -p` subprocess (no tools, private config dir, empty cwd, minimal env, prompt on stdin) and returns the structured output. `assistant/intent.py` re-validates that untrusted output against an allowlist and builds a `!command` string, which the cog executes with `bot.get_context` + `bot.invoke` on a copy of the user's message.

**Tech Stack:** Python 3.10, discord.py 2.3.1, Claude Code CLI (`claude -p`, native install), pytest, ruff, the offline harness at `.claude/skills/verify-mythic-plus-bot/`.

**Spec:** `docs/superpowers/specs/2026-11-03-headless-claude-assistant-design.md` — read it before any task.

## Global Constraints

- Branch `refactor/modularize-bot` in `C:\Users\zach\Work\mpb-refactor`, starting after the roster-fix commits. Never touch `C:\Users\zach\Work\mythic-plus-bot`. No `git stash`, no push, never edit `version.txt`.
- **Never run `python bot.py`; never load a `.env`** — except Task 3's smoke script, which reads exactly one key (`CLAUDE_CODE_OAUTH_TOKEN`) from an env file the owner names, and only the owner runs it.
- Set `PYTHONIOENCODING=utf-8` for harness commands.
- `ASSISTANT_ENABLED` defaults off. With it off the cog's listener returns before doing anything.
- The `claude` subprocess env contains only: `PATH`, `SYSTEMROOT`, `HOME`, `USERPROFILE`, `TEMP`, `TMP` (each only if present), `CLAUDE_CONFIG_DIR`, `CLAUDE_CODE_OAUTH_TOKEN`, `DISABLE_AUTOUPDATER=1`. Never `CLIENT_KEY`, `BLIZZ_*`, `UNDERMINE_*`, `RAIDERIO_*`, or anything else from `.env`.
- The prompt goes to the subprocess on stdin, never argv. `create_subprocess_exec`, never a shell.
- Command allowlist (exact): read `{keys, snipes, watches, token, help}`; write `{snipe, snipepet, unsnipe, watch, unwatch, tokenalert}`; flow `{key}`.
- Arg rules (exact): each arg a string, stripped, empty dropped, ≤100 chars, no leading `!`, none of `\n`, `\r`, `<@`, `@everyone`, `@here`, `` ` ``; at most 20 args.
- Replies built from Claude output use `allowed_mentions=discord.AllowedMentions.none()`.
- Failure reply text (exact): `I couldn't work that out — try \`!help\`.` Daily cap reply: `Daily assistant limit reached.` Busy reply: `One moment — still working on the last one.` Empty-mention reply: `What can I help with?` Access reply: ``You don't have access to `!<name>` here.``
- Persisted state: new `assistant.json` only (gitignored, atomic write). `state.json` and the other state files are untouched.
- **The gate** (end of every task): `python -m pytest -q` all pass; `ruff check . --exclude tests` clean; `ruff check tests --statistics` ≤ 12 errors; `PYTHONIOENCODING=utf-8 python .claude/skills/verify-mythic-plus-bot/scripts/harness.py run all` all PASS (from Task 7 on, including `assistant`); `... harness.py doctor` no `[XX]`; `PYTHONIOENCODING=utf-8 python .claude/skills/verify-schedule-roster/check_roster.py` exit 0.

## Review Focus

1. **Claude names a forbidden command or smuggles text** (`cleanup`, `@everyone` in an arg, a newline, a leading `!`): nothing runs, the user gets the failure reply. Pinned by `test_intent.py` reject cases and the `assistant` journey's `evil` / `inject` steps (Tasks 4, 7).
2. **Bot secrets leaking into the subprocess.** Pinned by `test_minimal_env_drops_secrets` (Task 2) and the journey's check of every recorded fake-claude env (Task 7).
3. **Messages that must stay silent** (guild text without a mention, `@everyone`, non-allowlisted users, `!commands`, bots): no reply and **no Claude call**. Pinned by `test_trigger.py` (Task 5) and the journey's fake-claude call count (Task 7).
4. **A hung or crashing `claude`**: killed at the timeout, one failure reply, bot keeps working; a second message during a call gets the busy reply. Pinned by `test_run_claude_timeout_kills` (Task 2) and the journey's `fake:timeout` / `fake:crash` steps (Task 7).
5. **Permissions follow the Discord author, not Claude.** An allowlisted non-banker asking for `!watch` gets the access reply and `watches.json` is unchanged. Pinned by the journey (Task 7).

---

## File Structure

| File | Responsibility |
|---|---|
| `assistant/__init__.py` | package marker |
| `assistant/claude_runner.py` | `parse_command`, `build_argv`, `minimal_env`, `parse_result`, `run_claude`, `RunResult` |
| `assistant/intent.py` | command sets, `USAGE`, `SCHEMA`, `Intent`, `IntentError`, `validate`, `build_command`, `render_system_prompt` |
| `assistant/system_prompt.md` | prompt template with `{{COMMANDS}}` |
| `assistant/usage.py` | `DailyUsage` (assistant.json) |
| `assistant/smoke.py` | owner-run live check of the real CLI |
| `cogs/assistant.py` | `should_handle`, `ConfirmView`, `Assistant` cog |
| `config.py` | `ASSISTANT_*`, `CLAUDE_CMD`, `CLAUDE_CODE_OAUTH_TOKEN` |
| `cogs/__init__.py` | `cogs.assistant` appended last |
| harness `scripts/harness.py`, `scripts/fake_claude.py`, `scripts/journeys.py` | mention fields, `typing()`, fake CLI, `assistant` journey |
| `tests/test_claude_runner.py`, `test_intent.py`, `test_trigger.py`, `test_usage.py` | unit tests |
| Dockerfile, pyproject.toml, .gitignore, CLAUDE.md, README.md, CHANGELOG.md, `cogs/admin.py` (`!help`) | ship + document |

---

### Task 1: Harness support — mention fields, `typing()`, fake `claude`

**Files:** Modify `.claude/skills/verify-mythic-plus-bot/scripts/harness.py`; Create `.claude/skills/verify-mythic-plus-bot/scripts/fake_claude.py`.

**Interfaces:** Produces `FakeMessage.raw_mentions: List[int]`, `.mentions: List[FakeUser]`, `.mention_everyone: bool`, `.role_mentions: list`, `.type: discord.MessageType`, `.webhook_id: None`; `_Messageable.typing()` async context manager (records a `typing` event); `Harness.mention() -> str`; `FAKE_ENV` assistant keys; `fake_claude.py` writing `fake_claude_calls.jsonl` in its cwd.

- [ ] **Step 1: FakeMessage fields.** In `FakeMessage.__init__`, after `self.flags = ...`, add:

```python
        ids = [int(m) for m in re.findall(r"<@!?(\d+)>", self.content)]
        self.raw_mentions = list(dict.fromkeys(ids))
        self.mentions = [world.users[i] for i in self.raw_mentions if i in world.users]
        self.mention_everyone = "@everyone" in self.content or "@here" in self.content
        self.role_mentions = []
        self.type = discord.MessageType.default
        self.webhook_id = None
```

and `import re` at the top.

- [ ] **Step 2: `typing()`.** Add to `_Messageable`:

```python
    def typing(self):
        world, channel = self._world, self

        class _Typing:
            async def __aenter__(self_inner):
                world.record("typing", channel=channel)

            async def __aexit__(self_inner, *exc):
                return False

        return _Typing()
```

- [ ] **Step 3: `Harness.mention()`** — add to `Harness`:

```python
    def mention(self) -> str:
        return f"<@{IDS['bot']}>"
```

- [ ] **Step 4: FAKE_ENV.** Add to `FAKE_ENV` (keep existing keys):

```python
    "ASSISTANT_ENABLED": "1",
    "ASSISTANT_USER_IDS": f"{IDS['banker']},{IDS['coordinator']}",
    "CLAUDE_BIN": json.dumps([sys.executable, str(Path(__file__).resolve().parent / "fake_claude.py")]),
    "ASSISTANT_CONFIG_DIR": "assistant-config",
    "ASSISTANT_WORK_DIR": "assistant-work",
    "ASSISTANT_TIMEOUT": "3",
    "CLAUDE_CODE_OAUTH_TOKEN": "offline-fake-token",
```

(`FAKE_ENV` is defined before `SCRIPTS`; use `Path(__file__)` directly as shown.)

- [ ] **Step 5: Create `fake_claude.py`:**

```python
#!/usr/bin/env python
"""Stand-in for `claude -p` in the offline harness. Reads the prompt on stdin, records the call, prints a canned result."""

import json
import os
import sys
import time


def _route(low: str, prompt: str) -> dict:
    if "unsnipe" in low:
        return {"kind": "command", "command": "unsnipe", "args": ["215147"], "summary": "stop sniping 215147"}
    if "my snipes" in low:
        return {"kind": "command", "command": "snipes", "args": [], "summary": "list snipes"}
    if "snipe" in low:
        return {"kind": "command", "command": "snipe", "args": ["215147", "5000", "Vibrant Shard"],
                "summary": "snipe 215147 under 5000g"}
    if "watch" in low:
        return {"kind": "command", "command": "watch", "args": ["241326"], "summary": "watch 241326"}
    if "make a run" in low:
        return {"kind": "command", "command": "key", "args": [], "summary": "start a key request"}
    if "evil" in low:
        return {"kind": "command", "command": "cleanup", "args": [], "summary": "not allowed"}
    if "inject" in low:
        return {"kind": "command", "command": "snipe", "args": ["1", "5", "@everyone"], "summary": "unsafe"}
    return {"kind": "answer", "text": "fake answer: " + prompt.strip()[:80]}


def main() -> int:
    prompt = sys.stdin.read()
    with open("fake_claude_calls.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"argv": sys.argv[1:], "env_keys": sorted(os.environ), "prompt": prompt}) + "\n")
    low = prompt.lower()
    if "fake:timeout" in low:
        time.sleep(600)
        return 0
    if "fake:badjson" in low:
        print("this is not json")
        return 0
    if "fake:crash" in low:
        print("boom", file=sys.stderr)
        return 2
    if "fake:error" in low:
        print(json.dumps({"type": "result", "subtype": "error_during_execution", "is_error": True,
                          "total_cost_usd": 0.001}))
        return 0
    print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.0012,
                      "duration_ms": 5, "session_id": "fake", "structured_output": _route(low, prompt)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 6: Verify the fields** with a scratch scenario (write it in your scratchpad, not the repo):

```python
async def scenario(h):
    alice = h.user("Alice")
    m = await h.say(alice, f"{h.mention()} hi @everyone <@!{alice.id}>", "key")
    h.check("raw_mentions parsed", m.raw_mentions == [100000000000000099, alice.id], m.raw_mentions)
    h.check("mentions resolve users", [u.id for u in m.mentions] == m.raw_mentions)
    h.check("mention_everyone", m.mention_everyone is True)
```

Run: `PYTHONIOENCODING=utf-8 python .claude/skills/verify-mythic-plus-bot/scripts/harness.py script <scratch>.py` → PASS 3/3. And `echo "hello fake:error" | python .claude/skills/verify-mythic-plus-bot/scripts/fake_claude.py` prints an error result (run in a scratch cwd; delete the jsonl it writes).

- [ ] **Step 7: Gate**, then commit: `git add .claude/skills/verify-mythic-plus-bot/scripts/{harness.py,fake_claude.py}` → `test(verify): harness mention fields, typing, and a fake claude CLI`.

---

### Task 2: Config + `claude_runner`

**Files:** Create `assistant/__init__.py` (empty), `assistant/claude_runner.py`, `tests/test_claude_runner.py`; Modify `config.py`, `pyproject.toml` (`packages += ["assistant"]`, `known-first-party += ["assistant"]`), harness `doctor`.

**Interfaces:** Produces `config.ASSISTANT_ENABLED: bool`, `ASSISTANT_USER_IDS: frozenset[int]`, `CLAUDE_CMD: List[str]`, `ASSISTANT_MODEL: str`, `ASSISTANT_TIMEOUT: float`, `ASSISTANT_DAILY_CAP: int`, `ASSISTANT_WORK_DIR: str`, `ASSISTANT_CONFIG_DIR: str`, `CLAUDE_CODE_OAUTH_TOKEN: str`; `claude_runner.RunResult(ok, output, error, cost_usd, duration_s, subtype)`, `parse_command(str) -> List[str]`, `build_argv(cmd, *, schema, model, system_prompt_file, max_turns=3, max_budget_usd=0.05) -> List[str]`, `minimal_env(*, config_dir, token, base=None) -> dict`, `parse_result(bytes) -> RunResult`, `async run_claude(prompt, *, cmd, schema, model, system_prompt_file, work_dir, config_dir, token, timeout) -> RunResult`.

- [ ] **Step 1: Failing tests** `tests/test_claude_runner.py`:

```python
import asyncio
import json
import sys
from pathlib import Path

from assistant import claude_runner as cr

FAKE = [sys.executable, str(Path(__file__).resolve().parents[1] / ".claude/skills/verify-mythic-plus-bot/scripts/fake_claude.py")]
SCHEMA = {"type": "object"}


def test_parse_command_plain_and_json():
    assert cr.parse_command("claude") == ["claude"]
    assert cr.parse_command('["py", "x.py"]') == ["py", "x.py"]


def test_build_argv_locks_down_tools():
    argv = cr.build_argv(["claude"], schema=SCHEMA, model="haiku", system_prompt_file="p.md")
    assert argv[:2] == ["claude", "-p"]
    i = argv.index("--tools")
    assert argv[i + 1] == ""
    for flag in ("--strict-mcp-config", "--no-session-persistence", "--json-schema", "--system-prompt-file"):
        assert flag in argv
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"


def test_minimal_env_drops_secrets():
    base = {"PATH": "/bin", "HOME": "/h", "CLIENT_KEY": "secret", "BLIZZ_CLIENT_SECRET": "s", "RANDOM": "x"}
    env = cr.minimal_env(config_dir="/cfg", token="tok", base=base)
    assert env == {"PATH": "/bin", "HOME": "/h", "CLAUDE_CONFIG_DIR": "/cfg",
                   "CLAUDE_CODE_OAUTH_TOKEN": "tok", "DISABLE_AUTOUPDATER": "1"}


def test_parse_result_shapes():
    ok = cr.parse_result(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                                     "total_cost_usd": 0.01, "structured_output": {"kind": "answer"}}).encode())
    assert ok.ok and ok.output == {"kind": "answer"} and ok.cost_usd == 0.01
    assert not cr.parse_result(b"nope").ok
    assert not cr.parse_result(json.dumps({"type": "result", "subtype": "error_max_turns"}).encode()).ok
    assert not cr.parse_result(json.dumps({"type": "result", "subtype": "success"}).encode()).ok  # no structured_output


def _run(prompt, tmp_path, timeout=10):
    return asyncio.run(cr.run_claude(prompt, cmd=FAKE, schema=SCHEMA, model="haiku",
                                     system_prompt_file=tmp_path / "p.md", work_dir=tmp_path / "w",
                                     config_dir=tmp_path / "c", token="tok", timeout=timeout))


def test_run_claude_success_uses_stdin_and_minimal_env(tmp_path):
    r = _run("hello there", tmp_path)
    assert r.ok and r.output["kind"] == "answer"
    call = json.loads((tmp_path / "w" / "fake_claude_calls.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert call["prompt"] == "hello there" and "hello there" not in " ".join(call["argv"])
    assert "CLIENT_KEY" not in call["env_keys"] and "CLAUDE_CODE_OAUTH_TOKEN" in call["env_keys"]


def test_run_claude_crash_and_badjson(tmp_path):
    assert not _run("fake:crash", tmp_path).ok
    assert not _run("fake:badjson", tmp_path).ok


def test_run_claude_timeout_kills(tmp_path):
    r = _run("fake:timeout", tmp_path, timeout=1)
    assert not r.ok and r.error == "timeout"
```

Run `python -m pytest tests/test_claude_runner.py -v` → FAIL (`No module named 'assistant'`).

- [ ] **Step 2: `assistant/claude_runner.py`:**

```python
"""Run one isolated headless `claude -p` call and parse its JSON result."""

import asyncio
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

_PASSTHROUGH = ("PATH", "SYSTEMROOT", "HOME", "USERPROFILE", "TEMP", "TMP")


@dataclass
class RunResult:
    ok: bool
    output: Optional[dict] = None
    error: str = ""
    cost_usd: float = 0.0
    duration_s: float = 0.0
    subtype: str = ""


def parse_command(value: str) -> List[str]:
    """CLAUDE_BIN is a path, or a JSON list of argv words (so tests can run a Python script)."""
    value = value.strip()
    if value.startswith("["):
        parsed = json.loads(value)
        if not (isinstance(parsed, list) and parsed and all(isinstance(p, str) for p in parsed)):
            raise ValueError("CLAUDE_BIN JSON must be a non-empty list of strings")
        return parsed
    return [value]


def build_argv(cmd, *, schema, model, system_prompt_file, max_turns=3, max_budget_usd=0.05) -> List[str]:
    return [
        *cmd, "-p",
        "--output-format", "json",
        "--json-schema", json.dumps(schema),
        "--tools", "",
        "--strict-mcp-config",
        "--max-turns", str(max_turns),
        "--max-budget-usd", str(max_budget_usd),
        "--model", model,
        "--no-session-persistence",
        "--permission-mode", "dontAsk",
        "--system-prompt-file", str(system_prompt_file),
    ]


def minimal_env(*, config_dir, token, base=None) -> dict:
    """Only what the CLI needs. load_dotenv put every bot secret in os.environ; none may pass through."""
    base = os.environ if base is None else base
    env = {k: base[k] for k in _PASSTHROUGH if k in base}
    env.update({"CLAUDE_CONFIG_DIR": str(config_dir), "CLAUDE_CODE_OAUTH_TOKEN": token, "DISABLE_AUTOUPDATER": "1"})
    return env


def parse_result(stdout: bytes) -> RunResult:
    try:
        data = json.loads(stdout.decode("utf-8", "replace"))
    except ValueError:
        return RunResult(ok=False, error="unparseable output")
    if isinstance(data, list):
        data = next((m for m in reversed(data) if isinstance(m, dict) and m.get("type") == "result"), {})
    if not isinstance(data, dict) or data.get("type") != "result":
        return RunResult(ok=False, error="no result message")
    cost = float(data.get("total_cost_usd") or 0.0)
    subtype = str(data.get("subtype", ""))
    if subtype != "success" or data.get("is_error"):
        return RunResult(ok=False, error=f"claude {subtype or 'error'}", cost_usd=cost, subtype=subtype)
    output = data.get("structured_output")
    if not isinstance(output, dict):
        return RunResult(ok=False, error="no structured output", cost_usd=cost, subtype=subtype)
    return RunResult(ok=True, output=output, cost_usd=cost, subtype=subtype)


async def run_claude(prompt, *, cmd, schema, model, system_prompt_file, work_dir, config_dir, token, timeout) -> RunResult:
    Path(work_dir).mkdir(parents=True, exist_ok=True)
    Path(config_dir).mkdir(parents=True, exist_ok=True)
    argv = build_argv(cmd, schema=schema, model=model, system_prompt_file=system_prompt_file)
    start = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            cwd=str(work_dir), env=minimal_env(config_dir=config_dir, token=token),
        )
    except OSError as exc:
        return RunResult(ok=False, error=f"could not start claude: {exc}")
    try:
        stdout, _stderr = await asyncio.wait_for(proc.communicate(prompt.encode("utf-8")), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return RunResult(ok=False, error="timeout", duration_s=time.monotonic() - start)
    result = parse_result(stdout)
    result.duration_s = time.monotonic() - start
    if proc.returncode != 0 and result.ok:
        result.ok, result.error = False, f"exit {proc.returncode}"
    elif proc.returncode != 0 and not result.error:
        result.error = f"exit {proc.returncode}"
    return result
```

- [ ] **Step 3: `config.py`** — add `from assistant.claude_runner import parse_command` to the import block at the top (the runner imports only the stdlib, so there is no cycle), then append after `BOT_VERSION`:

```python
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
```

Add `"assistant"` to `_FRESH` handling in `tests/conftest.py` (`name == "assistant" or name.startswith("assistant.")`) so fresh imports re-read config.

- [ ] **Step 4: Run** `python -m pytest tests/test_claude_runner.py -v` → 7 PASS. Add a test in `tests/test_config.py`:

```python
def test_assistant_defaults_off_and_parses_ids(fresh_import, monkeypatch):
    monkeypatch.delenv("ASSISTANT_ENABLED", raising=False)
    monkeypatch.setenv("ASSISTANT_USER_IDS", "5, x,6")
    cfg = fresh_import("config")
    assert cfg.ASSISTANT_ENABLED is False and cfg.ASSISTANT_USER_IDS == frozenset({5, 6})
```

- [ ] **Step 5: Harness doctor line.** In `doctor()`'s `boot_probe`, return also `import config as _c; _c.CLAUDE_CMD`; print `line(cmd[-1].endswith("fake_claude.py"), f"assistant uses the fake claude: {cmd}")`.

- [ ] **Step 6: Gate**, commit `feat(assistant): isolated claude -p runner and config` (files: `assistant/`, `config.py`, `tests/test_claude_runner.py`, `tests/test_config.py`, `tests/conftest.py`, `pyproject.toml`, harness).

---

### Task 3: Live smoke check (owner runs it) — CHECKPOINT

**Files:** Create `assistant/smoke.py`.

- [ ] **Step 1: `assistant/smoke.py`:**

```python
"""Owner-run live check of the real `claude` CLI with the assistant's exact lockdown flags.

    python -m assistant.smoke --env-file PATH_TO_.env [--claude claude] [--model haiku]

Reads only CLAUDE_CODE_OAUTH_TOKEN from the env file. Prints the init tool/MCP list, the result, duration, and cost.
Exit 0 only if the call succeeded with structured output, no tools beyond StructuredOutput, and no MCP servers.
"""

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from dotenv import dotenv_values

from assistant import claude_runner as cr

SCHEMA = {"type": "object", "properties": {"kind": {"type": "string", "enum": ["answer"]}, "text": {"type": "string"}},
          "required": ["kind", "text"], "additionalProperties": False}


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--env-file", required=True)
    p.add_argument("--claude", default="claude")
    p.add_argument("--model", default="haiku")
    a = p.parse_args(argv)
    token = dotenv_values(a.env_file).get("CLAUDE_CODE_OAUTH_TOKEN") or ""
    if not token:
        print("CLAUDE_CODE_OAUTH_TOKEN not found in the env file (create one with `claude setup-token`).")
        return 2
    root = Path(tempfile.mkdtemp(prefix="mpb-assistant-smoke-"))
    (root / "prompt.md").write_text("Answer briefly. Return kind=answer.", encoding="utf-8")
    argv_ = cr.build_argv(cr.parse_command(a.claude), schema=SCHEMA, model=a.model,
                          system_prompt_file=root / "prompt.md")
    i = argv_.index("--output-format")
    argv_[i + 1] = "stream-json"
    argv_.insert(i + 2, "--verbose")
    start = time.monotonic()
    proc = subprocess.run(argv_, input="Say pong.".encode(), capture_output=True, cwd=root,
                          env=cr.minimal_env(config_dir=root / "config", token=token), timeout=120)
    took = time.monotonic() - start
    lines = [json.loads(x) for x in proc.stdout.decode("utf-8", "replace").splitlines() if x.strip().startswith("{")]
    init = next((m for m in lines if m.get("type") == "system" and m.get("subtype") == "init"), {})
    result = next((m for m in reversed(lines) if m.get("type") == "result"), {})
    tools, mcp = init.get("tools"), init.get("mcp_servers")
    print(f"exit={proc.returncode} seconds={took:.1f}")
    print(f"init.tools={tools} init.mcp_servers={mcp} init.model={init.get('model')}")
    print(f"result.subtype={result.get('subtype')} structured_output={result.get('structured_output')}")
    print(f"total_cost_usd={result.get('total_cost_usd')} num_turns={result.get('num_turns')}")
    if proc.returncode != 0:
        print("stderr:", proc.stderr.decode("utf-8", "replace")[-2000:])
    ok = (result.get("subtype") == "success" and isinstance(result.get("structured_output"), dict)
          and set(tools or []) <= {"StructuredOutput"} and not mcp)
    print("SMOKE PASS" if ok else "SMOKE FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Commit** `feat(assistant): owner-run live smoke check`.

- [ ] **Step 3: STOP — owner action.** The executor cannot run this (it needs the owner's subscription token). Ask the owner to: run `claude setup-token`, put `CLAUDE_CODE_OAUTH_TOKEN=...` in the bot's `.env` on the Pi (and optionally locally), then from the repo run `python -m assistant.smoke --env-file .env` (on the Pi) and paste the output. Record it in the ledger.

- [ ] **Step 4: Act on the result.**
  - `SMOKE PASS` → continue.
  - `structured_output` missing with `--tools ""` → change `build_argv` to drop `--tools ""` and add `--disallowedTools` with the built-in tool names the init message listed (keep `StructuredOutput` allowed); update `test_build_argv_locks_down_tools` accordingly; rerun smoke.
  - `num_turns` hit the limit → raise `max_turns` default to 5.
  - Unknown-flag error → remove that flag, note the CLI version, rerun.
  - Seconds > 45 on the Pi → raise `ASSISTANT_TIMEOUT` default to 90 and note it in CLAUDE.md.

---

### Task 4: Intent validation, command building, system prompt

**Files:** Create `assistant/intent.py`, `assistant/system_prompt.md`, `tests/test_intent.py`.

**Interfaces:** Produces `READ_COMMANDS`, `WRITE_COMMANDS`, `FLOW_COMMANDS`, `ALL_COMMANDS` (frozensets), `USAGE: Dict[str, str]`, `SCHEMA: dict`, `IntentError(ValueError)`, `Intent(kind, text, command, args: tuple, summary)` with `.needs_confirm`, `validate(obj) -> Intent`, `build_command(Intent) -> str`, `render_system_prompt(template_path) -> str`, `TEMPLATE_PATH: Path`.

- [ ] **Step 1: Failing tests** `tests/test_intent.py`:

```python
import pytest

from assistant import intent as it


def test_answer_and_command_roundtrip():
    a = it.validate({"kind": "answer", "text": " hi "})
    assert a.kind == "answer" and a.text == "hi" and not a.needs_confirm
    c = it.validate({"kind": "command", "command": "snipe", "args": ["215147", " 5000 ", "", "Vibrant Shard"]})
    assert it.build_command(c) == "!snipe 215147 5000 Vibrant Shard" and c.needs_confirm
    assert not it.validate({"kind": "command", "command": "snipes"}).needs_confirm


@pytest.mark.parametrize("obj", [
    "text", {"kind": "answer"}, {"kind": "answer", "text": "  "}, {"kind": "nope"},
    {"kind": "command", "command": "cleanup"}, {"kind": "command", "command": "snipe", "args": "x"},
    {"kind": "command", "command": "snipe", "args": [1]},
    {"kind": "command", "command": "snipe", "args": ["!cleanup"]},
    {"kind": "command", "command": "snipe", "args": ["a\nb"]},
    {"kind": "command", "command": "snipe", "args": ["@everyone"]},
    {"kind": "command", "command": "snipe", "args": ["<@123>"]},
    {"kind": "command", "command": "snipe", "args": ["`x`"]},
    {"kind": "command", "command": "snipe", "args": ["x" * 101]},
    {"kind": "command", "command": "snipe", "args": ["a"] * 21},
])
def test_rejects(obj):
    with pytest.raises(it.IntentError):
        it.validate(obj)


def test_sets_are_disjoint_and_usage_covers_all():
    assert not (it.READ_COMMANDS & it.WRITE_COMMANDS) and set(it.USAGE) == set(it.ALL_COMMANDS)
    assert set(it.SCHEMA["properties"]["command"]["enum"]) == set(it.ALL_COMMANDS)


def test_system_prompt_lists_every_command():
    text = it.render_system_prompt(it.TEMPLATE_PATH)
    assert "{{COMMANDS}}" not in text and all(f"!{name}" in text for name in it.ALL_COMMANDS)
```

Run → FAIL (`cannot import name 'intent'`).

- [ ] **Step 2: `assistant/intent.py`:**

```python
"""Validate Claude's structured output (untrusted) and turn it into a bot command string."""

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
            if len(arg) > MAX_ARG_LEN or arg.startswith("!") or any(bad in arg for bad in _FORBIDDEN):
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
```

- [ ] **Step 3: `assistant/system_prompt.md`:**

```markdown
You are the assistant inside a World of Warcraft Mythic+ guild's Discord bot, talking to the bot's owner.

You are a helpful general assistant first. Answer any question directly — about the bot, about WoW, Mythic+, theorycrafting, or anything unrelated — with kind "answer" and your reply in "text". Keep answers short and Discord-friendly.

Only when the message clearly asks the bot to DO one of the actions below, reply with kind "command", the command name (without "!") in "command", its arguments in "args" (one string per argument, in order; a multi-word label may be one string), and a one-line "summary" of what it will do. Never pick a command for a question. If an action is unclear or an argument is missing, answer with kind "answer" and ask for what is missing.

Never claim you have done something; the bot runs the command after you reply. Never put mentions, backticks, line breaks, or "!" at the start of an argument.

Commands:
{{COMMANDS}}
```

- [ ] **Step 4: Run** `python -m pytest tests/test_intent.py -v` → PASS. **Gate**, commit `feat(assistant): validate Claude intents against a command allowlist`.

---

### Task 5: Trigger predicate

**Files:** Create `cogs/assistant.py` (predicate only for now — no cog class yet, so do not add it to `EXTENSIONS`), `tests/test_trigger.py`.

**Interfaces:** Produces `should_handle(message, *, bot_user_id: int, allowed_ids, enabled: bool) -> Optional[str]` — `None` = ignore; `""` = addressed but empty; otherwise the cleaned prompt.

- [ ] **Step 1: Failing tests** `tests/test_trigger.py`:

```python
from types import SimpleNamespace

import discord
import pytest

from cogs.assistant import should_handle

BOT, OWNER = 99, 5


def msg(content, *, dm=False, author=OWNER, bot=False, webhook=None, mtype=discord.MessageType.default,
        group=False, raw=None, mentions=()):
    ctype = discord.ChannelType.group if group else (discord.ChannelType.private if dm else discord.ChannelType.text)
    raw = raw if raw is not None else [int(x) for x in __import__("re").findall(r"<@!?(\d+)>", content)]
    return SimpleNamespace(content=content, author=SimpleNamespace(id=author, bot=bot), webhook_id=webhook,
                           type=mtype, channel=SimpleNamespace(type=ctype), raw_mentions=raw,
                           mentions=[SimpleNamespace(id=i) for i in mentions])


def run(m, enabled=True):
    return should_handle(m, bot_user_id=BOT, allowed_ids={OWNER}, enabled=enabled)


@pytest.mark.parametrize("m,expected", [
    (msg("hi", dm=True), "hi"),
    (msg("hi"), None),
    (msg("<@99> hi"), "hi"),
    (msg("<@!99>  hi <@99>"), "hi"),
    (msg("hi", mentions=[BOT]), "hi"),                      # reply with ping
    (msg("@everyone hi", raw=[]), None),
    (msg("<@99> hi", author=7), None),                      # not allowlisted
    (msg("hi", dm=True, author=7), None),
    (msg("<@99> hi", bot=True), None),
    (msg("<@99> hi", webhook=1), None),
    (msg("<@99> hi", mtype=discord.MessageType.pins_add), None),
    (msg("!keys", dm=True), None),
    (msg("<@99> !keys"), None),
    (msg("hi", group=True), None),                          # group DM needs a mention
    (msg("<@99> hi", group=True), "hi"),
    (msg("<@99>"), ""),
])
def test_should_handle(m, expected):
    assert run(m) == expected


def test_disabled_ignores_everything():
    assert run(msg("hi", dm=True), enabled=False) is None
```

Run → FAIL (`No module named 'cogs.assistant'`).

- [ ] **Step 2: `cogs/assistant.py`:**

```python
"""Assistant: plain-language messages from allowlisted users, answered or acted on via headless Claude."""

import re
from typing import Optional

import discord

_HANDLED_TYPES = (discord.MessageType.default, discord.MessageType.reply)


def should_handle(message, *, bot_user_id: int, allowed_ids, enabled: bool) -> Optional[str]:
    """None = not for the assistant; "" = addressed with nothing to say; otherwise the prompt without the mention."""
    if not enabled:
        return None
    author = message.author
    if getattr(author, "bot", False) or getattr(message, "webhook_id", None):
        return None
    if getattr(message, "type", discord.MessageType.default) not in _HANDLED_TYPES:
        return None
    if author.id not in allowed_ids:
        return None
    content = message.content or ""
    if content.lstrip().startswith("!"):
        return None
    is_dm = getattr(message.channel, "type", None) is discord.ChannelType.private
    mentioned = bot_user_id in (getattr(message, "raw_mentions", None) or []) or any(
        getattr(u, "id", None) == bot_user_id for u in (getattr(message, "mentions", None) or [])
    )
    if not (is_dm or mentioned):
        return None
    cleaned = re.sub(rf"<@!?{bot_user_id}>", "", content).strip()
    if cleaned.startswith("!"):
        return None
    return cleaned
```

- [ ] **Step 3: Run** → PASS. **Gate** (the cog module isn't in `EXTENSIONS` yet, so the harness is unchanged), commit `feat(assistant): trigger predicate (DM or mention, allowlisted)`.

---

### Task 6: Daily usage counter

**Files:** Create `assistant/usage.py`, `tests/test_usage.py`; Modify `.gitignore` (add `assistant.json` and `assistant.json.tmp` beside the other state files).

**Interfaces:** Produces `DailyUsage(day="", count=0, cost_usd=0.0)`, `.load(path="assistant.json")` (classmethod; missing/corrupt → fresh), `.save(path="assistant.json")` (atomic), `.allow(today: str, cap: int) -> bool`, `.record(today: str, cost: float) -> None`.

- [ ] **Step 1: Failing tests** `tests/test_usage.py`:

```python
from assistant.usage import DailyUsage


def test_cap_and_rollover(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    u = DailyUsage.load()
    assert u.allow("2026-11-03", 2)
    u.record("2026-11-03", 0.01)
    u.record("2026-11-03", 0.01)
    assert not u.allow("2026-11-03", 2)
    assert u.allow("2026-11-04", 2)            # new day resets
    u.save()
    again = DailyUsage.load()
    assert (again.day, again.count) == ("2026-11-04", 0)   # allow() on the new day already rolled it over


def test_persists_and_tolerates_corruption(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    u = DailyUsage.load()
    u.record("2026-11-03", 0.5)
    u.save()
    assert DailyUsage.load().count == 1
    (tmp_path / "assistant.json").write_text("{bad", encoding="utf-8")
    assert DailyUsage.load().count == 0
```

Run → FAIL.

- [ ] **Step 2: `assistant/usage.py`:**

```python
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
```

- [ ] **Step 3: Run** → PASS. **Gate**, commit `feat(assistant): daily usage cap persisted in assistant.json`.

---

### Task 7: The `Assistant` cog end to end

**Files:** Modify `cogs/assistant.py`, `cogs/__init__.py` (append `"cogs.assistant"` **last**, so every command exists when it loads), harness `scripts/journeys.py` (journey `assistant`).

**Interfaces:** Consumes Tasks 2, 4, 5, 6. Produces `Assistant(commands.Cog)` with listener `on_assistant_message`, `ConfirmView(author_id, timeout=60)` (`.confirmed: bool`), `async setup(bot)`.

- [ ] **Step 1: Failing journey.** Add to `journeys.py` and register `"assistant": (assistant, "plain-language DM/mention → answer or confirmed command via fake claude")`:

```python
def _acts(h, before):
    kinds = ("send", "edit", "delete", "interaction_response", "followup", "log_error")
    return [e for e in h.world.events[before:] if e["kind"] in kinds]


def _claude_calls(h):
    path = h.data_dir / "assistant-work" / "fake_claude_calls.jsonl"
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


async def assistant(h):
    owner, other, stranger, bot = h.banker, h.coordinator, h.user("Stranger"), h.mention()

    await h.say(owner, "what is the best mage spec?", "dm")
    h.check("1:1 DM without a mention is answered", bool(await h.wait_for(owner, "fake answer: what is the best mage spec?")))

    before, calls = len(h.world.events), len(_claude_calls(h))
    await h.say(owner, "what is the best mage spec?", "key")
    await h.say(owner, "@everyone hello", "key")
    await h.say(stranger, "hello", "dm")
    await h.say(stranger, f"{bot} hello", "key")
    h.check("unaddressed, @everyone, and non-allowlisted messages: no reply and no claude call",
            not _acts(h, before) and len(_claude_calls(h)) == calls, _acts(h, before))

    await h.say(owner, f"{bot} how do keys work?", "key")
    h.check("#keys @mention is answered", bool(await h.wait_for("key", "fake answer: how do keys work?")))
    await h.say(owner, bot, "key")
    h.check("empty mention asks what to do", bool(await h.wait_for("key", "What can I help with?")))

    before = len(h.world.events)
    await h.say(owner, "!snipes", "dm")
    h.check("typed !command gets exactly one reply (assistant stays out)",
            len([e for e in h.world.events[before:] if e["kind"] == "send"]) == 1)

    await h.say(owner, "please snipe 215147 for me under 5000", "dm")
    ask = await h.wait_for(owner, "I'll run `!snipe 215147 5000 Vibrant Shard`")
    await h.click(owner, ask, "Confirm")
    await h.wait_for(owner, "Sniping")
    h.check("confirmed write ran through the real command", str(owner.id) in json.dumps(h.state_file("snipes.json")))

    snapshot = h.state_file("snipes.json")
    await h.say(owner, "unsnipe 215147", "dm")
    ask = await h.wait_for(owner, "I'll run `!unsnipe 215147`")
    await h.click(owner, ask, "Cancel")
    await h.wait_for(owner, "Cancelled")
    h.check("cancelled write changes nothing", h.state_file("snipes.json") == snapshot)

    before = len(h.world.events)
    await h.say(owner, "show my snipes", "dm")
    await h.wait_for(owner, "Your snipes")
    h.check("read command runs without a confirm prompt",
            not any("I'll run" in (e.get("content") or "") for e in h.world.events[before:]))

    await h.say(other, "watch item 241326", "dm")
    ask = await h.wait_for(other, "I'll run `!watch 241326`")
    await h.click(other, ask, "Confirm")
    await h.wait_for(other, "don't have access to `!watch`")
    h.check("gates follow the Discord author, not Claude", h.state_file("watches.json") in (None, {}, []))

    await h.say(owner, "make a run thursday", "dm")
    h.check("key request opens the DM flow", bool(await h.wait_for(owner, "Before scheduling a key")))

    for text in ("evil", "inject", "fake:badjson", "fake:error", "fake:crash", "fake:timeout"):
        await h.say(other, text, "dm")
        h.check(f"{text}: one failure reply", bool(await h.wait_for(other, "couldn't work that out", timeout=15)))

    calls = _claude_calls(h)
    secrets = {"CLIENT_KEY", "BLIZZ_CLIENT_SECRET", "BLIZZ_CLIENT_ID", "UNDERMINE_API_KEY", "RAIDERIO_API_KEY"}
    h.check("no claude call saw a bot secret", all(not (secrets & set(c["env_keys"])) for c in calls), len(calls))
    h.check("every call is locked down",
            all("--strict-mcp-config" in c["argv"] and c["argv"][c["argv"].index("--tools") + 1] == "" for c in calls))
    h.check("mentions never reach the prompt", all("<@" not in c["prompt"] for c in calls))
    h.check("no errors logged", not [e for e in h.world.events if e["kind"] == "log_error"])
```

(add `import json` at the top of `journeys.py` if `json_dump` is its only use today.)

Run `harness.py run assistant` → FAIL (no Assistant cog: the DM gets no answer).

- [ ] **Step 2: Implement the cog** — append to `cogs/assistant.py` (keep `should_handle`), with these imports at the top:

```python
import asyncio
import copy
import logging
from datetime import datetime
from pathlib import Path

from discord.ext import commands

import config
from assistant import claude_runner, intent
from assistant.usage import DailyUsage

logger = logging.getLogger("discord")
FAILURE = "I couldn't work that out — try `!help`."
NO_MENTIONS = discord.AllowedMentions.none()


def _chunks(text: str, size: int = 2000):
    return [text[i:i + size] for i in range(0, len(text), size)] or [""]


class ConfirmView(discord.ui.View):
    def __init__(self, author_id: int, timeout: float = 60) -> None:
        super().__init__(timeout=timeout)
        self.author_id = author_id
        self.confirmed = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.author_id

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        self.confirmed = True
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await interaction.response.defer()
        self.stop()


class Assistant(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot
        self._busy = asyncio.Semaphore(1)
        self.usage = DailyUsage.load()
        self.system_prompt_file = Path(config.ASSISTANT_CONFIG_DIR) / "system_prompt.md"

    async def cog_load(self) -> None:
        unknown = [name for name in intent.USAGE if self.bot.get_command(name) is None]
        if unknown:
            raise RuntimeError(f"assistant catalog names commands the bot doesn't have: {unknown}")
        if config.ASSISTANT_ENABLED:
            self.system_prompt_file.parent.mkdir(parents=True, exist_ok=True)
            self.system_prompt_file.write_text(intent.render_system_prompt(intent.TEMPLATE_PATH), encoding="utf-8")

    @commands.Cog.listener("on_message")
    async def on_assistant_message(self, message: discord.Message) -> None:
        prompt = should_handle(message, bot_user_id=self.bot.user.id, allowed_ids=config.ASSISTANT_USER_IDS,
                               enabled=config.ASSISTANT_ENABLED)
        if prompt is None:
            return
        if not prompt:
            await message.channel.send("What can I help with?")
            return
        today = datetime.now(config._CST).date().isoformat()
        if not self.usage.allow(today, config.ASSISTANT_DAILY_CAP):
            await message.channel.send("Daily assistant limit reached.")
            return
        if self._busy.locked():
            await message.channel.send("One moment — still working on the last one.")
            return
        async with self._busy:
            async with message.channel.typing():
                result = await claude_runner.run_claude(
                    prompt, cmd=config.CLAUDE_CMD, schema=intent.SCHEMA, model=config.ASSISTANT_MODEL,
                    system_prompt_file=self.system_prompt_file, work_dir=config.ASSISTANT_WORK_DIR,
                    config_dir=config.ASSISTANT_CONFIG_DIR, token=config.CLAUDE_CODE_OAUTH_TOKEN,
                    timeout=config.ASSISTANT_TIMEOUT,
                )
            self.usage.record(today, result.cost_usd)
            self.usage.save()
        logger.info("assistant: user=%s dm=%s ok=%s subtype=%s cost=%.4f seconds=%.1f", message.author.id,
                    message.guild is None, result.ok, result.subtype, result.cost_usd, result.duration_s)
        logger.debug("assistant: prompt=%r", prompt[:200])
        if not result.ok:
            logger.warning("assistant: claude call failed: %s", result.error)
            await message.channel.send(FAILURE)
            return
        try:
            parsed = intent.validate(result.output)
        except intent.IntentError as exc:
            logger.warning("assistant: rejected intent: %s", exc)
            await message.channel.send(FAILURE)
            return
        await self._act(message, parsed)

    async def _act(self, message: discord.Message, parsed: "intent.Intent") -> None:
        if parsed.kind == "answer":
            for chunk in _chunks(parsed.text):
                await message.channel.send(chunk, allowed_mentions=NO_MENTIONS)
            return
        if parsed.command in intent.FLOW_COMMANDS:
            await self.bot._do_key_request_flow(message.author)
            return
        command = intent.build_command(parsed)
        if parsed.needs_confirm:
            view = ConfirmView(message.author.id)
            ask = await message.channel.send(f"I'll run `{command}` — confirm?", view=view, allowed_mentions=NO_MENTIONS)
            await view.wait()
            if not view.confirmed:
                await ask.edit(content=f"Cancelled `{command}`.", view=None)
                return
            await ask.edit(content=f"Running `{command}`.", view=None)
        await self._invoke(message, command)

    async def _invoke(self, message: discord.Message, command: str) -> None:
        proxy = copy.copy(message)
        proxy.content = command
        ctx = await self.bot.get_context(proxy)
        if ctx.command is None:
            await message.channel.send(FAILURE)
            return
        try:
            allowed = await ctx.command.can_run(ctx)
        except commands.CheckFailure:
            allowed = False
        if not allowed:
            await message.channel.send(f"You don't have access to `!{ctx.command.name}` here.")
            return
        await self.bot.invoke(ctx)


async def setup(bot) -> None:
    await bot.add_cog(Assistant(bot))
```

and append `"cogs.assistant"` as the last entry of `cogs.EXTENSIONS`.

- [ ] **Step 3: Run** `harness.py run assistant` → PASS. Update the doctor's cog-count expectation from 8 to 9 (`cogs loaded: [...]` list now includes `Assistant`). Note: `!key`'s `in_channels(KEY)` gate is bypassed here deliberately — the channel rule exists to keep #keys tidy, the key-request button is open to everyone, and the flow itself runs in DMs (document in CLAUDE.md, Task 8).

- [ ] **Step 4: Gate** (all journeys incl. `assistant`), commit `feat(assistant): Assistant cog — answers and confirmed commands via headless Claude`.

---

### Task 8: Ship and document

**Files:** Dockerfile, pyproject.toml, CLAUDE.md, README.md, CHANGELOG.md, `config.py` (`BOT_VERSION`), `cogs/admin.py` (`!help` embed), `.claude/skills/verify-mythic-plus-bot/features/` (new `assistant.md` + README index).

- [ ] **Step 1: Dockerfile.** `COPY assistant/ ./assistant/`. Install the native Claude Code CLI using the command from https://code.claude.com/docs/en/setup (read it; pin a version if the installer supports it), set `ENV DISABLE_AUTOUPDATER=1`, and run the install as `botuser` so `claude` lands on that user's PATH. If the official installer can't run in `python:3.10-slim`, note it in CLAUDE.md as a manual Pi step instead.
- [ ] **Step 2: `!help`** — add one field to the embed in `cogs/admin.py`: name `🤖 Assistant (owner, experimental)`, value `DM me, or mention me, in plain language — I'll answer or offer to run a command (writes ask for confirmation).`
- [ ] **Step 3: Docs.** CLAUDE.md: file map rows (`assistant/*`, `cogs/assistant.py`), a "Natural-language assistant" workflow section (trigger rule, isolation flags, env list, confirm rule, allowlist, `!key` gate bypass rationale, `assistant.json`), env vars table additions, commands table note. README: setup (`claude setup-token`, `.env` keys, `ASSISTANT_ENABLED=1`, smoke command). Feature map: `features/assistant.md` (four-H2 contract, journey `assistant`) + README index row.
- [ ] **Step 4: Release.** `config.BOT_VERSION = "1.11.0"`; CHANGELOG `## [1.11.0] - <date>` above 1.10.1 with `### Improvements` → `- Experimental: the bot owner can DM or mention the bot in plain language; it answers or offers to run a command.` (Whether 1.11.0's announcement should also restate 1.10.1's notes is an owner decision recorded at finish — do not merge sections.) CLAUDE.md version line 1.11.0.
- [ ] **Step 5: Gate**, commit `docs(assistant): ship config, Docker, help, and release 1.11.0`.

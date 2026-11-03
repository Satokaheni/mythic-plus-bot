"""Run one isolated headless `claude -p` call and parse its JSON result."""

import asyncio
import contextlib
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
    detail: str = ""  # a short reason from the CLI's result (e.g. an auth failure); never env or the token


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


def _cost(value) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _detail(data: dict) -> str:
    """The CLI's own failure text: `result`, else the first of `errors[]`."""
    errors = data.get("errors")
    first_error = errors[0] if isinstance(errors, list) and errors else ""
    return str(data.get("result") or first_error or "")[:200]


def parse_result(stdout: bytes) -> RunResult:
    try:
        data = json.loads(stdout.decode("utf-8", "replace"))
    except ValueError:
        return RunResult(ok=False, error="unparseable output")
    if isinstance(data, list):
        data = next((m for m in reversed(data) if isinstance(m, dict) and m.get("type") == "result"), {})
    if not isinstance(data, dict) or data.get("type") != "result":
        return RunResult(ok=False, error="no result message")
    cost = _cost(data.get("total_cost_usd"))
    subtype = str(data.get("subtype", ""))
    if subtype != "success" or data.get("is_error"):
        error = f"claude {subtype or 'error'}" + (": is_error" if data.get("is_error") else "")
        return RunResult(ok=False, error=error, cost_usd=cost, subtype=subtype, detail=_detail(data))
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
        with contextlib.suppress(ProcessLookupError):  # it may have exited on its own just now
            proc.kill()
        await proc.wait()
        return RunResult(ok=False, error="timeout", duration_s=time.monotonic() - start)
    except asyncio.CancelledError:  # bot shutdown: don't orphan the child
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()
        raise
    result = parse_result(stdout)
    result.duration_s = time.monotonic() - start
    if proc.returncode != 0 and result.ok:
        result.ok, result.error = False, f"exit {proc.returncode}"
    elif proc.returncode != 0 and not result.error:
        result.error = f"exit {proc.returncode}"
    return result

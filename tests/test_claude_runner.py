import asyncio
import json
import sys
from pathlib import Path

import pytest

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


def test_run_claude_cancel_kills_child(tmp_path, monkeypatch):
    """Bot shutdown cancels the awaiting task; the claude child must not outlive it."""
    procs = []
    real_exec = asyncio.create_subprocess_exec

    async def spy(*args, **kwargs):
        proc = await real_exec(*args, **kwargs)
        procs.append(proc)
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spy)

    async def scenario():
        task = asyncio.create_task(cr.run_claude(
            "fake:timeout", cmd=FAKE, schema=SCHEMA, model="haiku", system_prompt_file=tmp_path / "p.md",
            work_dir=tmp_path / "w", config_dir=tmp_path / "c", token="tok", timeout=30))
        for _ in range(100):
            if procs:
                break
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.5)
        task.cancel()
        try:
            await task
            cancelled = False
        except asyncio.CancelledError:
            cancelled = True
        alive = procs[0].returncode is None
        if alive:  # don't leak the 600 s sleeper when the runner fails to kill it
            procs[0].kill()
            await procs[0].wait()
        return cancelled, alive

    cancelled, alive = asyncio.run(scenario())
    assert cancelled
    assert not alive, "child process still running after the task was cancelled"


def _kill_then_vanish(monkeypatch):
    """The child exits on its own just before kill(): kill() raises ProcessLookupError (we still really kill it)."""
    real_exec = asyncio.create_subprocess_exec

    async def spy(*args, **kwargs):
        proc = await real_exec(*args, **kwargs)
        real_kill = proc.kill

        def kill():
            real_kill()
            raise ProcessLookupError

        proc.kill = kill
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spy)


def test_timeout_survives_a_child_that_already_exited(tmp_path, monkeypatch):
    _kill_then_vanish(monkeypatch)
    r = _run("fake:timeout", tmp_path, timeout=1)
    assert not r.ok and r.error == "timeout"


def test_cancel_survives_a_child_that_already_exited(tmp_path, monkeypatch):
    _kill_then_vanish(monkeypatch)

    async def scenario():
        task = asyncio.create_task(cr.run_claude(
            "fake:timeout", cmd=FAKE, schema=SCHEMA, model="haiku", system_prompt_file=tmp_path / "p.md",
            work_dir=tmp_path / "w", config_dir=tmp_path / "c", token="tok", timeout=30))
        await asyncio.sleep(0.5)
        task.cancel()
        await task

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(scenario())

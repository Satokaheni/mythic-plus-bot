import json
import subprocess
import sys
import tempfile
from pathlib import Path

from assistant import smoke

FAKE = Path(__file__).resolve().parent.parent / ".claude/skills/verify-mythic-plus-bot/scripts/fake_claude.py"

INIT = {"type": "system", "subtype": "init", "tools": ["StructuredOutput"], "mcp_servers": [], "model": "haiku"}
RESULT = {"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.001, "num_turns": 2,
          "structured_output": {"kind": "answer", "text": "pong"}}


def _claude(script=FAKE):
    return json.dumps([sys.executable, str(script)])


def _env(tmp_path):
    env = tmp_path / "env"
    env.write_text("CLAUDE_CODE_OAUTH_TOKEN=fake\n", encoding="utf-8")
    return str(env)


def _stream_cli(tmp_path, lines):
    """A stand-in CLI that prints the given stream-json lines (strings are printed verbatim)."""
    script = tmp_path / "stream_claude.py"
    body = [x if isinstance(x, str) else json.dumps(x) for x in lines]
    script.write_text(f"import sys\nsys.stdin.read()\nprint({chr(10).join(body)!r})\n", encoding="utf-8")
    return _claude(script)


def test_smoke_passes_with_locked_down_init(tmp_path, capsys):
    code = smoke.main(["--env-file", _env(tmp_path), "--claude", _stream_cli(tmp_path, [INIT, RESULT])])
    out = capsys.readouterr().out
    assert code == 0 and "SMOKE PASS" in out


def test_smoke_fails_without_init_message(tmp_path, capsys):
    # the harness fake prints a bare result and no system/init message: tools can't be verified -> FAIL
    code = smoke.main(["--env-file", _env(tmp_path), "--claude", _claude()])
    out = capsys.readouterr().out
    assert "result.subtype=success" in out
    assert code == 1 and "SMOKE FAIL" in out


def test_smoke_fails_when_init_lists_extra_tools(tmp_path, capsys):
    init = dict(INIT, tools=["StructuredOutput", "Bash"])
    assert smoke.main(["--env-file", _env(tmp_path), "--claude", _stream_cli(tmp_path, [init, RESULT])]) == 1
    assert "SMOKE FAIL" in capsys.readouterr().out


def test_smoke_skips_unparseable_lines(tmp_path, capsys):
    cli = _stream_cli(tmp_path, ["{not json", INIT, "{\"truncated\": ", RESULT])
    assert smoke.main(["--env-file", _env(tmp_path), "--claude", cli]) == 0
    assert "SMOKE PASS" in capsys.readouterr().out


def test_smoke_timeout_fails_cleanly(tmp_path, capsys, monkeypatch):
    def hang(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs.get("timeout"))

    monkeypatch.setattr(smoke.subprocess, "run", hang)
    assert smoke.main(["--env-file", _env(tmp_path), "--claude", _claude()]) == 1
    assert "SMOKE FAIL (timeout)" in capsys.readouterr().out


def test_smoke_removes_its_temp_dir(tmp_path, monkeypatch):
    made = []
    real = tempfile.mkdtemp

    def spy(*args, **kwargs):
        made.append(Path(real(*args, dir=tmp_path, **{k: v for k, v in kwargs.items() if k != "dir"})))
        return str(made[-1])

    monkeypatch.setattr(smoke.tempfile, "mkdtemp", spy)
    smoke.main(["--env-file", _env(tmp_path), "--claude", _stream_cli(tmp_path, [INIT, RESULT])])
    assert made and not made[0].exists()


def test_smoke_missing_token_exits_2(tmp_path, capsys):
    env = tmp_path / "env"
    env.write_text("OTHER=1\n", encoding="utf-8")
    assert smoke.main(["--env-file", str(env), "--claude", _claude()]) == 2

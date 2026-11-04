import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from mythicbot.assistant import smoke

FAKE = Path(__file__).resolve().parent.parent / ".claude/skills/verify-mythic-plus-bot/scripts/fake_claude.py"

INIT = {"type": "system", "subtype": "init", "tools": ["StructuredOutput"], "mcp_servers": [], "model": "haiku"}
RESULT = {"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.001, "num_turns": 2,
          "structured_output": {"kind": "answer", "text": "pong"}}
CONTEXT_RESULT = dict(RESULT, structured_output={"kind": "command", "command": "snipe", "args": ["215147", "5000"]})


def _claude(script=FAKE):
    return json.dumps([sys.executable, str(script)])


def _env(tmp_path):
    env = tmp_path / "env"
    env.write_text("CLAUDE_CODE_OAUTH_TOKEN=fake\n", encoding="utf-8")
    return str(env)


def _stream_cli(tmp_path, lines, context_lines=None):
    """A stand-in CLI printing stream-json lines; the two-turn context call (stdin has "history") gets context_lines."""
    def text(ls):
        return "\n".join(x if isinstance(x, str) else json.dumps(x) for x in ls)

    context_text = text(context_lines if context_lines is not None else [INIT, CONTEXT_RESULT])
    script = tmp_path / "stream_claude.py"
    script.write_text("import sys\n"
                      f"print({context_text!r} if '\"history\"' in sys.stdin.read() else {text(lines)!r})\n",
                      encoding="utf-8")
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


def test_smoke_fails_when_the_followup_ignores_history(tmp_path, capsys):
    cli = _stream_cli(tmp_path, [INIT, RESULT], context_lines=[INIT, RESULT])  # context call answers "pong"
    assert smoke.main(["--env-file", _env(tmp_path), "--claude", cli]) == 1
    out = capsys.readouterr().out
    assert "follow-up resolved to snipe 215147 5000: False" in out and "SMOKE FAIL" in out


def test_smoke_context_call_is_locked_down_too(tmp_path, capsys):
    init = dict(INIT, tools=["StructuredOutput", "Bash"])
    cli = _stream_cli(tmp_path, [INIT, RESULT], context_lines=[init, CONTEXT_RESULT])
    assert smoke.main(["--env-file", _env(tmp_path), "--claude", cli]) == 1
    assert "SMOKE FAIL" in capsys.readouterr().out


@pytest.mark.parametrize("args", [["215147", "5000"], ["215147", "5,000"], ["215147", "5000g", "Vibrant Shard"]])
def test_followup_snipe_accepts_price_spellings(args):
    assert smoke._is_followup_snipe({"kind": "command", "command": "snipe", "args": args})


def test_followup_snipe_rejects_other_results():
    assert not smoke._is_followup_snipe({"kind": "answer", "text": "5000"})
    assert not smoke._is_followup_snipe({"kind": "command", "command": "snipe", "args": ["215147"]})
    assert not smoke._is_followup_snipe(None)

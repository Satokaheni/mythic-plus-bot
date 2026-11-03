import json
import sys
from pathlib import Path

from assistant import smoke

FAKE = Path(__file__).resolve().parent.parent / ".claude/skills/verify-mythic-plus-bot/scripts/fake_claude.py"


def _claude():
    return json.dumps([sys.executable, str(FAKE)])


def test_smoke_runs_against_fake_cli(tmp_path, capsys):
    env = tmp_path / "env"
    env.write_text("CLAUDE_CODE_OAUTH_TOKEN=fake\n", encoding="utf-8")
    code = smoke.main(["--env-file", str(env), "--claude", _claude()])
    out = capsys.readouterr().out
    assert "result.subtype=success" in out
    # fake emits structured_output and no init message, so tools/mcp are empty and the check passes
    assert code == 0
    assert "SMOKE PASS" in out


def test_smoke_missing_token_exits_2(tmp_path, capsys):
    env = tmp_path / "env"
    env.write_text("OTHER=1\n", encoding="utf-8")
    assert smoke.main(["--env-file", str(env), "--claude", _claude()]) == 2

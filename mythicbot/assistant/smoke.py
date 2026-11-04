"""Owner-run live check of the real `claude` CLI with the assistant's exact lockdown flags.

    python -m mythicbot.assistant.smoke --env-file PATH_TO_.env [--claude claude] [--model haiku]

Reads only CLAUDE_CODE_OAUTH_TOKEN from the env file. Prints the init tool/MCP list, the result, duration, and cost.
Exit 0 only if the call succeeded with structured output and its init message showed no tools beyond
StructuredOutput and no MCP servers. A run with no init message, or one that times out, is SMOKE FAIL.
A second call uses the real system prompt and intent schema with a two-turn JSON history and passes only if the
model resolves the follow-up to `snipe 215147 5000`.
"""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from dotenv import dotenv_values

from mythicbot.assistant import claude_runner as cr
from mythicbot.assistant import intent
from mythicbot.market.snipelist import parse_gold

SCHEMA = {"type": "object", "properties": {"kind": {"type": "string", "enum": ["answer"]}, "text": {"type": "string"}},
          "required": ["kind", "text"], "additionalProperties": False}
CONTEXT_STDIN = json.dumps({"history": [{"role": "user", "text": "snipe item 215147"},
                                        {"role": "assistant", "text": "What max price, in gold?"}],
                            "message": "5000"})


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
    try:
        return _run(a, token, root)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _json_lines(stdout: bytes) -> list:
    lines = []
    for raw in stdout.decode("utf-8", "replace").splitlines():
        try:
            msg = json.loads(raw)
        except ValueError:
            continue
        if isinstance(msg, dict):
            lines.append(msg)
    return lines


def _call(a, token: str, root: Path, *, schema, prompt_file: Path, stdin: str, label: str):
    """One locked-down stream-json call. (lockdown_ok, structured_output), or None on timeout."""
    argv_ = cr.build_argv(cr.parse_command(a.claude), schema=schema, model=a.model, system_prompt_file=prompt_file)
    i = argv_.index("--output-format")
    argv_[i + 1] = "stream-json"
    argv_.insert(i + 2, "--verbose")
    start = time.monotonic()
    try:
        proc = subprocess.run(argv_, input=stdin.encode("utf-8"), capture_output=True, cwd=root,
                              env=cr.minimal_env(config_dir=root / "config", token=token), timeout=120)
    except subprocess.TimeoutExpired:
        return None
    took = time.monotonic() - start
    lines = _json_lines(proc.stdout)
    init = next((m for m in lines if m.get("type") == "system" and m.get("subtype") == "init"), {})
    result = next((m for m in reversed(lines) if m.get("type") == "result"), {})
    tools, mcp = init.get("tools"), init.get("mcp_servers")
    print(f"[{label}] exit={proc.returncode} seconds={took:.1f}")
    print(f"[{label}] init.tools={tools} init.mcp_servers={mcp} init.model={init.get('model')}")
    print(f"[{label}] result.subtype={result.get('subtype')} structured_output={result.get('structured_output')}")
    print(f"[{label}] total_cost_usd={result.get('total_cost_usd')} num_turns={result.get('num_turns')}")
    if proc.returncode != 0:
        print(f"[{label}] stderr:", proc.stderr.decode("utf-8", "replace")[-2000:])
    if not init:
        print(f"[{label}] no system/init message: cannot verify the tool and MCP lockdown")
    ok = (result.get("subtype") == "success" and isinstance(result.get("structured_output"), dict)
          and bool(init) and isinstance(tools, list) and set(tools) <= {"StructuredOutput"} and not mcp)
    return ok, result.get("structured_output")


def _is_followup_snipe(out) -> bool:
    """True only for args `!snipe` itself would run as item 215147 at 5000g (it reads args[0], then parse_gold(args[1]))."""
    if not isinstance(out, dict) or out.get("kind") != "command" or out.get("command") != "snipe":
        return False
    args = [str(x) for x in out.get("args") or []]
    return len(args) >= 2 and args[0] == "215147" and parse_gold(args[1]) == 5000 * 10000


def _run(a, token: str, root: Path) -> int:
    (root / "prompt.md").write_text("Answer briefly. Return kind=answer.", encoding="utf-8")
    base = _call(a, token, root, schema=SCHEMA, prompt_file=root / "prompt.md", stdin="Say pong.", label="lockdown")
    if base is None:
        print("SMOKE FAIL (timeout)")
        return 1
    (root / "system_prompt.md").write_text(intent.render_system_prompt(intent.TEMPLATE_PATH), encoding="utf-8")
    followup = _call(a, token, root, schema=intent.SCHEMA, prompt_file=root / "system_prompt.md",
                     stdin=CONTEXT_STDIN, label="context")
    if followup is None:
        print("SMOKE FAIL (timeout)")
        return 1
    resolved = _is_followup_snipe(followup[1])
    print(f"[context] follow-up resolved to snipe 215147 5000: {resolved}")
    ok = base[0] and followup[0] and resolved
    print("SMOKE PASS" if ok else "SMOKE FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

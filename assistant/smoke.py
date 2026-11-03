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

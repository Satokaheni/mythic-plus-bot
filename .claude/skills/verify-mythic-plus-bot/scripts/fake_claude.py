#!/usr/bin/env python
"""Stand-in for `claude -p` in the offline harness. Reads the prompt on stdin, records the call, prints a canned result."""

import json
import os
import sys
import time


def _route(low: str, prompt: str) -> dict:
    if "fake:long" in low:
        return {"kind": "answer", "text": ("long answer " * 700)[:7000]}
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

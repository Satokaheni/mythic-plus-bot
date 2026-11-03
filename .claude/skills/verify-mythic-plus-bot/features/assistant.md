# Natural-language assistant

An allowlisted user (the harness allowlists `h.banker` and `h.coordinator`) can DM the bot, or @mention it in a channel, in plain language. A headless `claude -p` call returns an answer, which the bot posts, or a command, which the bot validates and runs: reads run directly, writes post ``I'll run `...` — confirm?`` with Confirm/Cancel buttons, and `key` opens the key-request DM flow. The harness replaces the real CLI with `scripts/fake_claude.py`; nothing calls Anthropic.

## Sub-features

- `assistant-answer` a 1:1 DM or a guild @mention gets `fake answer: <prompt>`; long answers are capped at 3 messages ending in `…`.
- `assistant-silent` guild text or a group DM without a mention, `@everyone`, non-allowlisted authors, and typed `!commands` get no assistant reply and no Claude call. A group DM or guild message that @mentions the bot is handled; the harness has no group-DM entry point, so that rule is pinned by `tests/test_trigger.py`.
- `assistant-empty` a bare @mention replies `What can I help with?`.
- `assistant-read` a read command (`show my snipes` -> `!snipes`) runs with no confirm prompt.
- `assistant-write` a write command (`please snipe ...`, `unsnipe ...`) posts the confirm prompt; Confirm runs the real command, Cancel changes nothing.
- `assistant-gate` permissions follow the Discord author, checked before any confirm prompt: a non-banker asking for `!watch` gets ``You don't have access to `!watch` here.`` straight away, with no Confirm prompt.
- `assistant-flow` `make a run ...` opens the key-request DM flow.
- `assistant-failure` a forbidden command (`evil`), unsafe arg (`inject`), bad JSON, error result, crash, or timeout each get `I couldn't work that out`.
- `assistant-busy` a message during an in-flight call gets `One moment — still working on the last one.`; every call is counted in `assistant.json`.

## How to get to it (user POV)

- DM the bot in plain language (allowlisted user only).
- @mention the bot in `#keys` or any channel it reads.
- Press Confirm or Cancel on the bot's ``I'll run `...` `` message.

## Driving it with the harness

Preconditions:

- Baseline boot. The harness sets `ASSISTANT_ENABLED=1`, allowlists `h.banker` and `h.coordinator`, points `CLAUDE_BIN` at `fake_claude.py`, and sets a 3 s timeout. Each fake call is logged to `h.assistant_dir / "work" / "fake_claude_calls.jsonl"` (argv, env keys, prompt). The work and config dirs are under `<system temp>/mpb-verify/<run-id>/`, outside the repo, because the assistant disables itself when either is inside a git work tree.
- Fake routes are keyed on prompt text: `unsnipe`, `my snipes`, `snipe`, `watch`, `make a run`, `evil` (forbidden `cleanup`), `inject` (`@everyone` arg); anything else answers. Modes: `fake:long`, `fake:badjson`, `fake:error`, `fake:crash`, `fake:timeout` (sleeps; the bot kills it at the timeout).

- **Answer in DM.** Run `await h.say(h.banker, "what is the best mage spec?", "dm")` then `await h.wait_for(h.banker, "fake answer: what is the best mage spec?")`.
- **Mention in #keys.** Run `await h.say(h.banker, f"{h.mention()} how do keys work?", "key")` then `await h.wait_for("key", "fake answer: how do keys work?")`.
- **Confirmed write.** Run `await h.say(h.banker, "please snipe 215147 for me under 5000", "dm")`, `ask = await h.wait_for(h.banker, "I'll run `!snipe 215147 5000 Vibrant Shard`")`, `await h.click(h.banker, ask, "Confirm")`, then `await h.wait_for(h.banker, "Sniping")`. `h.state_file("snipes.json")` contains the owner's id.
- **Cancelled write.** Same with `unsnipe 215147` and `"Cancel"`; wait for `Cancelled`; `snipes.json` is unchanged.
- **Access refused.** Run `await h.say(h.coordinator, "watch item 241326", "dm")`, then wait for `don't have access to`. No `I'll run` prompt is posted (the access reply is the only bot act besides typing) and `watches.json` is unchanged.
- **Failures.** Run `await h.say(h.coordinator, "evil", "dm")` (also `inject`, `fake:badjson`, `fake:error`, `fake:crash`, `fake:timeout`) then `await h.wait_for(h.coordinator, "couldn't work that out", timeout=15)`.
- **Proof.** Run `python $H run assistant`. `summary.json` shows every check passing. The journey also asserts that no recorded fake-claude env held a bot secret, that every call used `--strict-mcp-config` and `--tools ""`, and that the call count in `assistant.json` matches.

## Gotchas

- This proves the bot-side wiring only. The real CLI (flags, `StructuredOutput`-only tool list, latency) is checked by the owner with `python -m assistant.smoke --env-file .env`, not by the harness.
- `h.say` returns once the bot is quiet, so a `fake:timeout` call is still in flight afterwards; wait for its failure reply with a long `timeout`.
- The busy reply only appears while a call is in flight; send the second message right after the `fake:timeout` one.
- A confirmed banker command in a guild channel deletes the user's plain-language message (DMs are untouched).
- Typed `!commands` never reach Claude, so they cannot exercise the assistant; use plain text.
- The `key` flow bypasses `!key`'s `#keys`-only gate by design (it runs in DMs).

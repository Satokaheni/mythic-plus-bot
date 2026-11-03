# Natural-language assistant via headless Claude Code — design

Date: 2026-11-03 · Builds on: `refactor/modularize-bot` (after the roster fixes) · Status: owner-reviewed design, pending spec review
Research draft (facts, citations, rejected options): kept by the lead; the cited facts that matter are restated here.

## Intent

**What the owner asked for:** talk to the bot in plain language and have it run Claude Code in headless mode (`claude -p`). Claude decides whether the message asks the bot to do something (set up a snipe, a token alert, look up runs, start a key request) or is just a question — including general questions that have nothing to do with the bot — and the bot acts or answers. Later: pre-filled key requests, then conversation context across messages.

**Who uses it:** only the owner. The owner's Claude subscription pays for it, so nobody else may trigger it.

**Success criteria**
1. A message that addresses the bot (1:1 DM, or an @mention elsewhere) from an allowlisted user gets an answer or a correctly chosen bot action; every other message behaves exactly as today.
2. Claude's output never grants a permission: every action runs through the existing command, under the real author, with the existing gates.
3. Writes and public posts need an explicit Confirm click; reads and answers do not.
4. A Claude failure (timeout, crash, bad output) produces a one-line reply and a log warning, never an exception or a hang.
5. The `claude` subprocess cannot see the bot's secrets, the repo, or the owner's personal Claude Code config, and has no tools.
6. Everything except the live model call is verifiable offline in the harness with a fake `claude`.

## Non-goals (v1)

Conversation context/sessions (next spec); pre-filled key-request form (v2); Claude reading bot state or calling tools/MCP; other users; slash commands.

## Owner decisions (2026-11-03)

- Runtime: headless `claude -p` (not a direct API call).
- Auth: the owner's subscription (`CLAUDE_CODE_OAUTH_TOKEN` from `claude setup-token`). Consequence: `--bare` can't be used (it only reads `ANTHROPIC_API_KEY`); isolation comes from flags + a private config dir + an empty cwd.
- Allowlist: `ASSISTANT_USER_IDS` (the owner). Anyone else: silence.
- Confirmation: writes and public posts confirm; reads and answers run immediately.
- Scope v1: answers (bot-related and general) + all mapped commands; `!key` opens the existing DM form. Pre-fill is v2.
- General questions: Claude answers any question directly; it picks a command only when the message clearly asks the bot to do something.

Lead defaults the owner can change at spec review: model `haiku` via `ASSISTANT_MODEL`; a reply to a bot message with ping counts as a mention (verify live); mentioning the bot's managed role does not trigger; `@bot !keys` is ignored on this path (the `!` path stays disjoint); daily call cap 100 and per-call budget $0.05 (subscription calls report `total_cost_usd` as an estimate — the cap guards runaway loops, not billing).

## Trigger (new `Assistant` cog, `cogs/assistant.py`)

`@commands.Cog.listener("on_message")` runs alongside `commands.Bot`'s default `process_commands`. A pure function decides:

```
should_handle(message, bot_user_id, allowed_ids) -> Optional[str]   # cleaned prompt or None
```

In order: ignore `author.bot`, `webhook_id`, and non-default/reply message types; ignore if `ASSISTANT_ENABLED` is off or the author is not in `allowed_ids`; ignore if the text starts with `!` before or after stripping a leading bot mention; if `message.channel.type is ChannelType.private` → handle; otherwise handle only if `bot_user_id in message.raw_mentions` (never `mentioned_in()`, which is true for @everyone; `mention_everyone` and role mentions never trigger). Group DMs (`ChannelType.group`) need a mention — defensive, since bots effectively can't join group DMs. Strip every `<@id>` / `<@!id>` for the bot; an empty remainder gets "What can I help with?" without calling Claude. Threads under #keys count as guild channels (mention required) and commands gated by `in_channels(KEY_CHANNEL_ID)` will refuse there — the reply says so.

## The Claude call (`assistant/claude_runner.py`)

One `asyncio.create_subprocess_exec` per message, no shell; the prompt goes on **stdin**, never argv.

Flags: `-p --output-format json --json-schema <intent schema> --tools "" --strict-mcp-config --max-turns 3 --max-budget-usd 0.05 --model $ASSISTANT_MODEL --no-session-persistence --permission-mode dontAsk --system-prompt-file <bot-owned prompt>` (exact tool-disable form — `--tools ""` vs `--disallowedTools` — settled by the smoke test, since `--json-schema` works through an injected `StructuredOutput` tool).

Isolation: `cwd` = an empty dedicated dir (`ASSISTANT_WORK_DIR`, default `~/.mpb-assistant/work`); `CLAUDE_CONFIG_DIR` = a private dir (`~/.mpb-assistant/config`) so the owner's `~/.claude` (CLAUDE.md, hooks, MCP servers, plugins) never loads; **explicit minimal `env`**: `PATH`, `HOME`, `CLAUDE_CONFIG_DIR`, `CLAUDE_CODE_OAUTH_TOKEN`, `DISABLE_AUTOUPDATER=1` — `load_dotenv` has put `CLIENT_KEY` and every API key into `os.environ`, and a default subprocess would inherit them all.

Execution: `asyncio.wait_for(proc.communicate(prompt), ASSISTANT_TIMEOUT)` (default 60 s; on timeout `kill()` + `wait()`); a global `asyncio.Semaphore(1)` (a second message while one runs gets "One moment — still working on the last one."); `async with channel.typing()` while waiting.

Result handling: parse stdout JSON; success requires `type == "result"`, `subtype == "success"`, `is_error` false, and a `structured_output` that re-validates against the schema in Python. Anything else is a failure.

## Intent schema and system prompt (`assistant/intent.py`, `assistant/system_prompt.md`)

```
{"kind": "answer",  "text": str}
{"kind": "command", "command": <enum of allowed names>, "args": [str, ...], "summary": str}
```

The system prompt (bot-owned file, loaded at startup) says: you are a helpful general assistant for a WoW Mythic+ guild bot; answer any question directly with `kind: answer` — WoW, theorycrafting, or anything unrelated; choose `kind: command` only when the message clearly asks the bot to do something, using exactly one command from the catalog; never claim to have done something. The catalog is generated from the cogs' registered commands plus the `!help` text, so it can't drift from the code.

Python re-validation (Claude's output is untrusted): `command` must be in `READ_COMMANDS = {keys, snipes, watches, token, help}`, `WRITE_COMMANDS = {snipe, snipepet, unsnipe, watch, unwatch, tokenalert}`, or `FLOW_COMMANDS = {key}`; each arg is a single token or a label: no newlines, no leading `!`, no `<@`, `@everyone`, `@here`, max 100 chars, max 20 args. The bot builds the command string itself: `"!" + command + " " + " ".join(args)`.

## Acting

- **answer:** reply with `allowed_mentions=AllowedMentions.none()`, split into ≤2000-char messages.
- **read command:** run immediately through the real pipeline: `msg = copy.copy(message); msg.content = built`; `ctx = await bot.get_context(msg)`; if `not await ctx.command.can_run(ctx)` (catching `CheckFailure`) → "You don't have access to `!x`."; else `await bot.invoke(ctx)`. The command's own replies, DMs, deletions, and state saves happen exactly as if typed.
- **write command:** post "I'll run `!snipe 215147 5000 Vibrant Shard` — Confirm / Cancel" (a `View`, 60 s timeout, `interaction_check` = original author only); Confirm → same pipeline; Cancel/timeout → edit to "Cancelled." with buttons removed.
- **flow command (`!key`):** run through the pipeline; the existing DM form opens. In a DM channel `in_channels(KEY)` refuses — the assistant invokes the flow directly via the facade `bot._do_key_request_flow(author)` instead, with the same registration and conflict checks the flow already has.
- Banker commands delete their triggering message in guild channels; on this path that deletes the natural-language message — accepted and documented.

## Failures and logging

Timeout, non-zero exit, unparseable stdout, error subtype, missing/invalid `structured_output`, daily cap reached → one-line reply ("I couldn't work that out — try `!help`." / "Daily assistant limit reached.") + `logger.warning`. INFO log per call: user id, channel kind, intent kind/command, duration, `total_cost_usd`, subtype. Prompt and output text only at DEBUG, truncated. Never log env or the token. Daily counter persisted in a new `assistant.json` (date, count; gitignored like the other state files, atomic write like `watches.json`) — `state.json` untouched.

## Config (`config.py`, `.env`)

`ASSISTANT_ENABLED` (default off), `ASSISTANT_USER_IDS` (comma-separated), `CLAUDE_BIN` (default `claude`), `ASSISTANT_MODEL` (default `haiku`), `ASSISTANT_TIMEOUT` (60), `ASSISTANT_DAILY_CAP` (100), `ASSISTANT_WORK_DIR`, `ASSISTANT_CONFIG_DIR`, `CLAUDE_CODE_OAUTH_TOKEN`. The cog loads always; with `ASSISTANT_ENABLED` off its listener returns immediately.

## Verification

- **Smoke test first (manual, on the Pi, before the build plan's code tasks):** install the native `claude` (ARM64), run one real call with the exact flags and a tiny schema; record latency, RSS, `total_cost_usd`, the `system/init` tool list (must be empty or only `StructuredOutput`), and that no CLAUDE.md/hooks/MCP load. Adjust the tool-disable flag form if needed.
- **Unit tests:** `should_handle` truth table (DM/guild/group, mention forms, @everyone, role mention, `!` prefix before/after mention, bots, webhooks, non-allowlisted); intent re-validation (each reject rule); command building; result parsing for each failure shape.
- **Harness:** a fake `claude` script (`CLAUDE_BIN`) that reads stdin and returns canned results by keyword, with timeout / error-subtype / bad-JSON modes; harness `FAKE_ENV` sets `ASSISTANT_ENABLED=1`, `ASSISTANT_USER_IDS`, and `CLAUDE_BIN`, and `doctor` asserts the bin is the fake. `FakeMessage` gains `raw_mentions`, `mentions`, `mention_everyone`, `role_mentions`, `type`, `webhook_id` parsed from `h.say` content. Journey `assistant`: DM question answered; general question answered; #keys plain text ignored; #keys @mention answered; @everyone ignored; non-allowlisted ignored; `!snipe …` typed directly → exactly one reply; "@bot snipe 215147 under 5000g" → Confirm → `snipes.json` updated; Cancel → unchanged; read command runs without confirm; allowlisted non-banker asking for `!watch` → access reply and `watches.json` unchanged; "make a run Thursday" in DM → key form DM opens; timeout → one apology, no ERROR log.
- **Deploy:** Dockerfile installs `claude` (pinned, `DISABLE_AUTOUPDATER=1`) and copies `assistant/`; CLAUDE.md, README, `!help` embed, CHANGELOG updated; `BOT_VERSION` bump.

## Risks

Prompt injection steering Claude into a *permitted* but unwanted action (mitigated by confirm on writes); Pi resources (Claude Code wants ≥4 GB RAM; concurrency 1); Claude Code flag drift across releases (pin version; doctor/startup check); command-grammar coupling (journeys cover each mapped command); subscription-usage limits throttling the bot (daily cap; failures degrade to a reply).

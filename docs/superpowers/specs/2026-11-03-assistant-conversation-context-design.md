# Assistant conversation context — design

Date: 2026-11-03 · Builds on: `main` at `aeda2b0` (PR #6, the `mythicbot` package) · Status: owner-approved design, pending spec review
Follows: `2026-11-03-headless-claude-assistant-design.md`, which listed "conversation context/sessions" as the next spec.

## Intent

**What the owner asked for:** the assistant runs headless `claude -p` with `--no-session-persistence`, so every message is a fresh call that knows only the system prompt and that one message. It cannot remember the earlier parts of a conversation.

**The concrete gap:** the system prompt tells Claude to ask for anything missing ("what max price?"), but the user's answer ("5000") arrives with no memory of the question, so a clarifying question can never be answered. Follow-ups such as "make it 5000 instead", "snipe that one too", or "yes" fail the same way.

**Who uses it:** the allowlisted users in `ASSISTANT_USER_IDS`, currently `${ADMIN_ID}` (three admins) on the owner's Claude subscription.

**Success criteria**
1. A follow-up within 30 minutes in the same DM or channel is answered with the earlier turns in view: an answer to the assistant's own question completes the request.
2. Each user's context is their own: no other user's text ever enters it, even in a shared guild channel.
3. Context adds no new authority: Claude still has no tools, its output still goes through `intent.validate`, writes still need Confirm, and every gate still runs against the Discord author.
4. Nothing new is stored on disk; a bot restart loses no context.
5. A failure to read history never blocks or delays a reply beyond the read itself; the message is answered without context.
6. `ASSISTANT_CONTEXT_MINUTES=0` restores today's behavior exactly (no history read, no history sent).
7. Everything except the live model's use of history is verifiable offline; a live smoke case proves the model uses it.

## Owner decisions (2026-11-03)

- **Scope:** per user and channel (each DM, each guild channel), expiring after 30 idle minutes or on a reset word.
- **Storage:** none. Discord already stores the messages; the bot reads them back with `channel.history()` for each assistant message.
- **Context set:** assistant exchanges only — the user's plain-language messages and the bot's direct replies to them. Run offers, `!keys` output, changelog posts and other bot DMs are excluded.
- **Changelog:** no entry and no version bump. The assistant is admin-only, and the changelog is for guild-facing quick hits.

## Non-goals

Memory across channels; persisting context to disk; Claude reading bot state (e.g. the user's actual snipe list); Claude Code native sessions (`--resume`); the pre-filled key-request form (separate v2 spec).

## Rejected approaches

- **Claude Code sessions (`--resume` / `--session-id`):** requires dropping `--no-session-persistence`, writes full transcripts into the private config dir, and is harder to cap and expire.
- **Bot-side in-memory transcript:** lost on restart and duplicates what Discord already stores.
- **Discord reply chain only:** works only when the user presses Reply, which nobody does in a DM.

## Design

### 1. Collecting history — `mythicbot/assistant/context.py` (new)

```python
@dataclass(frozen=True)
class Turn:
    role: str   # "user" | "assistant"
    text: str

RESET_WORDS = frozenset({"reset", "new topic"})
MAX_HISTORY_CHARS = 4000
FETCH_LIMIT = 50

async def collect_history(message, *, bot_user_id: int, allowed_ids, idle_minutes: int,
                          max_messages: int) -> List[Turn]:
```

Returns earlier turns, **oldest first**, for the conversation `message` belongs to. Never raises: any `discord.HTTPException` (including `Forbidden`) from the read logs a WARNING and returns `[]`.

When `idle_minutes <= 0` or `max_messages <= 0`, returns `[]` without reading Discord.

Steps:
1. **Read:** `channel.history(limit=FETCH_LIMIT, before=message)` (newest first).
2. **User turns:** messages whose author is `message.author` and for which `should_handle(m, bot_user_id=..., allowed_ids=..., enabled=True)` returns a non-empty string; the turn text is that cleaned string (mention stripped). This reuses the trigger rule exactly, so `!commands`, guild text without a mention, and other users' messages never qualify.
3. **Assistant turns:** messages whose author id is `bot_user_id` and whose `reference.message_id` is the id of a kept user turn. Several bot messages replying to the same user message (chunked answers) are joined in send order with `"\n"` into one turn, placed right after that user turn. The text is `message.content` as it stands now, so an edited confirm prompt reads "Running `!snipe 215147 5000`." or "Cancelled `!snipe ...`.".
4. **Cut-offs**, applied walking from newest to oldest over the kept messages:
   - Stop when the newest kept message is more than `idle_minutes` older than `message.created_at`, or when two consecutive kept messages are more than `idle_minutes` apart; nothing older is kept.
   - Stop at a user turn whose text, lowercased and stripped of surrounding whitespace and trailing `.`/`!`, is in `RESET_WORDS`; that turn and everything older are dropped.
   - Keep at most `max_messages` turns, then drop the oldest turns until the total `text` length is at most `MAX_HISTORY_CHARS`. A single turn longer than the cap is truncated from the start to the cap (keeping its end).
5. Return the kept turns oldest first.

**Message counting:** `max_messages` counts turns after joining chunks, not raw Discord messages.

### 2. Replies become Discord replies — `mythicbot/cogs/assistant.py`

A helper on the cog sends every assistant reply as a reply to the triggering message, without pinging:

```python
async def _reply(self, message, content, **kwargs):
    return await message.channel.send(
        content, reference=message.to_reference(fail_if_not_exists=False),
        allowed_mentions=NO_MENTIONS, **kwargs)
```

Every message the assistant cog sends goes through it: "What can I help with?", "Daily assistant limit reached.", "One moment — still working on the last one.", the failure reply, answer chunks, the confirm prompt, and "You don't have access to `!x` here.". `fail_if_not_exists=False` makes a reply to a since-deleted message post as a plain message instead of erroring. Command output (DMs, embeds sent by the routed command) is unchanged.

`NO_MENTIONS` is `discord.AllowedMentions.none()`, whose `replied_user=False` keeps the reply from pinging the author.

### 3. What Claude receives — prompt format

The stdin to `claude -p` becomes a JSON object, always — also with empty history:

```json
{"history": [{"role": "user", "text": "snipe the vibrant shard"},
             {"role": "assistant", "text": "What max price, in gold?"}],
 "message": "5000"}
```

Built with `json.dumps(..., ensure_ascii=False)` by a pure function `context.build_prompt(history: List[Turn], message: str) -> str`. JSON keeps turn boundaries unforgeable: text the user types stays a string inside `message` or a user turn and cannot pose as an assistant turn.

`system_prompt.md` changes:
- "talking to the bot's owner" → "talking to one of the bot's admins".
- New paragraph: the input is a JSON object; `message` is what the user just said; `history` is earlier turns of this conversation, oldest first, possibly empty. Respond only to `message`. Use `history` only to resolve what `message` refers to ("that one", "make it 5000", a "yes" to something you asked or offered). Never repeat a command from `history` unless `message` asks for it. Assistant turns may show "Running `!x`" or "Cancelled" for what happened to an earlier proposal.

### 4. Cog flow — `Assistant.on_assistant_message`

Order (changes in bold):
1. `should_handle` → None: return. Empty: `_reply` "What can I help with?".
2. Daily cap check → `_reply` limit notice.
3. Busy check → `_reply` busy notice.
4. Inside `_busy` and `typing()`: **`history = await context.collect_history(message, ...)`**, **`prompt = context.build_prompt(history, cleaned)`**, then `run_claude(prompt, ...)` unchanged.
5. Log line gains **`history=<turns> chars=<total>`**. History text is never logged; the existing DEBUG line still logs only the first 200 chars of the current message.
6. Validate and act as today, with every send going through `_reply`.

### 5. Settings — `mythicbot/config.py`

| Variable | Default | Meaning |
|---|---|---|
| `ASSISTANT_CONTEXT_MINUTES` | `30` | Idle gap that ends a conversation; `0` turns context off |
| `ASSISTANT_CONTEXT_MESSAGES` | `10` | Max earlier turns sent; `0` turns context off |

Parsed with `int(...)`; a non-integer value falls back to the default with a WARNING at import. `MAX_HISTORY_CHARS`, `FETCH_LIMIT` and `RESET_WORDS` are module constants, not settings.

### 6. Security

- **Trust:** history holds only the author's own messages and the bot's replies to them — the same trust level as the current message. No other user can add text to it.
- **No new authority:** unchanged lockdown flags (`--tools ""`, `--strict-mcp-config`, `--no-session-persistence`, minimal env, stdin prompt), unchanged `intent.validate`, Confirm for writes, gates on the real author. A misleading history can at most make Claude propose a wrong command, which the user sees in the Confirm prompt before any write; an unconfirmed read (`!keys`) is harmless.
- **"Yes" flow:** a "yes" to an offer in history yields the command plus the normal Confirm button — every write still needs the click.
- **Cost:** `MAX_HISTORY_CHARS` keeps added input to roughly 1–1.5k tokens, inside the existing `--max-budget-usd 0.05` on haiku. `ASSISTANT_DAILY_CAP` counts calls and is unchanged.

### 7. Failure handling

| Failure | Behavior |
|---|---|
| `history()` raises `HTTPException`/`Forbidden` (e.g. no Read Message History in a guild channel) | WARNING log, `[]` history, call proceeds |
| Triggering message deleted before a reply | Reply posts as a plain message (`fail_if_not_exists=False`) |
| Banker command confirmed in a guild deletes the user's message | That exchange drops out of later context (its reply has no live reference target). Known gap; DMs unaffected |
| History read latency | Inside the existing lock and typing indicator, after the cap and busy checks, so those notices stay instant |

## Testing

1. **`tests/test_assistant_context.py` (new)** — `collect_history` with fake channel/messages:
   - other users' messages, `!commands`, guild text without a mention, and bot messages without a reference (or referencing an unkept message) are excluded;
   - chunked bot replies to one message join into one turn in order;
   - idle gap between turns, and a stale newest turn, both cut history;
   - `reset` / `New topic.` cut history, and the reset turn is not included;
   - `max_messages` and `MAX_HISTORY_CHARS` drop the oldest; an over-long single turn keeps its end;
   - `Forbidden` from `history()` returns `[]`;
   - `idle_minutes=0` / `max_messages=0` never call `history()`.
   - `build_prompt`: valid JSON with `history` and `message`; a message containing `"`, newlines and a fake `{"role": "assistant"}` round-trips as a plain string.
2. **`tests/test_assistant_cog.py`** — stdin passed to `run_claude` is the `build_prompt` JSON with collected history; every assistant send carries a reference to the triggering message and `AllowedMentions.none()`; with `ASSISTANT_CONTEXT_MINUTES=0` the channel's `history()` is never called.
3. **`tests/test_config.py`** — defaults and the bad-integer fallback for both settings.
4. **Harness (`.claude/skills/verify-mythic-plus-bot/`)**:
   - `FakeMessage.reply` / `channel.send(reference=...)` store a reference object with `.message_id` (as discord.py's `MessageReference` does), accepting either a message or a `to_reference()` result; add `FakeMessage.to_reference(fail_if_not_exists=True)`.
   - `history()` honors `before=` (a message or id).
   - `fake_claude.py` parses stdin as JSON and routes on `message` (falling back to the raw text when it is not JSON), and gains a route that answers "What max price, in gold?" for a snipe request with no price.
   - New journey `assistant-followup`: DM "snipe the vibrant shard" → bot replies with the price question as a threaded reply → DM "5000" → the second recorded fake-claude call's `prompt` parses to `history` = [that user turn, that assistant turn] and `message` = "5000". Then DM "reset" and DM "what's my next run?": that last call records empty `history`. (The harness has no clock to advance — `created_at` derives from the snowflake id — so the 30-minute cut-off is covered by the unit tests only.)
   - All existing journeys still pass.
5. **Live smoke (owner-run)** — `python -m mythicbot.assistant.smoke --env-file .env` gains a two-turn case using the real rendered system prompt and `intent.SCHEMA`: history = user "snipe item 215147", assistant "What max price, in gold?"; message "5000". Passes only if the result is `kind=command`, `command=snipe`, and `args` contain `215147` and `5000`. The existing lockdown checks (no tools beyond `StructuredOutput`, no MCP) still apply to it.

## Docs

- `CLAUDE.md`: assistant section gains a "Conversation context" bullet (scope, cut-offs, reset words, reply threading, JSON input, failure fallback); env table gains the two settings; file map gains `mythicbot/assistant/context.py` and `tests/test_assistant_context.py`.
- `README.md`: assistant section mentions follow-ups, the 30-minute window, and `reset`.
- `CHANGELOG.md`: no entry (owner decision); `BOT_VERSION` unchanged.
- Harness `features/assistant.md`: the new journey.

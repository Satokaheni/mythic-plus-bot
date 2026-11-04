"""Conversation context for the assistant, read back from Discord on each message (the bot stores nothing).

Kept: the author's own assistant messages (as judged by `clean`, i.e. the cog's trigger rule) and the bot's
Discord replies to them. Cut at an idle gap, at a reset word, at `max_messages` turns and `MAX_HISTORY_CHARS`.
"""

import json
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Callable, List, Optional

logger = logging.getLogger("discord")

RESET_WORDS = frozenset({"reset", "new topic"})
MAX_HISTORY_CHARS = 4000
FETCH_LIMIT = 50


@dataclass(frozen=True)
class Turn:
    role: str  # "user" | "assistant"
    text: str


def is_reset(text: str) -> bool:
    return text.strip().rstrip(".!").strip().lower() in RESET_WORDS


def select_turns(current, earlier, *, bot_user_id: int, clean: Callable[[object], Optional[str]],
                 idle_minutes: int, max_messages: int) -> List[Turn]:
    """Turns oldest first. `earlier` is the messages before `current`, newest first."""
    author_id = current.author.id
    users = {}
    for m in earlier:
        if m.author.id == author_id:
            text = clean(m)
            if text:
                users[m.id] = (m, text)
    replies = {}
    for m in earlier:
        ref_id = getattr(getattr(m, "reference", None), "message_id", None)
        if m.author.id == bot_user_id and ref_id in users:
            replies.setdefault(ref_id, []).append(m)

    gap = timedelta(minutes=idle_minutes)
    newest_first: List[Turn] = []
    later = current.created_at
    for msg_id in sorted(users, key=lambda i: (users[i][0].created_at, i), reverse=True):
        message, text = users[msg_id]
        bots = sorted(replies.get(msg_id, []), key=lambda b: (b.created_at, b.id))  # ids break same-instant ties
        newest = bots[-1].created_at if bots else message.created_at
        if later - newest > gap or is_reset(text):
            break
        answer = "\n".join(b.content for b in bots if b.content)
        if answer:
            newest_first.append(Turn("assistant", answer))
        newest_first.append(Turn("user", text))
        later = message.created_at

    kept: List[Turn] = []
    total = 0
    for turn in newest_first[:max_messages]:
        room = MAX_HISTORY_CHARS - total
        if len(turn.text) > room:
            if not kept:
                kept.append(Turn(turn.role, turn.text[-MAX_HISTORY_CHARS:]))
            break
        kept.append(turn)
        total += len(turn.text)
    return list(reversed(kept))


async def collect_history(message, *, bot_user_id: int, clean: Callable[[object], Optional[str]],
                          idle_minutes: int, max_messages: int) -> List[Turn]:
    """The conversation before `message`; [] when context is off or Discord refuses the read. Never raises."""
    if idle_minutes <= 0 or max_messages <= 0:
        return []
    try:
        earlier = [m async for m in message.channel.history(limit=FETCH_LIMIT, before=message)]
    except Exception as exc:  # Forbidden, or a network error from aiohttp: answer without context either way
        logger.warning("assistant: could not read history, answering without context: %s: %s",
                       type(exc).__name__, exc)
        return []
    return select_turns(message, earlier, bot_user_id=bot_user_id, clean=clean, idle_minutes=idle_minutes,
                        max_messages=max_messages)


def build_prompt(history: List[Turn], message: str) -> str:
    return json.dumps({"history": [{"role": t.role, "text": t.text} for t in history], "message": message},
                      ensure_ascii=False)

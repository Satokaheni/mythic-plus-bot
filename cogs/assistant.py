"""Assistant: plain-language messages from allowlisted users, answered or acted on via headless Claude."""

import re
from typing import Optional

import discord

_HANDLED_TYPES = (discord.MessageType.default, discord.MessageType.reply)


def should_handle(message, *, bot_user_id: int, allowed_ids, enabled: bool) -> Optional[str]:
    """None = not for the assistant; "" = addressed with nothing to say; otherwise the prompt without the mention."""
    if not enabled:
        return None
    author = message.author
    if getattr(author, "bot", False) or getattr(message, "webhook_id", None):
        return None
    if getattr(message, "type", discord.MessageType.default) not in _HANDLED_TYPES:
        return None
    if author.id not in allowed_ids:
        return None
    content = message.content or ""
    if content.lstrip().startswith("!"):
        return None
    is_dm = getattr(message.channel, "type", None) is discord.ChannelType.private
    mentioned = bot_user_id in (getattr(message, "raw_mentions", None) or []) or any(
        getattr(u, "id", None) == bot_user_id for u in (getattr(message, "mentions", None) or [])
    )
    if not (is_dm or mentioned):
        return None
    cleaned = re.sub(rf"<@!?{bot_user_id}>", "", content).strip()
    if cleaned.startswith("!"):
        return None
    return cleaned

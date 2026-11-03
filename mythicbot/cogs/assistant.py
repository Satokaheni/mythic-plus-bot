"""Assistant: plain-language messages from allowlisted users, answered or acted on via headless Claude."""

import asyncio
import copy
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import discord
from discord.ext import commands

from mythicbot import config
from mythicbot.assistant import claude_runner, intent
from mythicbot.assistant.usage import DailyUsage

logger = logging.getLogger("discord")
FAILURE = "I couldn't work that out — try `!help`."
NO_MENTIONS = discord.AllowedMentions.none()
MAX_ANSWER_CHUNKS = 3
_REPO_DIR = Path(__file__).resolve().parents[2]

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


def _unsafe_location(path: Path) -> Optional[str]:
    """Claude Code reads CLAUDE.md from parent dirs and settings from the project root: keep it out of any repo."""
    if path == _REPO_DIR or _REPO_DIR in path.parents:
        return "is inside the bot's own directory"
    for parent in (path, *path.parents):
        if (parent / ".git").exists():
            return f"is inside a git work tree ({parent})"
    return None


def _chunks(text: str, size: int = 2000, limit: int = MAX_ANSWER_CHUNKS):
    if len(text) > size * limit:
        text = text[:size * limit - 1] + "…"
    return [text[i:i + size] for i in range(0, len(text), size)] or [""]


class ConfirmView(discord.ui.View):
    def __init__(self, author_id: int, timeout: float = 60) -> None:
        super().__init__(timeout=timeout)
        self.author_id = author_id
        self.confirmed = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.author_id

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        self.confirmed = True
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await interaction.response.defer()
        self.stop()


class Assistant(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot
        self._busy = asyncio.Semaphore(1)
        self.usage = DailyUsage.load()
        # Absolute: claude runs with cwd=work_dir, so a relative path would resolve against the wrong dir.
        self.work_dir = Path(config.ASSISTANT_WORK_DIR).resolve()
        self.config_dir = Path(config.ASSISTANT_CONFIG_DIR).resolve()
        self.system_prompt_file = self.config_dir / "system_prompt.md"
        self.disabled = False  # set by cog_load when the setup is unsafe; never raised, so the bot still starts

    async def cog_load(self) -> None:
        unknown = [name for name in intent.USAGE if self.bot.get_command(name) is None]
        if unknown:
            logger.error("assistant disabled: its catalog names commands the bot doesn't have: %s", unknown)
            self.disabled = True
            return
        if config.ASSISTANT_ENABLED:
            for label, path in (("ASSISTANT_WORK_DIR", self.work_dir), ("ASSISTANT_CONFIG_DIR", self.config_dir)):
                problem = _unsafe_location(path)
                if problem:
                    logger.error("assistant disabled: %s %s %s; point it outside any repo", label, path, problem)
                    self.disabled = True
                    return
            self.system_prompt_file.parent.mkdir(parents=True, exist_ok=True)
            self.system_prompt_file.write_text(intent.render_system_prompt(intent.TEMPLATE_PATH), encoding="utf-8")

    @commands.Cog.listener("on_message")
    async def on_assistant_message(self, message: discord.Message) -> None:
        if self.disabled:
            return
        prompt = should_handle(message, bot_user_id=self.bot.user.id, allowed_ids=config.ASSISTANT_USER_IDS,
                               enabled=config.ASSISTANT_ENABLED)
        if prompt is None:
            return
        if not prompt:
            await message.channel.send("What can I help with?")
            return
        today = datetime.now(config._CST).date().isoformat()
        if not self.usage.allow(today, config.ASSISTANT_DAILY_CAP):
            await message.channel.send("Daily assistant limit reached.")
            return
        if self._busy.locked():
            await message.channel.send("One moment — still working on the last one.")
            return
        async with self._busy:
            async with message.channel.typing():
                result = await claude_runner.run_claude(
                    prompt, cmd=config.CLAUDE_CMD, schema=intent.SCHEMA, model=config.ASSISTANT_MODEL,
                    system_prompt_file=self.system_prompt_file, work_dir=self.work_dir,
                    config_dir=self.config_dir, token=config.CLAUDE_CODE_OAUTH_TOKEN,
                    timeout=config.ASSISTANT_TIMEOUT,
                )
            self.usage.record(today, result.cost_usd)
            self.usage.save()
        logger.info("assistant: user=%s dm=%s ok=%s subtype=%s cost=%.4f seconds=%.1f", message.author.id,
                    message.guild is None, result.ok, result.subtype, result.cost_usd, result.duration_s)
        logger.debug("assistant: prompt=%r", prompt[:200])
        if not result.ok:
            logger.warning("assistant: claude call failed: %s detail=%r", result.error, result.detail)
            await message.channel.send(FAILURE)
            return
        try:
            parsed = intent.validate(result.output)
        except intent.IntentError as exc:
            logger.warning("assistant: rejected intent: %s", exc)
            await message.channel.send(FAILURE)
            return
        logger.info("assistant: intent=%s", f"command:{parsed.command}" if parsed.kind == "command" else "answer")
        await self._act(message, parsed)

    async def _act(self, message: discord.Message, parsed: "intent.Intent") -> None:
        if parsed.kind == "answer":
            for chunk in _chunks(parsed.text):
                await message.channel.send(chunk, allowed_mentions=NO_MENTIONS)
            return
        if parsed.command in intent.FLOW_COMMANDS:
            await self.bot._do_key_request_flow(message.author)
            return
        command = intent.build_command(parsed)
        ctx = await self._permitted_context(message, command)  # gates run before any confirm prompt
        if ctx is None:
            return
        if parsed.needs_confirm:
            view = ConfirmView(message.author.id)
            ask = await message.channel.send(f"I'll run `{command}` — confirm?", view=view, allowed_mentions=NO_MENTIONS)
            await view.wait()
            if not view.confirmed:
                await ask.edit(content=f"Cancelled `{command}`.", view=None)
                return
            await ask.edit(content=f"Running `{command}`.", view=None)
        await self.bot.invoke(ctx)  # re-runs the command's checks at execution time

    async def _permitted_context(self, message: discord.Message, command: str) -> Optional[commands.Context]:
        """The real command's context for the Discord author, or None after replying why it won't run."""
        proxy = copy.copy(message)
        proxy.content = command
        ctx = await self.bot.get_context(proxy)
        if ctx.command is None:
            await message.channel.send(FAILURE)
            return None
        try:
            allowed = await ctx.command.can_run(ctx)
        except commands.CheckFailure:
            allowed = False
        if not allowed:
            await message.channel.send(f"You don't have access to `!{ctx.command.name}` here.")
            return None
        return ctx


async def setup(bot) -> None:
    await bot.add_cog(Assistant(bot))

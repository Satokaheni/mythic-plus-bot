"""Gear: officer-only gear audit command."""

import asyncio
import logging
from typing import Dict, List, Tuple

import aiohttp
import discord
from discord.ext import commands

from mythicbot import config
from mythicbot.cogs._checks import is_elevated
from mythicbot.guild import gearaudit, raiderio

logger = logging.getLogger("discord")


class Gear(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot

    async def _run_gear_audit(
        self, mappings: List[dict]
    ) -> Tuple[List[gearaudit.CharacterFindings], List[Tuple[str, str, str]]]:
        """Fetch and audit every mapped character. Returns (findings, failures).

        One character's failure never ends the sweep — it is collected and reported, since a
        404 usually means a rename or transfer the mapping file hasn't caught up with.
        """
        semaphore = asyncio.Semaphore(config.GEAR_AUDIT_CONCURRENCY)
        findings: List[gearaudit.CharacterFindings] = []
        failures: List[Tuple[str, str, str]] = []

        async with aiohttp.ClientSession() as session:

            async def audit_one(entry: dict) -> None:
                character = entry.get("character") or ""
                realm_slug = entry.get("realm_slug") or ""
                realm_name = entry.get("realm_name") or realm_slug
                if not character or not realm_slug:
                    # Report it rather than dropping it silently — a half-filled mapping entry is
                    # exactly the kind of drift this command is meant to surface.
                    failures.append((character or "(unnamed entry)", realm_name or "?", "incomplete mapping entry"))
                    return
                # The semaphore wraps the whole body, not just the equipment fetch: the per-gem
                # item_info lookups are Blizzard calls too, and bounding only the first request
                # would let ~30 characters' worth of gem lookups fan out at once.
                async with semaphore:
                    try:
                        items = await self.bot.blizzard.character_equipment(session, realm_slug, character)
                    except Exception as exc:  # noqa: BLE001 - one bad character must not end the sweep
                        logger.warning("gearaudit: %s/%s failed: %s", realm_slug, character, exc)
                        failures.append((character, realm_name, "fetch failed"))
                        return
                    if items is None:
                        failures.append((character, realm_name, "not found — renamed, transferred, or a stale mapping"))
                        return
                    gem_quality: Dict[int, str] = {}
                    for gem_id in gearaudit.gem_ids(items):
                        try:
                            gem_quality[gem_id] = (await self.bot.blizzard.item_info(session, gem_id)).quality
                        except Exception as exc:  # noqa: BLE001 - an unknown gem is simply not graded
                            logger.warning("gearaudit: gem %s lookup failed: %s", gem_id, exc)
                    findings.append(gearaudit.audit_character(character, realm_name, items, gem_quality))

            await asyncio.gather(*(audit_one(entry) for entry in mappings), return_exceptions=True)

        return findings, failures

    @commands.command(name="gearaudit")
    @is_elevated()
    async def cmd_gearaudit(self, ctx: commands.Context) -> None:
        message = ctx.message
        if message.guild is not None:
            try:
                await message.delete()
            except (discord.Forbidden, discord.NotFound):
                pass
        wanted = message.content[len("!gearaudit") :].strip()
        mappings = raiderio.load_character_mappings()
        if wanted:
            mappings = [m for m in mappings if (m.get("character") or "").lower() == wanted.lower()]
            if not mappings:
                await message.author.send(f"No mapping for `{wanted}`.")
                return
        if not mappings:
            await message.author.send("No characters are mapped, so there is nothing to audit.")
            return
        try:
            findings, failures = await self._run_gear_audit(mappings)
        except Exception as exc:  # noqa: BLE001 - a failed audit must not kill the command
            logger.warning("gearaudit failed: %s", exc)
            await message.author.send("The gear audit failed. Check the logs.")
            return
        for chunk in gearaudit.format_report(findings, failures):
            await message.author.send(chunk)


async def setup(bot) -> None:
    await bot.add_cog(Gear(bot))

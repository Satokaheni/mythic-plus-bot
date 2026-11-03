# Command help

`!help` (alias `!tools`) posts one embed titled `🤖 Mythic+ Bot — Commands` in whatever channel or DM the user typed it, listing every command by category with its usage and who may run it. Anyone can use it, registered or not.

## Sub-features

- `help-embed` posts the commands embed with its category fields: Mythic+ Scheduling, Availability & Setup, Gear Audit, Price Watch, WoW Token, Auction Sniper, Assistant (only when `ASSISTANT_ENABLED` is on, as in the harness), Help.
- `help-alias` makes `!tools` produce the same embed.
- `help-anywhere` answers in the channel the command came from: a guild channel or a DM.

## How to get to it (user POV)

- Type `!help` in `#keys`, `#availability`, or any channel the bot reads.
- Type `!help` or `!tools` in a DM to the bot.

## Driving it with the harness

Preconditions:

- Baseline boot (see README). No registration needed.

- **Help in #keys.** An unregistered member types `!help` in `#keys`. Run `u = h.user("Curious"); await h.say(u, "!help", "key")` then `msg = await h.wait_for("key", "Mythic+ Bot — Commands")`. `msg.embed.fields` has names containing `Scheduling`, `Availability`, `Gear Audit`, `Price Watch`, `WoW Token`, `Auction Sniper`, `Assistant`, `Help`.
- **Alias in DM.** The same member types `!tools` in a DM. Run `await h.say(u, "!tools", "dm")` then `await h.wait_for(u, "Mythic+ Bot — Commands")`. The embed arrives in that member's DM.
- **Proof.** Run `python $H run help`. `summary.json` shows 9/9 checks passing; `transcript.md` shows both embeds and their audiences.

## Gotchas

- The command must be exactly `!help` or `!tools`. `!help scheduling` or a trailing space is ignored silently.
- The command message is not deleted, unlike `!avail`, `!key`, and banker commands.
- The embed is static text. It can drift from the real handlers (it lists `!watch <id1> <id2> …` without per-item `-x`). Check a command against its `@commands.command` handler in the owning cog (`mythicbot/cogs/`), not against the help text; the help embed itself is `Admin.cmd_help` in `mythicbot/cogs/admin.py`.
- The embed must stay within Discord's limits (25 fields, 1024 per field, 6000 total). The harness rejects an oversized embed with `HTTPException 400`, so a long new help field fails this journey.

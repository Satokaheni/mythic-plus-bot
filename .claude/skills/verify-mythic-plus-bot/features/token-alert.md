# WoW Token sell alert

The banker (`BANKER_ID`) sets a gold threshold with `!tokenalert`, checks the current price and alert state with `!token`, and gets a DM when the WoW Token price rises past the threshold. While the price stays above it, further alerts fire only on new highs at least 10,000g above the last alerted price; a drop back below the threshold re-arms the alert. Every other member is ignored without a reply. State is two fields in `token_watch.json`.

## Sub-features

- `token-set` `!tokenalert <gold>` (commas allowed) sets the threshold, re-arms, and DMs `💰 WoW Token alert set: … **<gold>**`.
- `token-off` `!tokenalert off` clears the threshold and DMs `🔕 WoW Token alerts disabled.`
- `token-validate` `!tokenalert` with no argument DMs usage; a bad amount DMs `isn't a valid gold amount`.
- `token-status` `!token` DMs the current price (or `price unavailable`) and `Threshold: … — armed (waiting to cross)` or `alerted at …`.
- `token-gate` any non-banker using these commands gets no reply and changes nothing.
- `token-alert` `token_watch_check` (every 20 minutes) DMs `💰 **WoW Token — sell signal**` on the first crossing and on each new high of at least 10,000g, and re-arms below the threshold.

## How to get to it (user POV)

- The banker types `!tokenalert …` or `!token` in a DM or in `#keys` (the command is deleted in a guild channel; replies are DMs).
- Alerts arrive by DM with no user action.

## Driving it with the harness

Preconditions:

- Baseline boot; `token_watch.json` absent (alerts disabled).
- Offline by default: the Blizzard token price request is blocked, so `!token` reports `price unavailable`.

- **Set a threshold from #keys.** Run `cmd = await h.say(h.banker, "!tokenalert 300,000", "key")` then `reply = await h.wait_for(h.banker, "WoW Token alert set")`. `cmd.deleted` is true, the DM names `300,000`, and `h.state_file("token_watch.json")["token_watch"]["threshold"] == 3_000_000_000` (copper).
- **Status.** Run `await h.say(h.banker, "!token", "dm")` then `status = await h.wait_for(h.banker, "Threshold")`. The DM contains `armed`.
- **Non-banker is ignored.** Run `before = len(h.world.events); await h.say(h.user("NotTheBanker"), "!tokenalert 5", "key")`. No `send` or `delete` event follows in `h.world.events[before:]`, and `token_watch.json` is unchanged.
- **Bad input.** Run `await h.say(h.banker, "!tokenalert lots", "dm")` then `await h.wait_for(h.banker, "isn't a valid gold amount")`. The threshold is unchanged.
- **Disable.** Run `await h.say(h.banker, "!tokenalert off", "dm")` then `await h.wait_for(h.banker, "alerts disabled")`. `token_watch.json` has `threshold: null`.
- **Alert loop.** Run with `--network` and a threshold below the live price, then `await h.tick("token_watch_check")` and `await h.wait_for(h.banker, "sell signal")`; `last_alert` in `token_watch.json` equals the reported price. Offline, the tick logs a blocked request and sends nothing; say the alert path is unverified.
- **Proof.** Run `python $H run banker-token` (offline: no price).

## Gotchas

- The non-banker gate is silent by design. Prove it by the absence of `send`/`delete` events, not by waiting for a reply.
- A non-banker's `!tokenalert` in `#keys` is not deleted either; it stays in the channel.
- Setting a new threshold, or `off`, always clears `last_alert`, so it re-arms even if the price is already above the new threshold.
- `last_alert` is saved only after the DM is delivered. A banker with closed DMs (`dms_open=False`) gets no alert and the ratchet does not advance, so every tick retries.
- `!tokenalert 0`, negative, and decimal amounts are rejected; `parse_threshold` accepts only positive whole gold.
- There are no quiet hours and no price history; do not look for either.

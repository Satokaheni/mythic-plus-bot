## [1.5.0]

### Improvements
- `!watch` and `!unwatch` now accept multiple item IDs at once (e.g. `!watch 212283 212391 212284`), so the banker can add or remove several items in a single command.

## [1.4.0]

### Improvements
- The bot now automatically predicts the best weekly Mythic+ run from availability and play-history data and DMs a dry-run preview to the banker (no runs are created yet).
- Predicted rosters now prefer people's primary role, only using someone's off-role when a role can't be filled by a main (off-role assignments are flagged in the preview).

### Bug Fixes
- Raider.io run history is now parsed from the response's top-level fields (there is no `result` wrapper), so the daily harvest actually collects runs.
- The availability predictor now uses a fixed low baseline so slots rank by how often people actually play, instead of every slot saturating to 100% confidence.

## [1.3.0]

### Improvements
- The bot now backfills and daily-harvests Mythic+ run history from Raider.io for mapped, registered raiders, seeding the availability dataset with real play-time data.

## [1.2.0]

### Improvements
- The bot now logs availability reactions, DM offer accept/declines, and completed-run rosters to a local event log (events.jsonl), and no longer discards run history — building the dataset for a future automatic scheduler.

## [1.1.0]
### Improvements
- Added an owner-only Undermine Exchange price watch: `!watch`, `!unwatch`, and `!watches` let the banker track region-wide commodities and receive a DM when a price dips into a self-adjusting "pounce" low.

## [1.0.7]
### Improvements
- Coordinator and admins can now delete and modify runs directly from their manage menu, in addition to their existing add/remove/change role options

### Bug Fixes
- Fixed DM outreach and retry messages showing midnight instead of the actual run time

## [1.0.6]
### Improvements
- Availability message now resets automatically every Tuesday at noon CST, no longer requires a manual !avail command each week
- Full run DM now includes the current roster (Tank, Healer, DPS) when their run is full
- Coordinator and admins now share a unified elevated permission set; conflict notifications remain exclusive to the coordinator

### Bug Fixes
- Fixed coordinator being shown the limited organizer manage menu when they created a run themselves — coordinator now always gets the full coordinator manage menu
- Fixed schedule embed layout: "Posted By" field no longer causes an uneven two-column row
- Fixed embed not updating when removing yourself from a full run — notification DMs are now sent after the embed is updated, and HTTP errors during DM delivery no longer block the embed edit

## [1.0.5]
### Improvements
- Schedule embed now shows who posted the run in a "Posted By" field
- State deserialization now reconciles raider `current_runs` against schedule membership to heal any inconsistencies on load

### Bug Fixes
- Fixed schedule not being saved to state.json when the interaction window expired during posting
- Fixed buttons on existing schedules showing "interaction failed" after a bot restart by registering the view immediately on post
- Fixed `current_runs` and `denied_runs` being reset to a list instead of a set during `!cleanup`, causing crashes on the next key request

## [1.0.4]
### Improvements
- Schedule held in DM until organizer clicks Post Schedule, preventing premature publishing
- Confirmation prompt before adding a raider by name: shows class and roles so organizer can verify the match
- Admins (ADMIN_ID) now have the same run management permissions as the coordinator
- Run organizer is prompted to choose their role when creating a run if they have multiple registered roles
- Coordinator/admin can now change a current raider's role slot via a new Change Role button in the manage menu
- Coordinator/admin can specify any role (Tank/Healer/DPS) when manually adding a raider, regardless of what roles the raider has registered

## [1.0.3]
### Improvements
- JSON state storage replaces pickle: forward-compatible serialization with automatic migration from state.pkl on first boot
- Coordinator can add and remove raiders from existing schedules via the Manage button (without the ability to delete the run)
- Changelog messages are pinned automatically so !cleanup never removes them
- Docker support: Dockerfile and .dockerignore added for AWS container deployment

## [1.0.2]
### Improvements
- Manual raider add: organizer can add up to 4 registered raiders by display name after creating a schedule
- Partial name matching with dropdown selection when multiple raiders match the search

## [1.0.1]
### Improvements
- Organizer-only Manage button on schedule posts
- Delete run: removes schedule and DMs all signed-up members
- Modify run: edit key level, date, time, and note via modal
- Members automatically notified of what changed when a run is modified

## [1.0.0]
### Improvements
- Initial public release
- Embed-based schedule posts with Sign Up / Remove buttons
- Automatic DM outreach to available raiders when spots open
- Off-role displacement: main-role players bump secondary fillers (>8h before run)
- Fill queue with confirmation prompt when signing up for a full run
- Hourly cleanup of past schedules and retry of unanswered DMs
- !modify command to update class/roles/timezone
- !cleanup coordinator command to reset weekly state
- Persistent key-request button pinned in scheduling channel

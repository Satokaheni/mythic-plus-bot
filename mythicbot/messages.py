"""Pure message builders and version/changelog file helpers."""

from textwrap import dedent

_VERSION_FILE = "version.txt"


def _read_last_version() -> str:
    try:
        with open(_VERSION_FILE, "r") as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""


def _write_version(version: str) -> None:
    with open(_VERSION_FILE, "w") as f:
        f.write(version)


def _read_changelog(version: str) -> list[str]:
    """Read entries for a given version from CHANGELOG.md, preserving section headers."""
    try:
        with open("CHANGELOG.md", "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        return []
    in_section = False
    entries = []
    for line in lines:
        if line.startswith(f"## [{version}]"):
            in_section = True
            continue
        if in_section:
            if line.startswith("## ["):
                break
            if line.startswith("### "):
                entries.append(f"**{line[4:]}**")
            elif line.startswith("- "):
                entries.append(line[2:])
    return entries


def AVAILABILITY_MESSAGE(x, y, z) -> str:
    return f"""
React to this message to set your availability for this week's mythic plus runs
<t:{int(x)}:F> to <t:{int(y)}:F>

**If this is your first time signing up please check your DMs after reacting**


:green_circle: - Available
:yellow_circle: - Maybe Available
:red_circle: - Not Available

{z}
"""


def _dm_spot_available(name: str, level: str, ts: int, roster: str, role: str, role_type: str = "primary") -> str:
    return dedent(f"""
        Hello {name}, a spot has opened up for (Level {level})
        Run on <t:{ts}:F>.
        Your {role_type} role ({role}) is needed.

        {roster}

        React with :white_check_mark: if you would like to go, or :x: to decline.
        """)


def _dm_reminder(name: str, level: str, ts: int, roster: str, role: str) -> str:
    return dedent(f"""
        🔔 **Reminder** 🔔
        Hello {name}, a spot is still available for (Level {level})
        Run on <t:{ts}:F>.
        Your role ({role}) is needed.

        {roster}

        React with :white_check_mark: if you would like to go, or :x: to decline.
        """)

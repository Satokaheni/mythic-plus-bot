FROM python:3.10-slim

# tzdata is required for zoneinfo (used by the bot for timezone handling)
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies first so this layer is cached on code-only changes
COPY pyproject.toml .
RUN pip install --no-cache-dir "discord.py>=2.0.0" "python-dotenv>=1.0.0"

# Copy source files
COPY bot.py raider.py schedule.py views.py utils.py undermine.py watchlist.py \
     eventlog.py raiderio.py forecast.py blizzard.py snipelist.py tokenwatch.py \
     gearaudit.py config.py messages.py state.py CHANGELOG.md ./
COPY cogs/ ./cogs/
COPY assistant/ ./assistant/

# Run as non-root user
RUN useradd -m botuser && chown -R botuser:botuser /app
USER botuser

# Claude Code CLI for the natural-language assistant (cogs/assistant.py). Native installer from
# https://code.claude.com/docs/en/setup ("Install a specific version"), pinned to the version the
# assistant's smoke check passed on. It installs under ~/.local, so put ~/.local/bin on PATH.
# The assistant is off unless ASSISTANT_ENABLED=1 and CLAUDE_CODE_OAUTH_TOKEN are set at runtime.
RUN curl -fsSL https://claude.ai/install.sh | bash -s 2.1.317
ENV PATH="/home/botuser/.local/bin:${PATH}"
ENV DISABLE_AUTOUPDATER=1

# state.pkl and version.txt are written here at runtime.
# Mount an EFS volume at /app to persist state across container restarts.
# character_mappings.json (like .env) must be provided on the mounted runtime volume,
# not baked into the image.
CMD ["python", "bot.py"]

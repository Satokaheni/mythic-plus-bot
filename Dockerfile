FROM python:3.10-slim

# tzdata is required for zoneinfo (used by the bot for timezone handling)
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies first so this layer is cached on code-only changes
COPY pyproject.toml .
RUN pip install --no-cache-dir "discord.py>=2.0.0" "python-dotenv>=1.0.0"

# Copy source files
COPY bot.py raider.py schedule.py views.py utils.py undermine.py watchlist.py eventlog.py raiderio.py ./

# Run as non-root user
RUN useradd -m botuser && chown -R botuser:botuser /app
USER botuser

# state.pkl and version.txt are written here at runtime.
# Mount an EFS volume at /app to persist state across container restarts.
CMD ["python", "bot.py"]

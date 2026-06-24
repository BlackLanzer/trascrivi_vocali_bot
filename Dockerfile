# Telegram transcription bot.
# Streaming uses Deepgram's "ogg-opus" live encoding, so no ffmpeg/transcoding
# is needed — the image stays minimal.
FROM python:3.12-slim

# Don't write .pyc files; flush logs immediately.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

# Install dependencies first for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code.
COPY src/ ./src/

# Run as a non-root user; create the mount points it owns.
RUN useradd --create-home --uid 1000 botuser \
    && mkdir -p /app/data /app/config \
    && chown -R botuser:botuser /app
USER botuser

CMD ["python", "-m", "trascrivi_bot.main"]

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    STATE_DIR=/app/runs

WORKDIR /app

COPY pyproject.toml README.md ./
COPY automation ./automation
RUN pip install --no-cache-dir .

RUN useradd --create-home --uid 10001 bot && mkdir -p /app/runs && chown -R bot /app
USER bot

EXPOSE 8000

# The CLI is the default; `docker compose up` overrides the command to run the
# webhook receiver from the same image.
ENTRYPOINT ["remediate"]
CMD ["--help"]

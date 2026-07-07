# TokenVeil Community Edition image.
# Light by design: the Community anonymization engine is regex-based, so there
# are no spaCy models to download. The only non-Python dependency is Node.js,
# needed by claude_account.py for the Claude Code CLI (Claude subscription
# OAuth). If you only use API-key providers (Gemini, OpenAI, Mistral...), Node
# is unused at runtime but harmless.
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g @anthropic-ai/claude-code \
    && apt-get purge -y gnupg && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*
# curl is kept: used by the HEALTHCHECK below.

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Data dir: conversations, encrypted mapping, linked AI accounts. Always mounted
# as a volume in production (see docker-compose.yml), never left only in the image.
RUN mkdir -p data

# Run as non-root (defense in depth). uid 1000 matches the most common host user
# on a first Docker deployment, so files created in ./data stay host-editable.
RUN useradd -m -u 1000 tokenveil && chown -R tokenveil:tokenveil /app
USER tokenveil

EXPOSE 8500

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8500/healthz || exit 1

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8500"]

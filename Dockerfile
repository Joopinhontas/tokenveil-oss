# Image volontairement plus lourde que la moyenne FastAPI : les modèles
# spaCy fr+en (large) pèsent ~1 Go à eux seuls, et le CLI Claude Code a
# besoin de Node.js en plus de Python. C'est le prix de tourner le moteur
# d'anonymisation et la liaison OAuth Claude entièrement self-hosted.
FROM python:3.12-slim

# Node.js : nécessaire pour `claude` (Claude Code CLI), utilisé par
# claude_account.py pour la liaison OAuth et l'envoi des prompts. Gemini
# (clé API, google-genai) n'a besoin d'aucun binaire externe.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g @anthropic-ai/claude-code \
    && apt-get purge -y gnupg && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*
# curl est gardé : utilisé par le HEALTHCHECK ci-dessous

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# dossier de données : conversations, mapping chiffré, comptes liés Claude/
# Gemini — toujours monté en volume en production (voir docker-compose.yml),
# jamais laissé seulement dans la couche d'image.
RUN mkdir -p data

EXPOSE 8500

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8500/healthz || exit 1

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8500"]

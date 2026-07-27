# Install TokenVeil (Community edition)

A full Docker deployment, written to be followed start to finish with no prior knowledge of the project. To tune the options (authentication, providers, keywords, reverse proxy, and so on), see [CONFIG.md](CONFIG.md).

> 🇫🇷 Version française : [INSTALL.fr.md](INSTALL.fr.md)

---

## 1. Requirements

- A Linux server (bare metal, VM, or cloud) with **Docker** and **Docker Compose v2**.
- **1 to 2 GB of RAM** is enough: the Community engine is light and loads no models.
- About 1 GB of disk for the image.
- One free HTTP port (**8500** by default, configurable).
- If you expose it publicly: a domain name and a reverse proxy in front of the service (see [CONFIG.md](CONFIG.md)).

Nothing to install by hand: everything ships inside the Docker image.

## 2. Get the project

```bash
git clone https://github.com/Joopinhontas/tokenveil-oss.git tokenveil
cd tokenveil
```

## 3. Minimal configuration

```bash
cp .env.example .env
```

Set at least these two values in `.env`:

| Variable | Purpose | How to get it |
|---|---|---|
| `ANON_DB_KEY` | Encryption key for data at rest | `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `WEBAPP_USERS` | Login account(s), format `user:password` | Set your own |

**Important**: `ANON_DB_KEY` must **never** be lost or regenerated on an instance that already holds data (otherwise existing conversations can no longer be de-anonymized). Keep it in a secrets manager, not only in the `.env` file.

Every other option is covered in [CONFIG.md](CONFIG.md).

## 4. Start it

```bash
docker compose up -d --build
```

The build is **light and fast** (no model to download). Check that the service answers:

```bash
curl http://localhost:8500/healthz
# {"status": "ok"}
```

## 5. First access

- Open `http://<server>:8500/`.
- Sign in with an account from `WEBAPP_USERS` (the first one automatically becomes an administrator).
- Under **Preferences > AI accounts**, link an AI. The quickest way to test: a free **Gemini API key** (aistudio.google.com). Claude, OpenAI, Mistral, and the enterprise clouds are available too.
- Paste some text containing IPs, emails, API keys, and watch the anonymization before sending, then the restoration in the response.

## 6. Backup and updates

One folder to back up: **`./data`** (Docker volume), which holds the database and the linked AI accounts.

```bash
tar -czf tokenveil-backup-$(date +%F).tar.gz data/
```

To update the code:

```bash
git pull
docker compose up -d --build
```

A rebuild never touches `./data`: history and linked accounts survive.

## 7. Quick troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `docker compose up` fails, port already in use | Port taken | Change `ANON_PORT` in `.env` |
| Response streaming is not smooth behind a proxy | Reverse proxy buffering | See the reverse proxy section of [CONFIG.md](CONFIG.md) |
| Claude linking fails | Claude CLI unavailable | Start with an API-key provider instead (Gemini, OpenAI, and so on) |
| A user does not see Administration | Non-admin role | Promote them from Administration > Users |

---

Going further: [CONFIG.md](CONFIG.md) (configuration), [SECURITY.md](SECURITY.md) (security), [ARCHITECTURE.md](ARCHITECTURE.md) (overview).

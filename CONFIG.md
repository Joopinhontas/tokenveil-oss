# Configure TokenVeil

Every option is set either in the `.env` file (at deployment) or from the interface (Preferences and Administration). This document covers both. For the initial install, see [INSTALL.md](INSTALL.md).

> 🇫🇷 Version française : [CONFIG.fr.md](CONFIG.fr.md)

---

## 1. Environment variables (`.env`)

| Variable | Purpose | Default |
|---|---|---|
| `ANON_DB_KEY` | Encryption key for data at rest (**required**, keep it safe) | *(none)* |
| `WEBAPP_USERS` | Local accounts, format `user:password,user2:password2` | *(none)* |
| `AUTH_BACKEND` | Authentication method: `local` | `local` |
| `ANON_LANGUAGE` | Default detection language (`fr` / `en`) | `fr` |
| `ANON_PORT` | Port exposed on the host | `8500` |
| `COOKIE_SECURE` | Session cookie over HTTPS only, plus HSTS (set `true` behind TLS) | `false` |

> **LDAP / Active Directory** authentication (with group restriction and multi-tenant seat quotas) is an **Enterprise** feature.

## 2. Link an AI (per user)

Each user links their own AI under **Preferences > AI accounts**. No shared key: everyone uses their own access.

- **Gemini / OpenAI / Mistral**: a personal or company API key (the simplest way to start is a free Gemini key at aistudio.google.com).
- **Claude**: a Pro/Max subscription through sign-in, or an API key.
- **Vertex AI, Amazon Bedrock, Azure OpenAI, GitHub Models**: the credentials of the matching enterprise cloud.

The list of models in the picker **updates on its own**: new models appear, retired ones disappear, with no manual step.

## 3. Business keywords to anonymize

Under **Preferences > Keywords to anonymize**, add the internal project names, codenames, or identifiers specific to your organization that generic detection cannot guess. An administrator can push these rules to the whole team or to a specific user from the **Administration** panel.

## 4. Detected data categories

Under **Administration > Entities**, an administrator can **turn off** whole categories (for example, not anonymizing internal IP addresses for a given deployment). Handy for matching the masking level to the context.

## 5. Account and role management

Under **Administration > Users** (visible to admin accounts): create local accounts, promote a user to administrator, remove access. The first account created at startup is automatically an administrator.

## 6. Audit log

**Administration > Audit log**: records, for each message, **which category** of data was masked and **how many times**, never the real value. Useful to prove to a compliance officer that anonymization is happening, without creating a leak risk of its own.

## 7. Public exposure behind a reverse proxy

TokenVeil delivers the AI response **as a stream** (as it is generated). Most reverse proxies buffer responses by default, which breaks that effect. On the block pointing to TokenVeil (Nginx example):

```nginx
proxy_buffering off;
proxy_cache off;
proxy_http_version 1.1;
proxy_read_timeout 300s;
```

Turn on **HTTPS** as well (and set `COOKIE_SECURE=true`) as soon as the service is exposed beyond the local network: otherwise login credentials travel in clear text.

## 8. Pre-production checklist

- [ ] `ANON_DB_KEY` generated for this deployment and backed up off the server.
- [ ] `COOKIE_SECURE=true` and HTTPS active if exposed beyond the local network.
- [ ] `WEBAPP_USERS` passwords different from the `.env.example` samples.
- [ ] Network access to the port restricted if you do not want public exposure (firewall/VPN).
- [ ] Backups of `./data` scheduled.

---

See also: [SECURITY.md](SECURITY.md) (security measures) and [ARCHITECTURE.md](ARCHITECTURE.md) (overview).

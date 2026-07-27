# TokenVeil, architecture overview

This document explains the **idea** and the design principles behind TokenVeil. It does not cover the internal implementation. To install it: [INSTALL.md](INSTALL.md). To configure it: [CONFIG.md](CONFIG.md). For security and compliance: [SECURITY.md](SECURITY.md).

> 🇫🇷 Version française : [ARCHITECTURE.fr.md](ARCHITECTURE.fr.md)

## In one sentence

TokenVeil is a self-hosted proxy that sits between your teams and the AI models (Claude, Gemini, OpenAI, Mistral, and the enterprise clouds). It **anonymizes sensitive data before a message reaches the model**, then restores the real values in the response shown to the user. The real data never leaves your infrastructure.

## How a message flows

```
User (real data)
        │
        ▼
[1] Authentication (local accounts or LDAP/AD)
        │
        ▼
[2] Anonymization  →  sensitive data becomes neutral tokens
        │              (<PERSON_1>, <IP_ADDRESS_2>, <API_SECRET_3>...)
        ▼
[3] Sent to the model  →  ONLY the tokenized text leaves the network
        │
        ▼
[4] Model response  →  the model echoes the tokens as-is
        │
        ▼
[5] In-memory de-anonymization  →  the real values are restored
        │
        ▼
Shown to the user (readable response, real data)
```

What matters here:

- Tokenization happens **server-side, before the outbound call**. De-tokenization happens **after the response, in memory**. The AI provider only ever sees tokens, going out and coming back.
- Anonymization is **reversible on your side only**: the token-to-real-value mapping never leaves your process, and is never sent to the model.
- Stored messages contain **only the anonymized version**. The real data is never kept in clear text.

## The anonymization engine

The engine detects sensitive data and replaces it with typed tokens (the type is preserved: `<PERSON_1>`, `<IBAN_CODE_2>`), so the model still understands **what it is dealing with** without ever seeing the real value. The same value always gets the same token, which keeps relationships intact for the model.

Categories covered: personal names, emails, phone numbers, IP addresses (public and internal), MAC addresses, IBANs, credit cards, national IDs (including the French NIR), license plates, API keys and technical secrets, financial amounts, log identifiers, customer references, and **business terms specific to your organization** added in configuration.

The organization running the deployment can **turn off some categories** as needed.

### Two editions, one interface

TokenVeil comes in two editions that share **the same product** (interface, authentication, providers, storage, deployment) and differ only in the detection engine:

- **Community** (this repository, free): a deterministic engine covering the high-confidence categories above. Enough to evaluate the product end to end.
- **Enterprise** (commercial license): adds advanced detection of names, organizations, and locations **in free text** (NER plus machine learning), along with file anonymization (office documents, PDFs, OCR) and enterprise directory integration. The advanced engine drops in behind the same interface.

Anonymization quality is **measured** (an annotated test set plus random fuzzing) and published: see [tokenveil.eu/benchmark](https://tokenveil.eu/benchmark).

## Authentication

Two modes, your choice at deployment:

- **Local accounts**: managed inside the application (hashed passwords). Enough for a trial or a small team.
- **LDAP / Active Directory** (Enterprise): employees authenticate through the company's existing directory, with group restriction and seat quotas.

## Data and privacy

- Everything runs on **your** infrastructure (on-premises server or sovereign cloud). Nothing is hosted by the vendor.
- Sensitive data at rest (the anonymization mapping, the credentials of linked AI accounts) is **encrypted**.
- **No content telemetry**: the only outbound traffic, if any, is a license check that carries no personal data.

The security measures are detailed in [SECURITY.md](SECURITY.md).

## Supported AI providers

Claude (through a Pro/Max subscription or an API key), Gemini, OpenAI, Mistral, plus the enterprise cloud deployments: Vertex AI (Google Cloud), Amazon Bedrock, Azure OpenAI, GitHub Models. The user picks the model in the interface, and the list of available models updates on its own.

## Deployment

A single Docker container, `docker compose up`. The frontend is served as-is (no build step). One data folder to back up (database plus configuration). See [INSTALL.md](INSTALL.md).

## License

The code of this Community edition is available under the **Elastic License 2.0** (you may read, audit, self-host, and modify it; you may not turn it into a hosted service for third parties). The **Enterprise** features (ML engine, files, LDAP/AD, multi-tenant) and commercial deployment require a **commercial license**: [contact@tokenveil.eu](mailto:contact@tokenveil.eu).

## Status

Alpha. The core mechanism is validated end to end and measured. A third-party security audit is on the roadmap before any large-scale deployment.

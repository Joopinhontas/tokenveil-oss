# Security and privacy

This document describes the **security principles** of TokenVeil, for a CIO, CISO, or DPO evaluating the product. It states what is in place and what stays the organization's responsibility, without going into implementation detail.

> 🇫🇷 Version française : [SECURITY.fr.md](SECURITY.fr.md)

---

## 1. The core principle: the data does not leave

TokenVeil replaces sensitive data with neutral tokens **before** a message reaches the AI provider, and restores the real values **after** the response, locally. In practice:

- The AI provider only receives tokenized text, going in and coming back.
- The mapping between a token and its real value **never leaves your process** and is never sent to the model.
- Stored messages contain only the **anonymized** version: the real data is never kept in clear text.

Under the GDPR, this is **pseudonymization** applied by default, on your side. The AI provider cannot re-identify the data it receives.

## 2. Self-hosting and no third party

- Everything runs on **your** infrastructure (on-premises server or the sovereign cloud of your choice). No data is hosted by the vendor.
- **No content or usage telemetry.** The only possible outbound traffic is a license check, which carries **no personal data**.
- The vendor adds **no sub-processor** to your processing chain.

## 3. Encryption at rest

The sensitive items that are stored are encrypted:

- the anonymization mapping (token to real value);
- the credentials of the AI accounts each user links.

The encryption key is specific to your deployment and stays under your control.

## 4. Authentication and access control

- Local accounts with **hashed passwords** following current recommendations, or integration with your **enterprise directory** (LDAP / Active Directory, Enterprise edition).
- **Brute-force protection** on login, per account and per address.
- Separation of administrator and user roles; strict isolation between users (nobody sees anyone else's conversations).

## 5. Application hardening

- Strict **HTTP security headers** on every response, including a content security policy (CSP) with **no third-party origin**: all assets are served locally, which allows air-gapped operation and removes the risk of injection through an external CDN.
- Anti-abuse **rate limiting**.
- The container runs as a **non-privileged user** (non-root).
- Channel encryption (**HTTPS/TLS**) to enable on your side as soon as there is any network exposure.

## 6. Auditability

An **audit log** records, for each message, the category and the number of masked items, **never the real value**. It lets you show that anonymization is happening, without recreating a leak risk in the log itself.

## 7. Anonymization quality, measured

The leak rate (the share of sensitive data still readable after processing) is **measured**: an annotated test set plus random fuzzing (thousands of cases generated differently on every run). An independent verifier re-checks that nothing survives in the anonymized text. The methodology is public and reproducible: [tokenveil.eu/benchmark](https://tokenveil.eu/benchmark).

**Owned limit**: no detection system is perfect. A proper name unknown to the engine, in an ambiguous context, can in theory slip through. TokenVeil strongly reduces the risk without contractually eliminating it to 100%.

## 8. What stays your responsibility

- The physical and logical security of the infrastructure that hosts TokenVeil (it stays yours).
- Turning on HTTPS/TLS and restricting the network (firewall/VPN) based on your exposure.
- Backups and data retention according to your policy.
- The contractual relationship and any DPA with the AI provider you choose.

## 9. Status

Alpha. The core mechanism is validated and measured. A **third-party security audit / penetration test** is on the roadmap before any large-scale deployment. We can answer a vendor security questionnaire and sign a standard NDA.

---

Security and compliance contact: [contact@tokenveil.eu](mailto:contact@tokenveil.eu)

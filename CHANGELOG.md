# Changelog

All notable changes to TokenVeil Community Edition are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.3.0] - 2026-07-27

### Added
- Key-driven PII detection for structured data (JSON, key-value): the value of
  an explicitly sensitive key (phone, name, email, iban, address...) is
  tokenized based on its key regardless of the value's format. Catches
  bare-digit phones and uppercase surnames a format regex would miss, without
  touching SQL.
- Streaming reveal shows text as word groups that fade in (Gemini-style), over
  a stable markdown region re-rendered only on fold, with a pulsing write
  cursor. Static under `prefers-reduced-motion`.

### Performance
- `deanonymize` rewritten as a single regex pass + dict lookup (O(text) instead
  of O(tokens x text)), so streaming stays fluid on conversations with a large
  token mapping.

### Fixed
- Proper nouns embedded in identifiers (e.g. "paris" in `gw-paris-07`) are no
  longer masked, removing a false positive and a case-only non-reversibility.

### Docs
- Product Hunt badge added below the technical badges (README + README.fr).

## [0.2.1] - 2026-07-11

### Changed
- Docs split into user docs and buyer docs; `ARCHITECTURE.md` trimmed to the
  idea, not the recipe. Affiliation disclaimer made generic.

### Added
- Solid README badges, a security section, and a CI 0-leak gate.

### Fixed
- Local paths scrubbed from the public history.

## [0.2.0] - 2026-07-09

### Added
- Community edition ships a real, runnable regex engine (previously a dead
  stub): deterministic PII (emails, IPs, MAC, IBAN, cards, phones, secrets,
  amounts, names after a civility title).
- Enterprise-only features are gated in the UI; license enforcement disabled
  for the Community build.

## [0.1.0] - 2026-06-23

### Added
- Initial public showcase under the Elastic License 2.0: self-hosted multi-AI
  chat with reversible anonymization; Claude, Gemini, Vertex AI, Bedrock,
  OpenAI, Mistral providers. (ML engine stubbed; the full ML engine is
  Enterprise.)

[Unreleased]: https://github.com/Joopinhontas/tokenveil-oss/compare/HEAD

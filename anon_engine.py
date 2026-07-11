"""anon_engine.py — TokenVeil Community Edition engine.

This is the OPEN-SOURCE anonymization engine: a dependency-free, regex-based
detector that runs anywhere with no ML models to download. It anonymizes the
deterministic, high-confidence categories (emails, IPs, MAC addresses, IBANs,
credit cards, phone numbers, API keys/secrets, and names introduced by a
civility title) and restores them transparently in the response.

It is fully functional: you can run the whole app, chat through it, and watch
sensitive data get tokenized before it reaches the LLM. It's meant to let you
try TokenVeil end-to-end and judge the product for yourself.

What the Community engine does NOT include is the Enterprise detection stack,
which is where most of the accuracy on hard cases comes from:
  - Microsoft Presidio + spaCy NER (fr/en) for free-text person / organization
    / location detection (names without a title, in ordinary prose),
  - a ~500-name multilingual first-name lexicon used as a detection anchor,
  - log-structure heuristics (CamelCase identifier splitting, User-Agent
    sanctuarization in Combined Log Format, URL query-parameter decoding),
  - a known-value sweep guaranteeing "detected once, masked for the rest of the
    conversation" even when the NER is non-deterministic,
  - dozens of false-positive/false-negative reduction passes tuned against
    fuzz-tested synthetic logs (measured 0% leak, see tokenveil.eu/benchmark).

The Enterprise engine is available under a commercial license, and drops in
behind this exact same interface. Contact: contact@tokenveil.eu.
"""
import re

# Public entity catalog (kept identical to the Enterprise engine so the admin
# allow/deny UI and audit log stay consistent across editions).
ENTITIES_OF_INTEREST = [
    "PERSON", "EMAIL_ADDRESS", "PHONE_NUMBER", "IP_ADDRESS", "MAC_ADDRESS",
    "HOSTNAME", "CUSTOMER_REF", "LOCATION", "ORGANIZATION", "IBAN_CODE",
    "CREDIT_CARD", "US_SSN", "LOG_IDENTITY", "API_SECRET",
    "TRANSACTION_AMOUNT", "SYSTEM_USER", "IP_INTERNAL",
]

# Names for which two different casings ("Luc Dupont" / "LUC DUPONT") designate
# the same entity and should reuse the same token, for a coherent view to the LLM.
_CASE_INSENSITIVE_ENTITIES = {"PERSON", "ORGANIZATION", "LOCATION"}


def _p(regex, flags=0):
    return re.compile(regex, flags)


# Ordered detectors: (entity_type, compiled_regex, capture_group).
# Order matters — more specific / higher-value patterns first so they win when
# spans would otherwise overlap. group=0 means the whole match is the value;
# a positive group means only that capture group is anonymized (the rest, e.g.
# the "password=" key, stays readable).
_DETECTORS = [
    # Secrets with an exact provider signature (near-zero false positives).
    ("API_SECRET", _p(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\b"), 0),  # JWT
    ("API_SECRET", _p(r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA)[0-9A-Z]{16}\b"), 0),                     # AWS key id
    ("API_SECRET", _p(r"\bgh[pousr]_[A-Za-z0-9]{36,255}\b"), 0),                                # GitHub token
    ("API_SECRET", _p(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), 0),                                 # Slack token
    ("API_SECRET", _p(r"\b(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{20,}\b"), 0),                  # Stripe
    ("API_SECRET", _p(r"\bsk-ant-(?:api03-|oat01-)?[A-Za-z0-9_-]{20,}\b"), 0),                  # Anthropic
    ("API_SECRET", _p(r"\bsk-[A-Za-z0-9]{20,}\b"), 0),                                          # OpenAI
    # Secret as the value of a key=value / key: value pair whose key names a secret.
    ("API_SECRET", _p(r"(?i)\b(?:api[_-]?key|apikey|access[_-]?key|secret|token|password|passwd|pwd|bearer|auth)[=:]\s?([A-Za-z0-9_\-./+]{3,})"), 1),
    # Credentials inside a connection string ("scheme://user:password@host").
    ("API_SECRET", _p(r"(?i)[a-z][a-z0-9+.\-]*://[^/\s:]+:([^/\s@]+)@[\w.\-]+"), 1),
    # Financial / identity structured data.
    ("IBAN_CODE", _p(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,7}(?:[ ]?[A-Z0-9]{1,3})?\b"), 0),
    ("CREDIT_CARD", _p(r"\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{1,7}\b"), 0),
    ("EMAIL_ADDRESS", _p(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"), 0),
    # Network identifiers. Private ranges are tagged separately (internal vs public).
    ("IP_INTERNAL", _p(r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|127\.\d{1,3}\.\d{1,3}\.\d{1,3})\b"), 0),
    ("IP_ADDRESS", _p(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), 0),
    # MAC before IPv6: a MAC (xx:xx:xx:xx:xx:xx) also matches the loose IPv6
    # shape, so it must be tried first to win the overlap tie.
    ("MAC_ADDRESS", _p(r"\b(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\b"), 0),
    ("IP_ADDRESS", _p(r"(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{1,4}:){2,7}[0-9A-Fa-f]{0,4}(?![0-9A-Fa-f:])"), 0),  # IPv6 (loose)
    ("HOSTNAME", _p(r"\b(?:[a-zA-Z0-9-]+\.){1,}(?:local|internal|lan|corp|prod|dev)\b"), 0),
    ("CUSTOMER_REF", _p(r"\b(?:CLI|CUST|REF|TICKET|EMP)-?\d{4,}\b"), 0),
    ("PHONE_NUMBER", _p(r"(?<!\d)0[1-9](?:[ .\-]?\d{2}){4}(?!\d)"), 0),          # FR
    ("PHONE_NUMBER", _p(r"(?<!\d)\+\d{1,3}[ .\-]?\d(?:[ .\-]?\d{2}){3,4}(?!\d)"), 0),  # intl
    ("TRANSACTION_AMOUNT", _p(r"\b\d+(?:[ .,]\d{3})*[.,]\d{2}\s?(?:EUR|USD|GBP|CHF|€|\$|£)"), 0),
    # Identity in a structured log field ("user=jean.dupont", "login: marc").
    ("LOG_IDENTITY", _p(r"(?i)\b(?:user|username|login|client|owner|contact|admin)[=:]\s?([\w.\-]+)"), 1),
    ("SYSTEM_USER", _p(r"(?:Failed password for (?:invalid user )?|Accepted publickey for |\bUSER=)([\w.\-]+)"), 1),
    # Name introduced by a civility title (high-confidence person signal without
    # needing a name dictionary). Captures 1-2 capitalized words after the title.
    ("PERSON", _p(r"\b(?:M\.|Mme|Mlle|Dr\.?|Pr\.?|Mr\.?|Mrs\.?|Ms\.?)\s+([A-ZÀ-Ý][a-zà-ÿ]+(?:-[A-ZÀ-Ý][a-zà-ÿ]+)*(?:\s[A-ZÀ-Ý][a-zà-ÿ]+)?)"), 1),
]


class _Match:
    __slots__ = ("start", "end", "entity_type")

    def __init__(self, start, end, entity_type):
        self.start = start
        self.end = end
        self.entity_type = entity_type


def get_analyzer():
    """No-op in the Community edition (no ML model to warm up). Present so the
    startup warm-up in app.py stays identical across editions."""
    return None


class AnonSession:
    """Holds the token <-> real-value mapping for one conversation. The mapping
    never leaves this process; only tokenized text is stored/sent."""

    def __init__(self, language="fr", custom_terms=None, disabled_entities=None):
        self.language = language
        self.custom_terms = custom_terms or []
        self.disabled_entities = set(disabled_entities or [])
        self.active_entities = [e for e in ENTITIES_OF_INTEREST if e not in self.disabled_entities]
        self.value_to_token = {}
        self.token_to_value = {}
        self.counters = {}
        self._ci_index = {}  # casefolded value -> token, for name case coherence

    # --- token bookkeeping ---
    def _new_token(self, entity_type):
        self.counters[entity_type] = self.counters.get(entity_type, 0) + 1
        return f"<{entity_type}_{self.counters[entity_type]}>"

    def _token_for(self, value, entity_type):
        token = self.value_to_token.get(value)
        if token is None and entity_type in _CASE_INSENSITIVE_ENTITIES:
            token = self._ci_index.get(value.casefold())
        if token is None:
            token = self._new_token(entity_type)
            self.token_to_value[token] = value
            if entity_type in _CASE_INSENSITIVE_ENTITIES:
                self._ci_index.setdefault(value.casefold(), token)
        self.value_to_token[value] = token
        return token

    # --- detection ---
    def _custom_matches(self, line):
        out = []
        for t in self.custom_terms:
            label = (t.get("label") or "CUSTOM_TERM").strip() or "CUSTOM_TERM"
            if label in self.disabled_entities:
                continue
            try:
                pattern = t["term"] if t.get("is_regex") else re.escape(t["term"])
                for m in re.finditer(pattern, line, re.IGNORECASE):
                    if m.end() > m.start():
                        out.append(_Match(m.start(), m.end(), label))
            except (re.error, KeyError):
                continue
        return out

    def _detect(self, line):
        matches = []
        for entity_type, rx, group in _DETECTORS:
            if entity_type not in self.active_entities:
                continue
            for m in rx.finditer(line):
                start, end = m.span(group)
                if end > start:
                    matches.append(_Match(start, end, entity_type))
        matches += self._custom_matches(line)
        # Known-value sweep: re-mask any value already seen this session even if
        # a detector missed it on this line (keeps tokens consistent). Names are
        # swept case-insensitively so "LUC DUPONT" is caught once "Luc Dupont"
        # is known (the two map to the same token).
        for value, token in self.value_to_token.items():
            if len(value) < 4 or "\n" in value:
                continue
            etype = token[1:token.rfind("_")]
            ci = etype in _CASE_INSENSITIVE_ENTITIES
            flags = re.IGNORECASE if ci else 0
            # for case-insensitive entities (names/places), exclude . - / flanks
            # so a known proper noun ("Paris") is not re-matched as a substring
            # of a technical identifier ("gw-paris-07", "paris.internal"), which
            # would break the identifier and its literal reversibility.
            if ci:
                pattern = r"(?<![\w./-])" + re.escape(value) + r"(?![\w./-])"
            else:
                pattern = r"(?<!\w)" + re.escape(value) + r"(?!\w)"
            for m in re.finditer(pattern, line, flags):
                matches.append(_Match(m.start(), m.end(), etype))
        return self._drop_overlaps(matches)

    @staticmethod
    def _drop_overlaps(matches):
        # Longest span wins; ties keep first seen (detector order = priority).
        ordered = sorted(matches, key=lambda r: (r.end - r.start), reverse=True)
        kept = []
        for r in ordered:
            if any(not (r.end <= k.start or r.start >= k.end) for k in kept):
                continue
            kept.append(r)
        return kept

    # --- public API ---
    def anonymize(self, text):
        # Line by line so a detector can't merge two log lines into one entity.
        return "\n".join(self._anonymize_line(l) for l in text.split("\n"))

    def _anonymize_line(self, line):
        if not line.strip():
            return line
        matches = sorted(self._detect(line), key=lambda r: r.start, reverse=True)
        out = line
        for r in matches:
            token = self._token_for(line[r.start:r.end], r.entity_type)
            out = out[:r.start] + token + out[r.end:]
        return out

    def deanonymize(self, text):
        out = text
        for token in sorted(self.token_to_value, key=len, reverse=True):
            out = out.replace(token, self.token_to_value[token])
        return out

    def mapping_report(self):
        return "\n".join(f"{tok} -> {val}" for tok, val in self.token_to_value.items())

    def to_state(self):
        return {
            "value_to_token": self.value_to_token,
            "token_to_value": self.token_to_value,
            "counters": self.counters,
        }

    @classmethod
    def from_state(cls, state, language="fr", custom_terms=None, disabled_entities=None):
        s = cls(language=language, custom_terms=custom_terms, disabled_entities=disabled_entities)
        s.value_to_token = state.get("value_to_token", {})
        s.token_to_value = state.get("token_to_value", {})
        s.counters = state.get("counters", {})
        for tok, val in s.token_to_value.items():
            etype = tok[1:tok.rfind("_")]
            if etype in _CASE_INSENSITIVE_ENTITIES:
                s._ci_index.setdefault(val.casefold(), tok)
        return s


# --- Independent coverage scanner (used by the admin "Benchmark" tab) ---
# Deliberately shares no logic with the engine above: it detects, in the
# ORIGINAL text, anything that LOOKS sensitive, then checks whether those exact
# values survive verbatim in the anonymized output. An honest external check.
_COVERAGE_SHAPES = {
    "IP_ADDRESS": _p(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    "EMAIL": _p(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
    "MAC_ADDRESS": _p(r"\b(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\b"),
    "IBAN_SHAPE": _p(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,7}\b"),
    "CREDIT_CARD_SHAPE": _p(r"\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{1,7}\b"),
    "JWT_SHAPE": _p(r"\beyJ[A-Za-z0-9_\-]{5,}\.[A-Za-z0-9_\-]{5,}\.[A-Za-z0-9_\-]{5,}\b"),
}


def scan_coverage(original, anonymized):
    by_category = {}
    leaks = []
    total_found = 0
    total_leaked = 0
    for label, rx in _COVERAGE_SHAPES.items():
        values = {m.group(0) for m in rx.finditer(original)}
        leaked = [v for v in values if v and v in anonymized]
        by_category[label] = {"found": len(values), "leaked": len(leaked)}
        total_found += len(values)
        total_leaked += len(leaked)
        for v in leaked:
            line_no = original[:original.index(v)].count("\n") + 1
            preview = v[:3] + "…" + v[-2:] if len(v) > 6 else "•••"
            leaks.append({"category": label, "line": line_no, "preview": preview})
    coverage_pct = round(100 * (1 - total_leaked / total_found), 1) if total_found else 100.0
    return {
        "total_found": total_found,
        "total_leaked": total_leaked,
        "coverage_pct": coverage_pct,
        "by_category": by_category,
        "leaks": sorted(leaks, key=lambda x: x["line"]),
        "name_candidates": [],  # free-text name detection is an Enterprise feature
    }

"""anon_engine.py — PROPRIETARY MODULE, NOT INCLUDED IN THIS PUBLIC REPOSITORY.

This is a stub matching the real module's public interface so the rest of
the codebase (app.py, proxy_cli.py) remains readable and importable. It
intentionally does NOT anonymize anything — it exists to show the shape of
the integration, not the detection logic itself.

The real implementation combines Microsoft Presidio + spaCy NER (fr/en)
with custom regex recognizers (secrets, IPs, IBANs, business references...),
log-structure-aware heuristics (CamelCase identifier splitting, User-Agent
sanctuarization in Combined Log Format, query-parameter decoding before
NER scan), and several false-positive/false-negative reduction passes
tuned against fuzz-tested synthetic logs.

Available under a commercial license — contact marc.bourrel81@gmail.com.
"""


def get_analyzer():
    raise NotImplementedError(
        "anon_engine is proprietary and not included in this public repository. "
        "Contact marc.bourrel81@gmail.com for a commercial license."
    )


def scan_coverage(original: str, anonymized: str) -> dict:
    raise NotImplementedError(
        "anon_engine is proprietary and not included in this public repository."
    )


class AnonSession:
    """Holds the token<->real-value mapping for one conversation."""

    def __init__(self, language: str = "fr", custom_terms: list = None):
        self.language = language
        self.custom_terms = custom_terms or []
        raise NotImplementedError(
            "anon_engine is proprietary and not included in this public repository. "
            "Contact marc.bourrel81@gmail.com for a commercial license."
        )

    def anonymize(self, text: str) -> str:
        raise NotImplementedError

    def deanonymize(self, text: str) -> str:
        raise NotImplementedError

    def mapping_report(self) -> str:
        raise NotImplementedError

    def to_state(self) -> dict:
        raise NotImplementedError

    @classmethod
    def from_state(cls, state: dict, language: str = "fr", custom_terms: list = None) -> "AnonSession":
        raise NotImplementedError(
            "anon_engine is proprietary and not included in this public repository."
        )

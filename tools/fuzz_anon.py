#!/usr/bin/env python3
"""Random fuzzer for the Community anonymization engine.

Generates synthetic log lines with sensitive values (email, IP, secret, IBAN,
credit card, phone) mixed into neutral decoy text, then measures how many
sensitive values leak through anonymization and how much neutral text gets
over-redacted. A different seed on every run means nobody knows the exact cases
in advance: it's a statistical measurement, not a hand-picked demo.

This mirrors the methodology described at https://tokenveil.eu/benchmark. Note
that it measures the COMMUNITY engine (deterministic categories only); the
Enterprise engine adds free-text name/organization detection and is measured
separately.

Usage:
    python3 tools/fuzz_anon.py            # 2000 lines, random seed
    python3 tools/fuzz_anon.py --n 5000
    python3 tools/fuzz_anon.py --seed 42  # reproducible
"""
import argparse
import random
import sys

sys.path.insert(0, ".")
from anon_engine import AnonSession

DECOY_WORDS = [
    "buffer", "cluster", "thread", "queue", "shard", "broker", "replica",
    "snapshot", "checkpoint", "endpoint", "manifest", "pipeline", "registry",
    "throttle", "backoff", "heartbeat", "telemetry", "payload", "schema",
    "migration", "rollback", "cache", "session", "worker", "scheduler",
]
HEX = "0123456789abcdef"


def _rand_email():
    u = "".join(random.choices("abcdefghijklmnopqrstuvwxyz", k=random.randint(4, 9)))
    d = random.choice(["corp", "internal-svc", "example", "acme", "mail"])
    return f"{u}.{random.randint(1,99)}@{d}.net"


def _rand_ip():
    return ".".join(str(random.randint(1, 254)) for _ in range(4))


def _rand_secret():
    return "".join(random.choices(HEX, k=32))


def _rand_iban():
    return "FR76" + "".join(random.choices("0123456789", k=20))


def _rand_card():
    return " ".join("".join(random.choices("0123456789", k=4)) for _ in range(4))


def _rand_phone():
    return "0" + str(random.randint(1, 9)) + "".join(
        random.choices("0123456789", k=8)
    )


_GENERATORS = [
    ("email", _rand_email, lambda v: f"notification sent to {v}"),
    ("ip", _rand_ip, lambda v: f"connection from {v} refused"),
    ("secret", _rand_secret, lambda v: f"apikey={v} rotated"),
    ("iban", _rand_iban, lambda v: f"transfer to {v} pending"),
    ("card", _rand_card, lambda v: f"payment card {v} declined"),
    ("phone", _rand_phone, lambda v: f"callback number {v} logged"),
]


def gen_case(i):
    """Returns (line, sensitive_values, decoy_values)."""
    kind, gen, tmpl = random.choice(_GENERATORS)
    value = gen()
    decoys = random.sample(DECOY_WORDS, k=random.randint(2, 4))
    prefix = " ".join(decoys)
    line = f"{prefix} {tmpl(value)}"
    return line, [value], decoys


def run_fuzz(n, seed):
    random.seed(seed)
    total_sensitive = total_leaked = total_decoy = total_over_redacted = 0
    leak_examples = []
    over_examples = []

    for i in range(n):
        line, sensitive_values, decoy_values = gen_case(i)
        session = AnonSession(language="en")
        anon = session.anonymize(line)
        for v in sensitive_values:
            total_sensitive += 1
            if v in anon:
                total_leaked += 1
                if len(leak_examples) < 25:
                    leak_examples.append((line, v, anon))
        for v in decoy_values:
            total_decoy += 1
            if v not in anon:
                total_over_redacted += 1
                if len(over_examples) < 25:
                    over_examples.append((line, v, anon))

    return {
        "n": n,
        "seed": seed,
        "total_sensitive": total_sensitive,
        "total_leaked": total_leaked,
        "leak_rate": round(100 * total_leaked / total_sensitive, 2) if total_sensitive else 0.0,
        "total_decoy": total_decoy,
        "total_over_redacted": total_over_redacted,
        "over_redaction_rate": round(100 * total_over_redacted / total_decoy, 2) if total_decoy else 0.0,
        "leak_examples": leak_examples,
        "over_examples": over_examples,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()
    seed = args.seed if args.seed is not None else random.randrange(2**31)

    print(f"Fuzzing {args.n} random lines (seed={seed})...")
    result = run_fuzz(args.n, seed)
    print("=" * 60)
    print(f"Sample          : {result['n']} lines (seed {result['seed']})")
    print(f"Sensitive values: {result['total_sensitive']}")
    print(f"Leaks           : {result['total_leaked']} ({result['leak_rate']}%)")
    print(f"Over-redaction  : {result['total_over_redacted']} ({result['over_redaction_rate']}%)")
    print("=" * 60)
    if result["leak_examples"]:
        print(f"\n{len(result['leak_examples'])} leak example(s) (replay with --seed {seed}):")
        for line, val, anon in result["leak_examples"][:10]:
            print(f"  value={val!r}\n    in : {line}\n    out: {anon}")
    sys.exit(0 if result["total_leaked"] == 0 else 1)

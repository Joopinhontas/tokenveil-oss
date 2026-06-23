#!/usr/bin/env python3
"""CLI proxy: anonymise un texte/log, l'envoie à Claude, désanonymise la réponse.

Usage:
    python proxy_cli.py "ligne de log brute ici"
    echo "ligne de log" | python proxy_cli.py
    python proxy_cli.py --interactive
"""
import argparse
import os
import sys

import anthropic
from dotenv import load_dotenv
from anon_engine import AnonSession

load_dotenv()

SYSTEM_PROMPT = (
    "Tu reçois un texte contenant des tokens de la forme <TYPE_n> (ex: <IP_ADDRESS_1>, "
    "<PERSON_2>, <CUSTOMER_REF_3>). Ce sont des espaces réservés pour des données "
    "anonymisées. Tu DOIS les recopier EXACTEMENT tels quels dans ta réponse, sans les "
    "traduire, reformuler, changer la casse ou les paraphraser. Traite-les comme des "
    "identifiants opaques."
)


def call_claude(client, user_text: str) -> str:
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_text}],
    )
    return resp.content[0].text


def process(client, session: AnonSession, raw_text: str, show_mapping: bool):
    anonymized = session.anonymize(raw_text)
    print("\n--- envoyé à Claude (anonymisé) ---")
    print(anonymized)

    claude_response = call_claude(client, anonymized)
    print("\n--- réponse brute de Claude ---")
    print(claude_response)

    restored = session.deanonymize(claude_response)
    print("\n--- réponse désanonymisée (vraies données) ---")
    print(restored)

    if show_mapping:
        print("\n--- mapping token -> valeur réelle (jamais envoyé à Claude) ---")
        print(session.mapping_report())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("text", nargs="?", help="texte/log brut à traiter")
    parser.add_argument("--interactive", action="store_true", help="mode boucle interactive")
    parser.add_argument("--show-mapping", action="store_true", help="affiche le mapping token<->valeur")
    parser.add_argument("--lang", default="fr", help="langue pour la détection NER (fr/en)")
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ANTHROPIC_API_KEY manquant dans l'environnement.", file=sys.stderr)
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)
    session = AnonSession(language=args.lang)

    if args.interactive:
        print("Mode interactif. Ctrl+D ou 'exit' pour quitter. Mapping conservé sur toute la session.")
        while True:
            try:
                raw = input("\nlog/prompt> ")
            except EOFError:
                break
            if raw.strip().lower() == "exit":
                break
            if not raw.strip():
                continue
            process(client, session, raw, args.show_mapping)
        return

    if args.text:
        raw_text = args.text
    else:
        raw_text = sys.stdin.read()

    if not raw_text.strip():
        print("Aucun texte fourni.", file=sys.stderr)
        sys.exit(1)

    process(client, session, raw_text, args.show_mapping)


if __name__ == "__main__":
    main()

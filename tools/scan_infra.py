#!/usr/bin/env python3
"""Scanner d'infra autonome pour TokenVeil.

À copier-coller et exécuter directement sur le serveur (ou les serveurs) à
protéger — PAS sur la machine qui héberge la webapp. Aucune dépendance
externe (stdlib uniquement), aucune installation requise.

Ce script ne lit QUE :
  - la liste des noms de containers Docker en cours d'exécution (docker ps)
  - les hostnames internes déclarés dans /etc/hosts

Il ne lit JAMAIS de fichier .env, de variable d'environnement, de secret ou
de configuration applicative — volontairement, pour rester sans risque à
exécuter même sur un serveur de production.

Le résultat est un JSON imprimé sur stdout (uniquement ça, pour pouvoir le
copier proprement). Colle ce JSON dans l'onglet "Auto-détection" de
TokenVeil : RIEN n'est ajouté automatiquement, tu choisis ensuite
quels termes activer.

Usage :
    python3 scan_infra.py > resultat.json
    # ou directement :
    python3 scan_infra.py
"""
import json
import re
import subprocess
import sys

# noms techniques génériques très répandus : pas sensibles en soi, on les
# remonte tout de même (transparence) mais décochés par défaut côté UI.
GENERIC_NAMES = {
    "web", "app", "db", "api", "service", "main", "default", "test",
    "staging", "prod", "production", "dev", "development", "proxy",
    "server", "host", "local", "localhost", "redis", "postgres", "postgresql",
    "mysql", "mariadb", "nginx", "traefik", "caddy", "apache", "mongo",
    "mongodb", "rabbitmq", "kafka", "elasticsearch", "grafana", "prometheus",
    "portainer", "watchtower", "registry", "cache", "worker", "queue",
    "frontend", "backend", "client", "data", "storage", "node", "gateway",
}


def scan_docker():
    """Noms des containers Docker en cours d'exécution. Nécessite que
    l'utilisateur lançant ce script ait accès au docker CLI local — aucun
    accès réseau ni montage de socket distant."""
    try:
        out = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if out.returncode != 0:
        return []
    names = [n.strip() for n in out.stdout.splitlines() if n.strip()]
    return [{"term": n, "source": "docker", "default_label": "INFRA_SERVICE"} for n in names]


def scan_hosts():
    """Hostnames internes déclarés dans /etc/hosts (hors loopback/multicast
    standards)."""
    skip = {
        "localhost", "ip6-localhost", "ip6-loopback", "broadcasthost",
        "ip6-allnodes", "ip6-allrouters", "ip6-mcastprefix",
    }
    results = []
    try:
        with open("/etc/hosts") as f:
            content = f.read()
    except OSError:
        return []
    for line in content.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        ip, names = parts[0], parts[1:]
        for n in names:
            if n.lower() in skip or n.startswith("ip6-"):
                continue
            results.append({"term": n, "source": "/etc/hosts", "default_label": "HOSTNAME"})
    return results


def is_generic(term: str) -> bool:
    base = re.split(r"[-_.]", term.lower())[0]
    return term.lower() in GENERIC_NAMES or base in GENERIC_NAMES


def main():
    candidates = scan_docker() + scan_hosts()

    seen = set()
    deduped = []
    for c in candidates:
        key = c["term"].lower()
        if key in seen:
            continue
        seen.add(key)
        c["suggested"] = not is_generic(c["term"]) and len(c["term"]) > 2
        deduped.append(c)

    print(json.dumps({"scanned_at": __import__("time").time(), "candidates": deduped}, indent=2))

    print(
        f"\n[{len(deduped)} terme(s) trouvé(s) — copie le JSON ci-dessus dans "
        "l'onglet 'Auto-détection' de TokenVeil. Rien n'est envoyé "
        "automatiquement nulle part.]",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()

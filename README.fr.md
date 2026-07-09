<p align="center">
  <img src="static/brand/logo-mark.svg" width="96" alt="Logo TokenVeil" />
</p>

<h1 align="center">TokenVeil — Édition Community</h1>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-ELv2-blue.svg" alt="Licence : Elastic License 2.0"></a>
  <a href="../../releases"><img src="https://img.shields.io/github/v/release/Joopinhontas/tokenveil-oss?include_prereleases" alt="Dernière release"></a>
  <a href="Dockerfile"><img src="https://img.shields.io/badge/python-3.12%2B-blue.svg" alt="Python 3.12+"></a>
</p>

Une interface de chat auto-hébergée pour Claude, Gemini, Vertex AI, Bedrock, OpenAI et Mistral qui **anonymise automatiquement les données sensibles avant qu'elles n'atteignent le LLM** (PII, IP internes, clés API/secrets, IBAN, cartes bancaires, références clients...), et restaure de façon transparente les vraies valeurs dans la réponse affichée. **Les données réelles ne quittent jamais votre infrastructure.**

Ce dépôt est l'**édition Community** : entièrement fonctionnelle, code ouvert, libre à auto-héberger. Clonez, `docker compose up`, et vous avez un proxy IA privé qui tourne en deux minutes.

> 🇬🇧 English version: [README.md](README.md)

> Non affilié à, ni approuvé ni sponsorisé par Anthropic ou Google. « Claude » est une marque d'Anthropic PBC, « Gemini » une marque de Google LLC. Ce projet est un client indépendant qui utilise ces modèles via l'abonnement/accès API propre de chaque utilisateur.

---

## Essayer en 60 secondes

```bash
git clone https://github.com/Joopinhontas/tokenveil-oss.git
cd tokenveil-oss
cp .env.example .env          # puis renseignez ANON_DB_KEY + un login WEBAPP_USERS (voir le fichier)
docker compose up -d --build  # build léger : aucun modèle ML à télécharger
```

Ouvrez **http://localhost:8500**, connectez-vous, liez un compte IA (une clé API Gemini gratuite sur aistudio.google.com est le plus rapide), et collez un log plein d'IP, d'e-mails et de clés API. Regardez-les se faire tokeniser avant d'atteindre le modèle, puis restaurer dans la réponse.

Plutôt terminal ? Mesurez directement l'anonymiseur :

```bash
pip install -r requirements.txt
python3 tools/fuzz_anon.py --n 3000   # PII synthétiques aléatoires, mesure le taux de fuite
```

---

## Community vs Enterprise

TokenVeil se décline en deux éditions qui partagent **exactement le même produit** (UI, auth, providers, stockage, Docker) et ne diffèrent que par le **moteur de détection** derrière une interface unique (`anon_engine.py`).

| | **Community** (ce dépôt) | **Enterprise** (commercial) |
|---|---|---|
| Moteur | Regex, sans dépendance | Microsoft Presidio + spaCy NER (fr/en) + ML |
| Installation | Quelques secondes, tourne partout | Embarque ~1 Go de modèles |
| E-mails, IP, MAC, IBAN, cartes, téléphones, secrets | ✅ | ✅ |
| Clés API / tokens / mots de passe (AWS, GitHub, Stripe, JWT, PEM...) | ✅ | ✅ |
| Noms après une civilité (`M. Dupont`) | ✅ | ✅ |
| **Noms / organisations / lieux en texte libre** (sans titre, en prose) | ❌ | ✅ |
| Lexique de prénoms, heuristiques CamelCase/User-Agent/query-param | ❌ | ✅ |
| Taux de fuite mesuré | ~0 % sur les catégories déterministes | **0 %** sur 3 340+ valeurs, noms en texte libre inclus ([benchmark](https://tokenveil.eu/benchmark)) |
| Chat multi-providers, comptes locaux, admin, journal d'audit | ✅ | ✅ |
| Anonymisation de fichiers (joindre .docx/.xlsx/.pdf, OCR) | ❌ | ✅ |
| Auth LDAP / Active Directory + quotas de sièges multi-tenant | ❌ | ✅ |
| Licence / limite de sièges | Aucune (gratuit, jamais bloqué) | Sous licence |
| Support & licence | Autonome, ELv2 | Licence commerciale + support |

Le moteur Community est réellement utile et permet d'évaluer tout le produit. Le moteur Enterprise apporte la précision sur les cas réels difficiles (un nom de client noyé dans une stack trace, une organisation en prose). Il se branche derrière la même interface — rien d'autre ne change dans le code.

**Licence Enterprise / commerciale :** [contact@tokenveil.eu](mailto:contact@tokenveil.eu)

---

## Fonctionnement

**Les vraies valeurs ne franchissent jamais la frontière réseau vers le modèle.** La tokenisation se fait côté serveur, en mémoire, avant l'appel sortant. La détokenisation se fait après la réponse, aussi en mémoire. Le fournisseur ne voit que du texte tokenisé en entrée et en sortie.

### Facturation Claude par utilisateur (pas de clé API partagée)

Chaque utilisateur peut lier **son propre** abonnement Claude Pro/Max via un flux OAuth intégré. Le backend pilote `claude setup-token` (commande officielle du CLI Claude Code) via un pseudo-terminal, capture le token OAuth longue durée, et le stocke chiffré (Fernet) sur disque, isolé par utilisateur. Les prompts passent par l'abonnement de l'utilisateur, pas par une clé API partagée facturée. (Gemini, OpenAI, Mistral, Vertex, Bedrock se lient par clé API.)

### Transparence en direct

Pendant la frappe, l'UI montre en temps réel ce qui serait envoyé au modèle sous forme anonymisée. Chaque message envoyé a aussi un bouton « voir ce qui a été envoyé » révélant la charge tokenisée réelle qui a quitté le serveur. Rien de l'anonymisation n'est caché à l'utilisateur.

---

## Données au repos

- **Messages** : seule la version *anonymisée* est stockée. Le texte réel n'est jamais persisté en clair.
- **Mapping jeton ↔ valeur** : par conversation, chiffré Fernet au repos (clé `ANON_DB_KEY`). Déchiffré uniquement en mémoire, pour afficher la vue désanonymisée au propriétaire authentifié.
- **Identifiants des comptes liés** (tokens OAuth, clés API, mot de passe du compte de service LDAP) : chiffrés Fernet, permissions `600`, jamais journalisés.

## Authentification

- `AUTH_BACKEND=local` : comptes locaux (`WEBAPP_USERS` dans `.env`, ou créés depuis l'UI admin). Hachés PBKDF2.
- `AUTH_BACKEND=ldap` : bind+search sur votre LDAP/Active Directory existant, avec restriction de groupe optionnelle et quotas de sièges par groupe.

## Sécurité

Même l'édition Community embarque du durcissement de prod (voir [`middleware.py`](middleware.py)) : Content-Security-Policy stricte sans origine tierce, en-têtes anti-clickjacking, HSTS derrière TLS, et un limiteur de débit anti-abus. Les sessions sont des jetons aléatoires, le brute-force de connexion est limité par compte et par IP.

## Installation

**Docker (recommandé) :** voir [Essayer en 60 secondes](#essayer-en-60-secondes). Le dossier `./data` est le seul état à sauvegarder (base SQLite + mapping chiffré + comptes liés) ; c'est un volume monté, donc rebuilder pour mettre à jour le code n'y touche jamais.

**Sans Docker :**

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # renseignez ANON_DB_KEY, WEBAPP_USERS, AUTH_BACKEND
uvicorn app:app --host 0.0.0.0 --port 8500
```

Lier un abonnement Claude (pas les providers par clé API) nécessite en plus le CLI `claude` sur le `PATH` ; l'image Docker l'installe pour vous.

## Stack technique

| Couche | Choix |
|---|---|
| Backend | FastAPI (Python 3.12) |
| Frontend | Vanilla JS/HTML/CSS, sans étape de build |
| Anonymisation | **Community :** moteur regex sans dépendance · **Enterprise :** Presidio + spaCy NER + ML |
| Auth | Local (PBKDF2) ou LDAP/AD (`ldap3`) |
| Stockage | SQLite, Fernet (`cryptography`) au repos |
| Sécurité | CSP + en-têtes + rate limiting (`middleware.py`) |

## Licence

Code disponible sous [Elastic License 2.0](LICENSE). Vous pouvez lire, auditer, auto-héberger et modifier ce code. Vous ne pouvez **pas** l'offrir en service hébergé/managé à des tiers, ni contourner le système de licence. Licence de déploiement commercial et moteur Enterprise : [contact@tokenveil.eu](mailto:contact@tokenveil.eu).

---

*Voir [ARCHITECTURE.md](ARCHITECTURE.md) pour le design complet, et [tokenveil.eu/benchmark](https://tokenveil.eu/benchmark) pour la méthodologie de mesure de l'anonymisation.*

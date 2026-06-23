# TokenVeil : architecture complète

Document de référence interne (pas pour le client) : comment le produit fonctionne de bout en bout, fichier par fichier.

## 1. En une phrase

Proxy web self-hosted entre un employé et Claude/Gemini : anonymise les données sensibles AVANT l'envoi au LLM, désanonymise la réponse à l'affichage. Le LLM ne voit jamais la vraie donnée. Vendu en licence à des entreprises pour déploiement on-prem.

## 2. Schéma du flux d'un message

```
Utilisateur tape un message (données réelles)
        │
        ▼
[1] Auth (cookie de session) ─────────────────────── auth.py, app.py:_sessions
        │
        ▼
[2] Anonymisation (anon_engine.py)
    Presidio + spaCy NER + regex custom + CamelCase + UA sanctuarisé
    + query params décodés → texte avec tokens <TYPE_n>
        │
        ▼
[3] Stockage SQLite (db.py) : SEULE la version anonymisée est persistée
    mapping token↔valeur réelle chiffré Fernet (clé ANON_DB_KEY)
        │
        ▼
[4] Envoi au LLM (texte tokenisé uniquement)
    Claude  → claude_account.py (CLI `claude`, OAuth, abonnement perso)
    Gemini  → gemini_account.py (clé API perso, google-genai)
        │
        ▼
[5] Réponse reçue (toujours tokenisée, le LLM recopie les tokens tels quels)
        │
        ▼
[6] Désanonymisation en mémoire (jamais persistée en clair)
        │
        ▼
Affichage à l'utilisateur (données réelles restaurées)
```

La vraie donnée ne traverse JAMAIS le réseau vers Anthropic/Google. Elle reste en local (RAM + DB chiffrée).

## 3. Fichiers principaux

| Fichier | Rôle |
|---|---|
| `app.py` | FastAPI : toutes les routes HTTP, sessions, gate de licence, orchestration |
| `anon_engine.py` | Moteur d'anonymisation (le cœur du produit) |
| `auth.py` | Authentification : comptes locaux (DB) ou LDAP/AD |
| `db.py` | SQLite : conversations, messages, users, custom terms, settings, audit log |
| `claude_account.py` | Liaison OAuth Claude par utilisateur (CLI `claude setup-token`), exécution des prompts |
| `gemini_account.py` | Liaison clé API Gemini par utilisateur, exécution des prompts |
| `license.py` | Vérification + cycle de vie de la licence (ce dossier) |
| `static/index.html` | Frontend (chat + panel admin), vanilla JS, pas de build |
| `static/login.html` | Page de connexion |
| `proxy_cli.py` | Variante CLI hors webapp (legacy, clé API classique) |

## 4. Le moteur d'anonymisation (`anon_engine.py`)

**Module propriétaire, non inclus dans ce dépôt public.** Le fichier `anon_engine.py` présent ici est un stub qui expose la même interface (`AnonSession`, `get_analyzer`, `scan_coverage`) pour que le reste du code reste lisible, mais ne contient aucune logique de détection réelle.

Ce que le moteur réel fait, à haut niveau : combine Microsoft Presidio + spaCy NER (fr/en) avec des recognizers regex custom (secrets, IPs, IBAN, références métier...), des heuristiques tenant compte de la structure des logs, et plusieurs passes de réduction de faux positifs/négatifs, validées en continu par fuzzing aléatoire sur données synthétiques.

Implémentation complète disponible sous licence commerciale, contact contact@tokenveil.eu.

## 5. Authentification (`auth.py`)

Deux backends, bascule via `AUTH_BACKEND` (env ou UI admin, la DB prend le pas sur le fichier) :

- **local** : comptes dans la table `local_users` (mot de passe PBKDF2), créés depuis l'UI admin. Repli sur `WEBAPP_USERS`/`WEBAPP_USER` du `.env` si le compte n'existe pas en base (bootstrap).
- **ldap** : bind+search compatible OpenLDAP et Active Directory. Recherche le DN via un compte de service (ou anonyme), puis bind avec le mot de passe fourni. Le mot de passe ne quitte jamais le process, jamais stocké. Restriction optionnelle à un groupe (`LDAP_REQUIRE_GROUP_DN`), aussi utilisé pour le comptage de sièges (§7).

## 6. Données et stockage (`db.py`)

SQLite, fichier `data/anon.db` (volume Docker, seule donnée à sauvegarder).

| Table | Contenu |
|---|---|
| `conversations` | titre, propriétaire, favori |
| `messages` | **version anonymisée uniquement** (jamais le texte réel) |
| `custom_terms` | termes métier custom (verrouillables par l'admin, scope global ou par user) |
| `local_users` | comptes locaux (hash PBKDF2) |
| `user_roles` | admin/user par username (fonctionne pour LDAP aussi) |
| `app_settings` | config LDAP, backend auth (la DB prend le pas sur `.env`) |
| `user_preferences` | thème, avatar, langue, préférences UI |
| `audit_log` | qui/quand/combien de PII par catégorie (**jamais la valeur réelle ni le contenu**) |

Le mapping token↔valeur réelle est chiffré Fernet (clé `ANON_DB_KEY`, **à ne jamais perdre/régénérer sur une instance avec des données**, sinon mapping illisible pour toujours).

## 7. Système de licence

Architecture en 2 parties séparées : le **serveur de licences** (chez toi, vendeur) et le **module client** (`license.py`, embarqué dans chaque instance TokenVeil déployée chez un client).

### 7.1 Serveur de licences (`/home/kemar/docker/tokenveil-license-server/`)

Projet Docker à part, port 8700. Ed25519 : clé privée signe, clé publique (seule embarquée côté client) vérifie. Le client ne peut JAMAIS forger une licence.

- `keygen.py` : génère la paire de clés (une fois, à la création).
- `licensing.py` : signature des licences, DB SQLite (`licenses`, `verify_log`), logique de vérification.
- `app.py` : API REST (`/admin/licenses` CRUD, `/admin/licenses/{id}/download`, `/revoke`, `/reset-instance`, `/verify` public).
- `cli.py` : génération de licence en ligne de commande (`python cli.py create --customer X --seats N --days N`).

Schéma d'une licence (payload signé) :
```json
{"license_id": "LIC-...", "customer": "...", "max_seats": 5,
 "issued_at": "...", "expires_at": "..."}
```
Le `.lic` transmis au client = `base64(payload_json).base64(signature)`.

### 7.2 Module client (`license.py`, dans chaque instance déployée)

- **Vérification de signature** : avec la clé publique embarquée dans le code. Toute altération (ex : `max_seats` bidouillé) est détectée et rejetée, testé en pratique.
- **Stockage** : `data/license.lic` (survit aux redéploiements via le volume Docker) ou variable d'env `LICENSE_KEY`.
- **Phone-home** : tâche de fond (par défaut 1x/24h, `LICENSE_PHONE_HOME_INTERVAL`) qui appelle `/verify` sur le serveur de licences avec `license_id` + `instance_id` (UUID généré une fois, stocké dans `data/instance_id`).
- **Anti-duplication** : le serveur lie `license_id` ↔ `instance_id` à la 1ère vérification réussie. Si un AUTRE `instance_id` se présente ensuite (licence copiée sur un 2e serveur) → `instance_mismatch`. **2 confirmations consécutives** (pas un blip réseau ponctuel) → blocage immédiat, sans grace. Corrigé après avoir trouvé que l'ancienne logique donnait une grace infinie à une licence jamais validée avec succès.
- **Grace réseau** (`LICENSE_GRACE_PERIOD_DAYS`, 14j par défaut) : tolère un serveur de licences injoignable (panne réseau côté client). S'applique UNIQUEMENT à l'absence de réponse, jamais à un `instance_mismatch` confirmé.
- **Grace "pas de licence du tout"** (`LICENSE_NO_LICENSE_GRACE_DAYS`, 15j par défaut) : persisté sur disque (`data/no_license_since`), survit aux restarts. Passé ce délai → lockdown total (tout bloqué SAUF panel licence, `/api/account`, `/api/logout`) jusqu'à réinstallation d'une licence valide.

### 7.3 Comptage de sièges

- **Mode local** : nombre de comptes dans `local_users`.
- **Mode LDAP** : nombre de membres du groupe `LDAP_REQUIRE_GROUP_DN` (comptage live à chaque connexion, pas de cache). `None` si LDAP injoignable ou pas configuré → jamais traité comme un dépassement (pas de faux blocage sur une panne réseau).
- **Blocage** : à la connexion (`/api/login`), si le compte dépasse `max_seats` → 403 explicite. Les sessions déjà ouvertes ne sont pas coupées, seuls les nouveaux logins sont refusés.

### 7.4 Endpoints côté instance client

| Route | Usage |
|---|---|
| `GET /api/admin/license` | statut complet (admin) |
| `POST /api/admin/license` | installer/renouveler (valide la signature avant d'écrire, un token invalide ne casse pas l'existant) |
| `DELETE /api/admin/license` | retirer la licence de cette instance (migration propre vers un nouveau serveur, ne révoque rien côté vendeur, juste local) |

UI : onglet "Licence" dans le panel admin (statut, expiration, alerte <30j, formulaire d'installation, bouton de retrait avec confirmation).

## 8. Gate de licence (`app.py`)

`check_auth` (dépendance FastAPI utilisée par presque toutes les routes) vérifie, en plus de la session :
- Si `grace_exhausted()` (pas de licence valide ET grace de 15j dépassée) → 403 sur tout, SAUF `LICENSE_EXEMPT_PATHS` (`/api/admin/license`, `/api/account`, `/api/logout`) : pour qu'un admin puisse toujours atteindre le panel et réparer.
- Le frontend (HTML/JS/CSS) n'est jamais derrière ce gate (`StaticFiles` mount sans dépendance), toujours accessible, même en lockdown total.

## 9. Déploiement

- **Docker** (`Dockerfile`, `docker-compose.yml`) : image Python 3.12 + Node.js (pour le CLI `claude`) + modèles spaCy fr/en (~1 Go). `HEALTHCHECK` sur `/healthz`.
- **Volume `./data`** : SEULE donnée à sauvegarder (DB SQLite, comptes OAuth/clé API par utilisateur, licence, instance_id).
- **Reverse proxy** : nécessite `proxy_buffering off` + en-têtes spécifiques pour le streaming SSE du chat (sinon réponses livrées d'un bloc au lieu de progressivement).
- Voir `INSTALL.md` pour le runbook complet de déploiement client.

## 10. Ce qui n'est PAS encore fait (alpha)

- Isolation stricte des conversations par utilisateur (legacy/non-assignées visibles par tous)
- Liste d'autorisation/exclusion d'entités configurable par déploiement
- Modèle multi-tenant avec politiques d'accès par groupe LDAP
- UI graphique pour le serveur de licences (actuellement CLI + API JSON only ; `CONTEXT.md` du projet license-server prévu pour qu'un agent construise cette UI)

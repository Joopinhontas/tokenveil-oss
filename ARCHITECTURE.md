# TokenVeil : architecture complète

Document de référence interne (pas pour le client) : comment le produit fonctionne de bout en bout, fichier par fichier.

## 1. En une phrase

Proxy web self-hosted entre un employé et plusieurs LLM (Claude, Gemini, Vertex AI, Bedrock, OpenAI, Mistral) : anonymise les données sensibles AVANT l'envoi au LLM, désanonymise la réponse à l'affichage. Le LLM ne voit jamais la vraie donnée. Vendu en licence à des entreprises pour déploiement on-prem.

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
    (catégories désactivables par déploiement, voir §5)
        │
        ▼
[3] Stockage SQLite (db.py) : SEULE la version anonymisée est persistée
    mapping token↔valeur réelle chiffré Fernet (clé ANON_DB_KEY)
        │
        ▼
[4] Envoi au LLM (texte tokenisé uniquement), provider choisi par
    l'utilisateur dans le sélecteur de modèle, voir §9
        │
        ▼
[5] Réponse reçue (toujours tokenisée, le LLM recopie les tokens tels quels)
    dans la langue d'interface de l'utilisateur, voir §10
        │
        ▼
[6] Désanonymisation en mémoire (jamais persistée en clair)
        │
        ▼
Affichage à l'utilisateur (données réelles restaurées)
```

La vraie donnée ne traverse JAMAIS le réseau vers les fournisseurs de LLM. Elle reste en local (RAM + DB chiffrée).

## 3. Fichiers principaux

| Fichier | Rôle |
|---|---|
| `app.py` | FastAPI : toutes les routes HTTP, sessions, gate de licence, orchestration |
| `anon_engine.py` | Moteur d'anonymisation (le cœur du produit) |
| `auth.py` | Authentification : comptes locaux (DB) ou LDAP/AD, tenants multi-groupes |
| `db.py` | SQLite : conversations, messages, users, custom terms, settings, audit log, tenants LDAP |
| `claude_account.py` | Claude : OAuth/CLI (`claude setup-token`) **ou** clé API directe, une seule méthode active à la fois |
| `gemini_account.py` | Liaison clé API Gemini par utilisateur, exécution des prompts |
| `vertex_account.py` | Claude via Vertex AI (Google Cloud), credentials service account par utilisateur |
| `bedrock_account.py` | Claude via Amazon Bedrock, clés AWS par utilisateur |
| `openai_account.py` | OpenAI, clé API uniquement (pas d'équivalent OAuth, voir §9.3) |
| `mistral_account.py` | Mistral AI, clé API uniquement (même cas qu'OpenAI) |
| `license.py` | Vérification + cycle de vie de la licence |
| `static/index.html` | Frontend (chat + panel admin), vanilla JS, pas de build, i18n FR/EN intégré |
| `static/login.html` | Page de connexion, i18n autonome |
| `proxy_cli.py` | Variante CLI hors webapp (legacy, clé API classique) |

## 4. Le moteur d'anonymisation (`anon_engine.py`)

**Module propriétaire, non inclus dans ce dépôt public.** Le fichier `anon_engine.py` présent ici est un stub qui expose la même interface (`AnonSession`, `get_analyzer`, `scan_coverage`, `ENTITIES_OF_INTEREST`) pour que le reste du code reste lisible et importable, mais ne contient aucune logique de détection réelle.

Ce que le moteur réel fait, à haut niveau : combine Microsoft Presidio + spaCy NER (fr/en) avec des recognizers regex custom (secrets, IPs, IBAN, références métier...), des heuristiques tenant compte de la structure des logs, une liste d'autorisation/exclusion de catégories configurable par déploiement, et plusieurs passes de réduction de faux positifs/négatifs, validées en continu par fuzzing aléatoire sur données synthétiques.

Implémentation complète disponible sous licence commerciale, contact contact@tokenveil.eu.

## 5. Entités configurables par déploiement

Un admin peut désactiver des catégories natives entières (ex : "ne pas anonymiser les IP internes pour nous") sans toucher au code.

- **Stockage** : `db.get_disabled_entities()`/`set_disabled_entities()`, liste JSON dans `app_settings` (réglage global à l'instance, pas par utilisateur).
- **Application** : `AnonSession(disabled_entities=...)` calcule `active_entities` (filtre la liste passée à l'analyseur) ET filtre une seconde fois en sortie tous les résultats, parce que CamelCase/query-params taguent PERSON/LOCATION en dur indépendamment de `active_entities` : sans ce double filtre, désactiver PERSON ne l'aurait été que sur le chemin NER principal.
- **UI** : onglet admin "Entités", un toggle par catégorie (`pref-switch`, même composant que les préférences générales).
- Les termes custom métier ne sont jamais concernés (mécanisme séparé, scope par admin/utilisateur).

## 6. Authentification (`auth.py`)

Deux backends, bascule via `AUTH_BACKEND` (env ou UI admin, la DB prend le pas sur le fichier) :

- **local** : comptes dans la table `local_users` (mot de passe PBKDF2), créés depuis l'UI admin. Repli sur `WEBAPP_USERS`/`WEBAPP_USER` du `.env` si le compte n'existe pas en base (bootstrap).
- **ldap** : bind+search compatible OpenLDAP et Active Directory. Recherche le DN via un compte de service (ou anonyme), puis bind avec le mot de passe fourni. Le mot de passe ne quitte jamais le process, jamais stocké. Restriction optionnelle à un groupe global (`LDAP_REQUIRE_GROUP_DN`).

### 6.1 Multi-tenant LDAP (`db.list_ldap_tenants` / `auth.get_user_tenant`)

Plusieurs groupes LDAP peuvent coexister sur une seule instance, chacun avec sa propre sous-allocation de sièges, indépendante du plafond global de licence (ex : "Support : 10 sièges max sur les 50 licenciés").

- **CRUD** : table `ldap_tenants` (name, group_dn, max_seats), gérée depuis l'onglet LDAP de l'admin.
- **Résolution** : `auth.get_user_tenant(username)` résout le DN de l'utilisateur via le compte de service (sans son mot de passe, donc utilisable après coup), puis cherche dans quel groupe de tenant il apparaît (`member=<user_dn>`). Premier match dans l'ordre alphabétique des noms de tenant si appartenance à plusieurs groupes.
- **Comptage de sièges global** (`license.seats_used`) : si des tenants existent, somme les membres de chaque groupe de tenant au lieu du seul groupe global.
- **Blocage à la connexion** : en plus du plafond de licence global, un plafond par tenant est vérifié indépendamment (`/api/login`). `None` (LDAP injoignable) n'est jamais traité comme un dépassement, sur aucun des deux plafonds.

## 7. Données et stockage (`db.py`)

SQLite, fichier `data/anon.db` (volume Docker, seule donnée à sauvegarder). WAL activé (`PRAGMA journal_mode=WAL`) : les lecteurs ne bloquent plus les écrivains, nécessaire dès plusieurs utilisateurs actifs en même temps.

| Table | Contenu |
|---|---|
| `conversations` | titre, propriétaire, favori |
| `messages` | **version anonymisée uniquement** (jamais le texte réel) |
| `custom_terms` | termes métier custom (verrouillables par l'admin, scope global ou par user) |
| `local_users` | comptes locaux (hash PBKDF2) |
| `user_roles` | admin/user par username (fonctionne pour LDAP aussi) |
| `app_settings` | config LDAP, backend auth, entités désactivées (la DB prend le pas sur `.env`) |
| `user_preferences` | thème, avatar, **langue d'interface**, préférences UI |
| `audit_log` | qui/quand/combien de PII par catégorie (**jamais la valeur réelle ni le contenu**) |
| `ldap_tenants` | nom, DN de groupe, sièges max (voir §6.1) |

Le mapping token↔valeur réelle est chiffré Fernet (clé `ANON_DB_KEY`, **à ne jamais perdre/régénérer sur une instance avec des données**, sinon mapping illisible pour toujours).

## 8. Système de licence

Architecture en 2 parties séparées : le **serveur de licences** (chez toi, vendeur) et le **module client** (`license.py`, embarqué dans chaque instance TokenVeil déployée chez un client).

### 8.1 Serveur de licences (`tokenveil-license-server/`)

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

### 8.2 Module client (`license.py`, dans chaque instance déployée)

- **Vérification de signature** : avec la clé publique embarquée dans le code. Toute altération (ex : `max_seats` bidouillé) est détectée et rejetée, testé en pratique.
- **Stockage** : `data/license.lic` (survit aux redéploiements via le volume Docker) ou variable d'env `LICENSE_KEY`.
- **Phone-home** : tâche de fond périodique qui appelle `/verify` sur le serveur de licences avec `license_id` + `instance_id` (UUID généré une fois, stocké dans `data/instance_id`).
- **Anti-duplication** : le serveur lie `license_id` ↔ `instance_id` à la 1ère vérification réussie. Si un AUTRE `instance_id` se présente ensuite (licence copiée sur un 2e serveur) → `instance_mismatch`. Plusieurs confirmations consécutives (pas un blip réseau ponctuel) → blocage immédiat, sans grace.
- **Grace réseau** : tolère un serveur de licences injoignable (panne réseau côté client). S'applique UNIQUEMENT à l'absence de réponse, jamais à un `instance_mismatch` confirmé.
- **Grace "pas de licence du tout"** : persisté sur disque, survit aux restarts. Passé ce délai → lockdown total (tout bloqué SAUF panel licence, `/api/account`, `/api/logout`) jusqu'à réinstallation d'une licence valide.

### 8.3 Comptage de sièges

- **Mode local** : nombre de comptes dans `local_users`.
- **Mode LDAP** : voir §6.1 (somme par tenant, ou groupe global si pas de tenant configuré).
- **Blocage** : à la connexion, si le compte dépasse `max_seats` → 403 explicite. Les sessions déjà ouvertes ne sont pas coupées, seuls les nouveaux logins sont refusés.

### 8.4 Endpoints côté instance client

| Route | Usage |
|---|---|
| `GET /api/admin/license` | statut complet (admin) |
| `POST /api/admin/license` | installer/renouveler (valide la signature avant d'écrire, un token invalide ne casse pas l'existant) |
| `DELETE /api/admin/license` | retirer la licence de cette instance (migration propre vers un nouveau serveur, ne révoque rien côté vendeur, juste local) |

UI : onglet "Licence" dans le panel admin (statut, expiration, alerte <30j, formulaire d'installation, bouton de retrait avec confirmation).

## 9. Providers IA

Six providers possibles, sélectionnés par utilisateur dans le sélecteur de modèle du chat. Seuls les providers réellement liés apparaissent dans la liste (`updateModelMenuVisibility`, front) : pas de modèle cliquable mais inutilisable.

### 9.1 Claude : OAuth/CLI ou clé API

`claude_account.py` gère les deux méthodes, une seule active à la fois (lier l'une retire l'autre automatiquement) :
- **OAuth/CLI** (historique, inchangé) : `claude setup-token` piloté via pty, token stocké chiffré, exécution via le CLI `claude -p` avec continuité de session serveur (`--resume`).
- **Clé API** : SDK `anthropic` direct, sans état (historique reconstruit à chaque appel comme Gemini), pour les comptes facturés à l'usage plutôt qu'un abonnement Pro/Max.

### 9.2 Claude via cloud d'entreprise (Vertex AI / Bedrock)

`vertex_account.py` / `bedrock_account.py` : même Claude, facturé sur le compte cloud GCP/AWS de l'utilisateur plutôt qu'un abonnement personnel ou une clé API Anthropic directe. Utilisent les clients `anthropic.AnthropicVertex`/`AnthropicBedrock` du SDK officiel (même API Messages que le client direct, juste l'auth qui change : credentials service account GCP, ou access/secret key AWS). Liaison par utilisateur, validée par un vrai appel avant stockage chiffré.

### 9.3 Gemini, OpenAI, Mistral : clé API uniquement

Aucun équivalent à `claude setup-token` chez ces fournisseurs : abonnement consumer et facturation API sont des systèmes séparés, pas de mécanisme public pour faire passer l'un par l'autre.
- **Gemini** : deux pistes OAuth explorées et abandonnées (CLI officiellement coupé, Antigravity non-déterministe en test), détail dans `gemini_account.py`.
- **OpenAI** : Codex CLI a un OAuth, mais scopé au produit Codex, pas réutilisable pour un usage tiers général.
- **Mistral** : même séparation abonnement Le Chat Pro / facturation API.

Les trois suivent le même pattern stateless (`STATELESS_PROVIDERS` dans `app.py`) : historique reconstruit et réinjecté à chaque appel.

## 10. Internationalisation

Interface FR/EN complète (300 clés, parité vérifiée), pensée pour accueillir d'autres langues sans changement structurel.

- **Dictionnaire** : objet `I18N` dans `static/index.html`, clé → `{fr, en}`. `data-i18n`/`data-i18n-placeholder` sur le HTML statique, `t(key)` dans le JS pour le contenu généré dynamiquement.
- **Persistance** : préférence `language` dans `user_preferences` (par utilisateur), lue/écrite via `/api/preferences`. Cache `localStorage` pour un premier rendu instantané avant confirmation serveur.
- **Réponse de l'IA dans la langue de l'utilisateur** : `app.py:build_system_prompt()` ajoute une directive de langue lue depuis la préférence, indépendamment de la langue du message envoyé. `LANGUAGE_NAMES` (une ligne par langue) est le seul endroit à toucher pour en ajouter une.
- **login.html** : i18n autonome (dictionnaire séparé, sélecteur custom dans le coin), accessible avant authentification.

## 11. Déploiement

- **Docker** (`Dockerfile`, `docker-compose.yml`) : image Python 3.12 + Node.js (pour le CLI `claude`) + modèles spaCy fr/en (~1 Go). `HEALTHCHECK` sur `/healthz`.
- **Volume `./data`** : SEULE donnée à sauvegarder (DB SQLite, comptes/clés par utilisateur et par provider, licence, instance_id).
- **Reverse proxy** : nécessite `proxy_buffering off` + en-têtes spécifiques pour le streaming SSE du chat (sinon réponses livrées d'un bloc au lieu de progressivement).
- Voir `INSTALL.md` pour le runbook complet de déploiement client.

## 12. Ce qui n'est PAS encore fait (alpha)

- Liste d'allow/deny pour les nouveaux providers (Vertex/Bedrock/OpenAI/Mistral) : actuellement le filtre §5 ne s'applique qu'au moteur d'anonymisation, pas à un éventuel contrôle "quels providers un déploiement autorise".
- Tests réels contre de vrais comptes GCP/AWS pour Vertex/Bedrock (validés uniquement par gestion d'erreur sur credentials invalides, pas de succès end-to-end vérifié faute de compte cloud disponible).

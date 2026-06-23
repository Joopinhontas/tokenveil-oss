# TokenVeil : guide d'installation chez un client

Ce document décrit le déploiement complet d'TokenVeil chez un client, via Docker. Il est pensé pour
être suivi de bout en bout sans connaissance préalable du projet.

---

## 1. Prérequis côté client

- Un serveur Linux (physique, VM ou cloud) avec Docker et Docker Compose v2 installés
- 4 Go de RAM minimum (les modèles NLP fr/en chargés en mémoire au démarrage en consomment une bonne
  partie), 4 vCPU recommandés
- ~3 Go d'espace disque pour l'image (modèles spaCy inclus)
- Un port HTTP disponible (8500 par défaut, configurable)
- Si exposition publique : un nom de domaine et un reverse proxy devant le service (voir §7)
- Si authentification LDAP/Active Directory : accès réseau au contrôleur de domaine depuis le serveur

Aucune dépendance à installer à la main sur le serveur : Python, Node.js et le CLI Claude Code sont tous
embarqués dans l'image Docker.

## 2. Récupération du projet

```bash
git clone <url-du-repo> tokenveil
cd tokenveil
```

(Ou transfert du dossier par un autre moyen si pas de dépôt Git accessible côté client.)

## 3. Configuration (`.env`)

```bash
cp .env.example .env
```

Ouvrir `.env` et renseigner :

| Variable | Rôle | Comment l'obtenir |
|---|---|---|
| `ANON_DB_KEY` | Clé de chiffrement des données au repos (mapping anonymisation, tokens OAuth) | `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `AUTH_BACKEND` | `local` ou `ldap` | Selon l'infra du client |
| `WEBAPP_USERS` | Comptes locaux bootstrap, format `user:motdepasse,user2:motdepasse2` | À définir si `AUTH_BACKEND=local` |
| `LDAP_*` | Config annuaire | Voir les exemples commentés dans `.env.example` si `AUTH_BACKEND=ldap` |
| `ANON_LANGUAGE` | Langue par défaut de détection (`fr`/`en`) | Selon la langue des logs/données du client |
| `ANON_PORT` | Port d'écoute exposé sur l'hôte | `8500` par défaut, à changer si déjà pris |

**Point d'attention sécurité** : `ANON_DB_KEY` ne doit jamais être perdue ni régénérée sur une instance qui
contient déjà des données. Sans elle, le mapping anonymisation devient illisible et les conversations
existantes ne peuvent plus être désanonymisées à l'affichage. La sauvegarder dans un coffre-fort
(password manager d'entreprise), pas seulement dans le `.env` sur le serveur.

## 4. Premier démarrage

```bash
docker compose up -d --build
```

Premier build : plusieurs minutes (téléchargement des modèles NLP, ~1 Go). Les builds suivants (mise à
jour de code) sont rapides grâce au cache Docker.

Vérifier que le service répond :

```bash
curl http://localhost:8500/healthz
# {"status": "ok"}
```

Si `ANON_PORT` a été changé dans `.env`, adapter l'URL ci-dessus en conséquence.

## 5. Premier accès et création des comptes

- Ouvrir `http://<serveur>:<port>/` (ou le domaine public si déjà en place, voir §7)
- Se connecter avec un des comptes définis dans `WEBAPP_USERS` (le premier compte créé devient
  automatiquement admin)
- Dans **Préférences > Comptes IA**, chaque utilisateur lie sa propre IA :
  - **Claude** : OAuth, abonnement Pro/Max personnel, aucune clé API facturée
  - **Gemini** : clé API personnelle générée sur aistudio.google.com, gratuite sur les modèles Flash
- Pour ajouter d'autres comptes après coup : panneau **Administration** (visible uniquement pour les
  comptes admin) > onglet Utilisateurs

## 6. Mots-clés métier du client (optionnel mais recommandé)

Dans **Préférences > Mots-clés à anonymiser**, ajouter les codenames, noms de projets internes ou
identifiants propres au client que la détection générique ne peut pas connaître à l'avance. Un admin peut
déployer ces règles à toute l'équipe ou à un utilisateur précis depuis le panneau Administration.

## 7. Exposition publique derrière un reverse proxy

TokenVeil utilise du **streaming SSE** (réponse de l'IA affichée au fur et à mesure). La plupart des
reverse proxy bufferisent les réponses par défaut, ce qui casse cet effet (la réponse arrive d'un bloc à
la fin au lieu d'être progressive). Ajouter ces directives sur le bloc qui pointe vers TokenVeil :

**Nginx (bloc `location` dédié, ou config personnalisée Nginx Proxy Manager) :**
```nginx
proxy_buffering off;
proxy_cache off;
proxy_set_header Connection '';
proxy_http_version 1.1;
chunked_transfer_encoding off;
proxy_read_timeout 300s;
```

Sans ça, l'application reste fonctionnelle mais perd l'effet de streaming. Pas un bug du logiciel, un
comportement par défaut du proxy à désactiver explicitement.

Forcer HTTPS (`Force SSL`) est fortement recommandé : les identifiants de connexion et le contenu des
échanges (même anonymisés côté IA) transitent en clair sur le réseau sinon.

## 8. Sauvegarde et mise à jour

**Sauvegarde** : un seul dossier à sauvegarder, `./data` (monté en volume Docker). Il contient la base
SQLite (conversations, mapping chiffré) et les comptes IA liés par utilisateur. Sauvegarde simple :

```bash
tar -czf tokenveil-backup-$(date +%F).tar.gz data/
```

**Mise à jour du code** (nouvelle version livrée) :

```bash
git pull   # ou remplacement des fichiers
docker compose up -d --build
```

Le dossier `./data` n'est jamais touché par un rebuild : les comptes liés et l'historique survivent à la
mise à jour.

## 9. Checklist sécurité avant mise en production

- [ ] `ANON_DB_KEY` générée spécifiquement pour ce client, sauvegardée hors du serveur
- [ ] HTTPS actif si exposition au-delà du réseau local
- [ ] Mots de passe `WEBAPP_USERS` changés depuis les valeurs par défaut de `.env.example`
- [ ] `AUTH_BACKEND=ldap` configuré si le client a déjà un annuaire d'entreprise (évite la gestion de
  mots de passe en doublon)
- [ ] Accès réseau au port exposé restreint si pas d'exposition publique voulue (firewall/VPN)
- [ ] Sauvegarde de `./data` planifiée (cron, ou intégrée à la politique de backup existante du client)

## 10. Dépannage rapide

| Symptôme | Cause probable | Solution |
|---|---|---|
| `docker compose up` échoue, port déjà utilisé | Un autre service occupe le port | Changer `ANON_PORT` dans `.env` |
| Healthcheck reste `unhealthy` | Le serveur met du temps à charger les modèles NLP au premier démarrage | Attendre ~30-60s, revérifier avec `docker ps` |
| Streaming pas fluide derrière un reverse proxy | Buffering proxy activé par défaut | Voir §7 |
| Page "Congratulations" Nginx au lieu de l'app | Domaine pas associé au bon Proxy Host | Vérifier que le nom de domaine est bien dans les "Domain Names" du host visé |
| Liaison Claude échoue | CLI `claude` indisponible | Vérifier `docker exec <conteneur> claude --version` (doit répondre une version) |
| Un utilisateur ne voit pas le panneau Administration | Rôle non-admin | Promouvoir depuis Administration > Utilisateurs (par un admin existant) |

---

Pour le détail technique complet (architecture, choix de design, ce qui est encore en alpha), voir
`README.fr.md`.

# Installer TokenVeil (édition Community)

Déploiement complet via Docker, pensé pour être suivi de bout en bout sans connaissance préalable du projet. Pour régler les options (authentification, providers, mots-clés, reverse proxy...), voir [CONFIG.md](CONFIG.md).

---

## 1. Prérequis

- Un serveur Linux (physique, VM ou cloud) avec **Docker** et **Docker Compose v2**.
- **1 à 2 Go de RAM** suffisent : le moteur de l'édition Community est léger, aucun modèle à charger.
- ~1 Go d'espace disque pour l'image.
- Un port HTTP libre (**8500** par défaut, configurable).
- Si exposition publique : un nom de domaine et un reverse proxy devant le service (voir [CONFIG.md](CONFIG.md)).

Aucune dépendance à installer à la main : tout est embarqué dans l'image Docker.

## 2. Récupérer le projet

```bash
git clone https://github.com/Joopinhontas/tokenveil-oss.git tokenveil
cd tokenveil
```

## 3. Configuration minimale

```bash
cp .env.example .env
```

Renseigner au minimum deux valeurs dans `.env` :

| Variable | Rôle | Comment l'obtenir |
|---|---|---|
| `ANON_DB_KEY` | Clé de chiffrement des données au repos | `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `WEBAPP_USERS` | Compte(s) de connexion, format `user:motdepasse` | À définir vous-même |

**Important** : `ANON_DB_KEY` ne doit **jamais** être perdue ni régénérée sur une instance qui contient déjà des données (sinon les conversations existantes ne peuvent plus être désanonymisées). Sauvegardez-la dans un gestionnaire de secrets, pas seulement dans le `.env`.

Toutes les autres options sont détaillées dans [CONFIG.md](CONFIG.md).

## 4. Démarrer

```bash
docker compose up -d --build
```

Le build est **léger et rapide** (aucun modèle à télécharger). Vérifier que le service répond :

```bash
curl http://localhost:8500/healthz
# {"status": "ok"}
```

## 5. Premier accès

- Ouvrir `http://<serveur>:8500/`.
- Se connecter avec un compte défini dans `WEBAPP_USERS` (le premier devient automatiquement administrateur).
- Dans **Préférences > Comptes IA**, lier une IA. Le plus rapide pour tester : une **clé API Gemini** gratuite (aistudio.google.com). Claude, OpenAI, Mistral et les clouds d'entreprise sont aussi disponibles.
- Coller un texte contenant des IP, e-mails, clés API... et observer l'anonymisation avant envoi, puis la restauration dans la réponse.

## 6. Sauvegarde et mise à jour

Un seul dossier à sauvegarder : **`./data`** (volume Docker), qui contient la base de données et les comptes IA liés.

```bash
tar -czf tokenveil-backup-$(date +%F).tar.gz data/
```

Mise à jour du code :

```bash
git pull
docker compose up -d --build
```

Le dossier `./data` n'est jamais touché par un rebuild : l'historique et les comptes liés survivent.

## 7. Dépannage rapide

| Symptôme | Cause probable | Solution |
|---|---|---|
| `docker compose up` échoue, port déjà utilisé | Port occupé | Changer `ANON_PORT` dans `.env` |
| Streaming de la réponse pas fluide derrière un proxy | Buffering du reverse proxy | Voir la section reverse proxy de [CONFIG.md](CONFIG.md) |
| Liaison Claude échoue | Interface CLI Claude indisponible | Utilisez plutôt un provider par clé API (Gemini, OpenAI...) pour démarrer |
| Un utilisateur ne voit pas l'Administration | Rôle non-admin | Le promouvoir depuis Administration > Utilisateurs |

---

Pour aller plus loin : [CONFIG.md](CONFIG.md) (configuration), [SECURITY.md](SECURITY.md) (sécurité), [ARCHITECTURE.md](ARCHITECTURE.md) (vue d'ensemble).

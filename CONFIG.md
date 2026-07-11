# Configurer TokenVeil

Toutes les options se règlent soit dans le fichier `.env` (au déploiement), soit depuis l'interface (Préférences et Administration). Ce document couvre les deux. Pour l'installation initiale, voir [INSTALL.md](INSTALL.md).

---

## 1. Variables d'environnement (`.env`)

| Variable | Rôle | Défaut |
|---|---|---|
| `ANON_DB_KEY` | Clé de chiffrement des données au repos (**obligatoire**, à sauvegarder) | *(aucun)* |
| `WEBAPP_USERS` | Comptes locaux, format `user:motdepasse,user2:motdepasse2` | *(aucun)* |
| `AUTH_BACKEND` | Méthode d'authentification : `local` | `local` |
| `ANON_LANGUAGE` | Langue de détection par défaut (`fr` / `en`) | `fr` |
| `ANON_PORT` | Port exposé sur l'hôte | `8500` |
| `COOKIE_SECURE` | Cookie de session en HTTPS uniquement + HSTS (mettre `true` derrière TLS) | `false` |

> L'authentification **LDAP / Active Directory** (avec restriction par groupe et quotas de sièges multi-tenant) est une fonctionnalité de l'édition **Enterprise**.

## 2. Lier une IA (par utilisateur)

Chaque utilisateur lie sa propre IA dans **Préférences > Comptes IA**. Aucune clé partagée : chacun utilise son accès.

- **Gemini / OpenAI / Mistral** : clé API personnelle ou d'entreprise (le plus simple pour démarrer : une clé Gemini gratuite sur aistudio.google.com).
- **Claude** : abonnement Pro/Max via connexion, ou clé API.
- **Vertex AI, Amazon Bedrock, Azure OpenAI, GitHub Models** : identifiants du cloud d'entreprise correspondant.

La liste des modèles proposés dans le sélecteur se **met à jour automatiquement** : les nouveaux modèles apparaissent, ceux retirés disparaissent, sans intervention.

## 3. Mots-clés métier à anonymiser

Dans **Préférences > Mots-clés à anonymiser**, ajoutez les noms de projets internes, codenames ou identifiants propres à votre organisation que la détection générique ne peut pas deviner. Un administrateur peut déployer ces règles à toute l'équipe ou à un utilisateur précis depuis le panneau **Administration**.

## 4. Catégories de données détectées

Dans **Administration > Entités**, un administrateur peut **désactiver** des catégories entières (par exemple ne pas anonymiser les adresses IP internes pour un déploiement donné). Utile pour adapter le niveau de masquage au contexte.

## 5. Gestion des comptes et rôles

Dans **Administration > Utilisateurs** (visible pour les comptes admin) : créer des comptes locaux, promouvoir un utilisateur en administrateur, retirer un accès. Le premier compte créé au démarrage est automatiquement administrateur.

## 6. Journal d'audit

**Administration > Journal d'audit** : trace, pour chaque message, **quelle catégorie** de donnée a été masquée et **combien de fois**, jamais la valeur réelle. Utile pour prouver à un responsable conformité que l'anonymisation a bien lieu, sans recréer de risque de fuite.

## 7. Exposition publique derrière un reverse proxy

TokenVeil diffuse la réponse de l'IA **en flux** (au fur et à mesure). La plupart des reverse proxy bufferisent les réponses par défaut, ce qui casse cet effet. Sur le bloc qui pointe vers TokenVeil (exemple Nginx) :

```nginx
proxy_buffering off;
proxy_cache off;
proxy_http_version 1.1;
proxy_read_timeout 300s;
```

Activez aussi **HTTPS** (et passez `COOKIE_SECURE=true`) dès que le service est exposé au-delà du réseau local : les identifiants de connexion transitent en clair sinon.

## 8. Checklist avant mise en production

- [ ] `ANON_DB_KEY` générée pour ce déploiement et sauvegardée hors du serveur.
- [ ] `COOKIE_SECURE=true` et HTTPS actifs si exposition au-delà du réseau local.
- [ ] Mots de passe `WEBAPP_USERS` différents des exemples du `.env.example`.
- [ ] Accès réseau au port restreint si pas d'exposition publique voulue (firewall/VPN).
- [ ] Sauvegarde de `./data` planifiée.

---

Voir aussi : [SECURITY.md](SECURITY.md) (mesures de sécurité) et [ARCHITECTURE.md](ARCHITECTURE.md) (vue d'ensemble).

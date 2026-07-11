# TokenVeil, vue d'ensemble de l'architecture

Ce document explique **l'idée** et les principes de conception de TokenVeil. Il ne détaille pas l'implémentation interne. Pour installer : [INSTALL.md](INSTALL.md). Pour configurer : [CONFIG.md](CONFIG.md). Pour les aspects sécurité/conformité : [SECURITY.md](SECURITY.md).

## En une phrase

TokenVeil est un proxy auto-hébergé placé entre vos équipes et les modèles d'IA (Claude, Gemini, OpenAI, Mistral et clouds d'entreprise). Il **anonymise les données sensibles avant qu'un message n'atteigne le modèle**, puis restaure les vraies valeurs dans la réponse affichée. La donnée réelle ne quitte jamais votre infrastructure.

## Le flux d'un message

```
Utilisateur (données réelles)
        │
        ▼
[1] Authentification (comptes locaux ou LDAP/AD)
        │
        ▼
[2] Anonymisation  →  les données sensibles deviennent des jetons neutres
        │              (<PERSON_1>, <IP_ADDRESS_2>, <API_SECRET_3>...)
        ▼
[3] Envoi au modèle  →  SEUL le texte tokenisé sort du réseau
        │
        ▼
[4] Réponse du modèle  →  le modèle recopie les jetons tels quels
        │
        ▼
[5] Désanonymisation en mémoire  →  les vraies valeurs sont restaurées
        │
        ▼
Affichage à l'utilisateur (réponse lisible, données réelles)
```

Points clés :

- La tokenisation se fait **côté serveur, avant l'appel sortant**. La détokenisation se fait **après la réponse, en mémoire**. Le fournisseur d'IA ne voit que des jetons, en entrée comme en sortie.
- L'anonymisation est **réversible côté client uniquement** : la correspondance jeton ↔ valeur réelle ne quitte jamais votre process, et n'est jamais transmise au modèle.
- Les messages conservés ne contiennent **que la version anonymisée**. La donnée réelle n'est jamais stockée en clair.

## Le moteur d'anonymisation

Le moteur détecte les données sensibles et les remplace par des jetons typés (le type est conservé : `<PERSON_1>`, `<IBAN_CODE_2>`), de sorte que le modèle comprend **de quoi on parle** sans jamais voir la vraie valeur. Une même valeur reçoit toujours le même jeton, ce qui préserve les relations pour le modèle.

Catégories couvertes : noms de personnes, e-mails, téléphones, adresses IP (publiques et internes), adresses MAC, IBAN, cartes bancaires, numéros nationaux (dont le NIR français), plaques d'immatriculation, clés API et secrets techniques, montants financiers, identifiants de log, références clients, et **termes métier propres à votre organisation** ajoutés en configuration.

L'organisation qui déploie peut **désactiver certaines catégories** selon son besoin.

### Deux éditions, une seule interface

TokenVeil se décline en deux éditions qui partagent **le même produit** (interface, authentification, providers, stockage, déploiement) et ne diffèrent que par le moteur de détection :

- **Community** (ce dépôt, gratuit) : moteur déterministe couvrant les catégories à haute confiance ci-dessus. Suffit pour évaluer le produit de bout en bout.
- **Enterprise** (licence commerciale) : ajoute la détection avancée de noms, organisations et lieux **en texte libre** (NER + apprentissage automatique), plus l'anonymisation de fichiers (documents bureautiques, PDF, OCR) et l'intégration annuaire d'entreprise. Le moteur avancé se branche derrière la même interface.

La qualité de l'anonymisation est **mesurée** (jeu de test annoté + fuzzing aléatoire) et publiée : voir [tokenveil.eu/benchmark](https://tokenveil.eu/benchmark).

## Authentification

Deux modes, au choix du déploiement :

- **Comptes locaux** : gérés dans l'application (mots de passe hachés). Suffit pour un test ou une petite équipe.
- **LDAP / Active Directory** (Enterprise) : les employés s'authentifient via l'annuaire existant de l'entreprise, avec restriction par groupe et quotas de sièges.

## Données et confidentialité

- Tout tourne sur **votre** infrastructure (serveur on-premise ou cloud souverain). Rien n'est hébergé par l'éditeur.
- Les éléments sensibles au repos (correspondance d'anonymisation, identifiants des comptes IA liés) sont **chiffrés**.
- **Aucune télémétrie** sur le contenu : le seul flux sortant éventuel est une vérification de licence qui ne transporte aucune donnée personnelle.

Le détail des mesures de sécurité est dans [SECURITY.md](SECURITY.md).

## Providers d'IA supportés

Claude (via abonnement Pro/Max ou clé API), Gemini, OpenAI, Mistral, ainsi que les déploiements cloud d'entreprise : Vertex AI (Google Cloud), Amazon Bedrock, Azure OpenAI, GitHub Models. L'utilisateur choisit son modèle dans l'interface ; la liste des modèles disponibles se met à jour automatiquement.

## Déploiement

Conteneur Docker unique, `docker compose up`. Le frontend est servi tel quel (aucune étape de build). Un seul dossier de données à sauvegarder (base de données + configuration). Voir [INSTALL.md](INSTALL.md).

## Licence

Le code de cette édition Community est disponible sous **Elastic License 2.0** (lecture, audit, auto-hébergement, modification autorisés ; interdiction d'en faire un service hébergé pour des tiers). Les fonctionnalités **Enterprise** (moteur ML, fichiers, LDAP/AD, multi-tenant) et le déploiement commercial nécessitent une **licence commerciale** : [contact@tokenveil.eu](mailto:contact@tokenveil.eu).

## Statut

Alpha. Le mécanisme central est validé de bout en bout et mesuré. Un audit de sécurité tiers fait partie de la feuille de route avant un déploiement à grande échelle.

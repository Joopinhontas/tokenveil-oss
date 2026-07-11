# Sécurité et confidentialité

Ce document décrit les **principes de sécurité** de TokenVeil, à destination d'un DSI, RSSI ou DPO qui évalue le produit. Il présente ce qui est en place et ce qui reste à la charge de l'organisation, sans détailler l'implémentation.

---

## 1. Le principe central : la donnée ne sort pas

TokenVeil remplace les données sensibles par des jetons neutres **avant** qu'un message n'atteigne le fournisseur d'IA, et restaure les vraies valeurs **après** la réponse, en local. Concrètement :

- Le fournisseur d'IA ne reçoit que du texte tokenisé, en entrée comme en sortie.
- La correspondance entre un jeton et sa valeur réelle **ne quitte jamais votre process** et n'est jamais transmise au modèle.
- Les messages conservés ne contiennent que la version **anonymisée** : la donnée réelle n'est jamais stockée en clair.

Au sens du RGPD, il s'agit d'une **pseudonymisation** appliquée par défaut, côté client. Le fournisseur d'IA ne peut pas ré-identifier les données qu'il reçoit.

## 2. Auto-hébergement et absence de tiers

- Tout s'exécute sur **votre** infrastructure (serveur on-premise ou cloud souverain de votre choix). Aucune donnée n'est hébergée par l'éditeur.
- **Aucune télémétrie** sur le contenu ou l'usage. Le seul flux réseau sortant éventuel est une vérification de licence, qui ne transporte **aucune donnée personnelle**.
- L'éditeur n'ajoute **aucun sous-traitant** dans votre chaîne de traitement.

## 3. Chiffrement au repos

Les éléments sensibles stockés sont chiffrés :

- la correspondance d'anonymisation (jeton ↔ valeur réelle) ;
- les identifiants des comptes d'IA liés par chaque utilisateur.

La clé de chiffrement est propre à votre déploiement et reste sous votre contrôle.

## 4. Authentification et contrôle d'accès

- Comptes locaux avec **mots de passe hachés** selon les recommandations en vigueur, ou intégration à votre **annuaire d'entreprise** (LDAP / Active Directory, édition Enterprise).
- **Anti-force-brute** sur la connexion, par compte et par adresse.
- Séparation des rôles administrateur / utilisateur ; cloisonnement strict entre utilisateurs (personne ne voit les conversations d'un autre).

## 5. Durcissement applicatif

- **En-têtes de sécurité HTTP** stricts sur chaque réponse, dont une politique de contenu (CSP) **sans aucune origine tierce** : toutes les ressources sont servies localement, ce qui permet un fonctionnement en environnement isolé (air-gapped) et supprime le risque d'injection via un CDN externe.
- **Limitation de débit** anti-abus.
- Exécution du conteneur en **utilisateur non privilégié** (non-root).
- Chiffrement du canal (**HTTPS/TLS**) à activer côté déploiement dès toute exposition réseau.

## 6. Auditabilité

Un **journal d'audit** enregistre, pour chaque message, la catégorie et le nombre de données masquées, **jamais la valeur réelle**. Il permet de démontrer que l'anonymisation a bien lieu, sans recréer de risque de fuite dans le journal lui-même.

## 7. Qualité de l'anonymisation, mesurée

Le taux de fuite (proportion de données sensibles encore lisibles après traitement) est **mesuré** : jeu de test annoté + fuzzing aléatoire (des milliers de cas générés différemment à chaque exécution). Un vérificateur indépendant recontrôle que rien ne survit dans le texte anonymisé. Méthodologie publique et reproductible : [tokenveil.eu/benchmark](https://tokenveil.eu/benchmark).

**Limite assumée** : aucun système de détection n'est parfait. Un nom propre inconnu dans un contexte ambigu peut théoriquement échapper à la détection. TokenVeil réduit fortement le risque, sans le supprimer contractuellement à 100 %.

## 8. Ce qui reste à votre charge

- La sécurité physique et logique de l'infrastructure qui héberge TokenVeil (elle reste la vôtre).
- L'activation de HTTPS/TLS et la restriction réseau (firewall/VPN) selon votre exposition.
- La sauvegarde et la conservation des données selon votre politique.
- La relation contractuelle et le DPA éventuels avec le fournisseur d'IA que vous choisissez.

## 9. Statut

Alpha. Le mécanisme central est validé et mesuré. Un **audit de sécurité tiers / test d'intrusion** fait partie de la feuille de route avant un déploiement à grande échelle. Nous pouvons répondre à un questionnaire sécurité fournisseur et signer un NDA standard.

---

Contact sécurité / conformité : [contact@tokenveil.eu](mailto:contact@tokenveil.eu)

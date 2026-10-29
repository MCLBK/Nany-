# Rapport de sécurité — NANYÉ

Date : 29 octobre 2026 · Périmètre : API `backend/` et intégration du site statique.

Ce rapport décrit les protections mises en place et les contrôles réellement exécutés.
Il ne constitue **ni une certification OWASP ni une garantie d'absence de vulnérabilités**.

## 1. Contrôles exécutés

| Contrôle | Résultat |
|---|---|
| `pytest` — 76 tests fonctionnels et de sécurité | **76 réussis, 0 échec** |
| `bandit -r app` (1 920 lignes, aucune exclusion `#nosec`) | **Aucun problème signalé** |
| `pip-audit -r requirements-dev.txt` | **Aucune vulnérabilité connue** |
| Parcours complet sur serveur réel (visiteur, 3 clientes, paiements simulés, équipe, admin) | **19 étapes réussies** |
| Vérification des migrations (montée, descente, `alembic check`) sous SQLite | **OK** |
| OWASP ZAP Baseline | **Non exécuté** (ZAP indisponible sur la machine) — commande dans le README |
| Migrations et tests sur PostgreSQL | **Non exécutés** (aucun serveur disponible) |
| Intégration CinetPay réelle | **Non testée** (pas d'identifiants) — testée avec un prestataire simulé |

## 2. Risques examinés et protections

### Séparation des espaces et rôles
- Trois espaces distincts : site public, espace client (`#account` du site), espace de gestion
  (application séparée `admin/`, `noindex`, **aucun lien depuis le site public**).
  *Tests : `test_frontend_separation.py` échoue si un lien de gestion réapparaît dans le site public.*
- Cacher l'URL n'est pas une protection : chaque route `/api/admin/*` vérifie le rôle côté serveur.
- Rôles `customer`, `staff`, `admin`. Moindre privilège : l'équipe (`staff`) ne voit ni le chiffre
  d'affaires, ni les clients, ni les réglages, ne modifie pas les prix et ne peut ni annuler une
  commande payée ni rembourser. *Tests : `test_staff_handles_orders_and_stock_but_not_money_or_customers`.*
- Changement de rôle uniquement en ligne de commande sur le serveur (`create-admin`, `set-role`).
- Chaque action de gestion est tracée dans l'historique de la commande (rôle et identifiant de l'auteur).
- Liste des clients minimale (ni téléphone ni adresse) ; coordonnées complètes seulement dans la fiche.

### Contrôle d'accès (OWASP A01 · API1 BOLA · API5 BFLA)
- Chaque commande est cherchée avec `reference` **et** `user_id` : la commande d'un autre client
  renvoie 404, même référence connue. *Tests : `test_customers_cannot_see_each_others_orders`.*
- Toutes les routes `/api/admin/*` dépendent de `require_admin` au niveau du routeur ; refus par défaut.
  *Tests : `test_admin_routes_refused_to_anonymous_and_customers`.*
- Aucune route ne modifie le rôle ; `extra="forbid"` rejette `role`, `email`, `price`… envoyés par le client
  (protection contre l'affectation de masse). *Tests : `test_register_cannot_choose_role`, `test_profile_update_cannot_escalate`.*
- Premier administrateur créé uniquement en ligne de commande sur le serveur.

### Intégrité des commandes et du stock (API6 · logique métier)
- Prix et totaux calculés côté serveur ; champs de prix refusés. *Test : `test_client_supplied_price_is_refused`.*
- Quantités bornées (1 à 20 par produit, lignes dupliquées additionnées). *Test : `test_invalid_quantities_refused`.*
- Réservation atomique `UPDATE … WHERE stock >= n` + transaction annulée en bloc en cas d'échec :
  pas de survente concurrente. *Test : `test_insufficient_stock_refused_atomically`.*
- Produits « bientôt » et archivés non commandables. *Test : `test_unavailable_products_cannot_be_bought`.*
- Doubles soumissions : clé `Idempotency-Key` + contrainte d'unicité en base. *Test : `test_double_submission_returns_same_order`.*
- Références de commande aléatoires (~50 bits, `secrets`), non énumérables.

### Paiements
- Statut « payé » uniquement après vérification serveur à serveur (montant, devise, transaction).
  Un retour navigateur ne suffit jamais. *Tests : `test_browser_return_alone_never_marks_paid`, `test_amount_mismatch_is_refused`.*
- Notification CinetPay : HMAC-SHA256 `x-token` comparé en temps constant, identifiant de site vérifié,
  puis vérification auprès de CinetPay. *Tests : `test_webhook_rejects_bad_signature`, `test_webhook_trusts_provider_check_not_notification`.*
- Idempotence : un paiement confirmé est terminal, les rejeux sont sans effet. *Test : `test_webhook_valid_then_replay_is_idempotent`.*
- Nouvelle transaction à chaque tentative ; une commande payée ne peut pas être repayée.
- Aucune donnée de carte ni code Mobile Money ne transite par l'API.
- Le simulateur est désactivé en production, sauf activation explicite `ALLOW_SIMULATED_PAYMENTS=true`
  (mode démonstration signalé sur le site ; ventes simulées exclues du chiffre d'affaires).
  *Tests : `test_simulated_payments_in_production_require_explicit_flag`, `test_real_revenue_counted_separately_from_simulated`.*

### Authentification et sessions (A07 · API2)
- Argon2id (argon2-cffi), mise à niveau automatique du hachage à la connexion.
- Message d'échec identique que le compte existe ou non ; temps de réponse homogénéisé par un hachage factice.
- Limitation : 5 essais / 15 min par email, 20 par IP ; inscription, mot de passe oublié et réinitialisation limités.
- Session : jeton de 256 bits en cookie `HttpOnly`, `Secure` en production, `SameSite=Lax` ; seul son
  SHA-256 est stocké ; expirations absolue et d'inactivité ; nouvelle session à chaque connexion.
- Révocation : déconnexion, « déconnecter tous les appareils », changement et réinitialisation du mot de passe.
- Réinitialisation : jeton à usage unique, 30 min, transmis dans le fragment d'URL (jamais dans les journaux serveur),
  retiré de l'historique du navigateur par le site ; les autres liens en circulation sont invalidés.
- Aucun JWT ni jeton dans `localStorage`.

### CSRF
- Jeton synchronisé lié à la session, exigé dans l'en-tête `X-CSRF-Token` pour toute écriture authentifiée.
- Défense en profondeur : `Origin` non listée refusée sur les écritures ; corps JSON exigé
  (`Content-Type` strict), ce qui bloque les formulaires inter-sites.
  *Tests : `test_logout_requires_csrf_and_revokes_session`, `test_unlisted_origin_cannot_write`, `test_non_json_body_rejected`.*

### Validation et injections (A03)
- Schémas Pydantic stricts, longueurs bornées, formats contrôlés (slugs, téléphones, pays ISO).
- Accès base exclusivement via l'ORM / requêtes paramétrées ; aucune concaténation SQL ; aucune commande système.
- XSS : le site échappe tout contenu issu de l'API (`esc()`) ; les URL d'images sont limitées à
  `images/…` ou `https://` côté serveur et côté navigateur.
- Export CSV : préfixe `'` devant `= + - @` (injection de formules). *Test : `test_csv_export_neutralizes_formulas`.*
- SSRF : l'API ne télécharge jamais d'URL fournie par un utilisateur. Pas de téléversement de fichiers.

### Configuration et en-têtes (A05 · API8)
- CORS : liste explicite d'origines, identifiants autorisés seulement pour elles, méthodes et en-têtes limités.
- En-têtes : `X-Content-Type-Options`, `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`,
  CSP restrictive, `Cache-Control: no-store`, HSTS en HTTPS.
- Documentation interactive désactivée en production ; démarrage refusé si la configuration est permissive.
  *Test : `test_production_config_refuses_permissive_settings`.*
- Erreurs : messages génériques avec identifiant de requête ; les erreurs de validation ne renvoient
  jamais les valeurs saisies. *Tests : `test_unhandled_errors_are_generic`, `test_validation_errors_never_echo_input`.*
- Route de santé minimale (`{"status":"ok"}`).

### Abus et déni de service (API4)
- Corps de requête limité à 32 Ko ; transfert « chunked » sans longueur refusé sur les écritures.
- Limiteurs sur connexion, inscription, réinitialisation, commandes, paiements, alertes et webhooks.
- Appels CinetPay avec délai d'expiration (15 s) ; listes paginées et bornées.

### Données personnelles et journaux (A09)
- Données collectées minimales : nom, email, téléphone facultatif, adresse de livraison.
- Journaux : méthode, chemin, statut, durée, identifiant de requête — jamais de corps, cookies,
  chaîne de requête, mots de passe, jetons ou données de paiement.
- Suppression de compte : anonymisation des données personnelles, commandes conservées pour la comptabilité.
- `python -m app.cli purge` supprime les sessions et jetons expirés.

### Chaîne d'approvisionnement
- Dépendances figées à des versions exactes et auditées (`pip-audit`).
- Remarque : FastAPI 0.162 embarque des points d'instrumentation OpenTelemetry **désactivés par défaut** ;
  le paquet `opentelemetry` n'est pas installé, aucune donnée n'est exportée.
- Site : polices Google et icônes Tabler chargées depuis des CDN à version figée, sans bloquer le démarrage.

## 3. Risques résiduels et points non vérifiés

| Point | Niveau | Recommandation |
|---|---|---|
| Limiteur de débit en mémoire : valable pour **un seul processus** | Moyen si plusieurs instances | Garder 1 worker, ou Redis / limitation de l'hébergeur (Cloudflare) |
| Format exact de la signature `x-token` CinetPay non confirmé en conditions réelles | Moyen | Vérifier avec la documentation CinetPay actuelle et le mode test ; la vérification serveur à serveur reste la garantie principale |
| CSP du site statique : les gestionnaires `onclick` en ligne imposent `'unsafe-inline'` pour les scripts | Moyen | Migrer progressivement vers `addEventListener` puis retirer `'unsafe-inline'` |
| Pas de vérification de l'adresse email à l'inscription | Faible | Ajouter un email de confirmation une fois le SMTP en place |
| L'inscription indique qu'un email est déjà utilisé (message neutre mais distinct) | Faible | Atténué par la limitation ; supprimable avec la vérification d'email |
| Pas de double authentification pour l'administrateur | Moyen | Mot de passe long et unique ; TOTP envisageable plus tard |
| Envoi des alertes « bientôt disponible » non automatisé | Fonctionnel | Demandes enregistrées et visibles dans l'administration |
| ZAP Baseline, PostgreSQL, CinetPay réel, SMTP réel | Non vérifiés | À exécuter en préproduction (voir README) |
| Avis clients et note « 4.9 · 248 avis » affichés sur le site | Contenu | Texte marketing statique, sans système d'avis derrière |

## 4. En-têtes conseillés pour le site statique

À configurer chez l'hébergeur du site (exemple au format `_headers` de Netlify / Cloudflare Pages) :

```
/*
  Content-Security-Policy: default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net; font-src https://fonts.gstatic.com https://cdn.jsdelivr.net; img-src 'self' data:; connect-src 'self' https://api.nanye.com; form-action 'self'; frame-ancestors 'none'; base-uri 'self'
  Strict-Transport-Security: max-age=31536000; includeSubDomains
  X-Content-Type-Options: nosniff
  Referrer-Policy: strict-origin-when-cross-origin
  Permissions-Policy: camera=(), microphone=(), geolocation=()
```

Les redirections vers la page de paiement CinetPay se font par navigation (`location.href`),
non concernées par `connect-src`.

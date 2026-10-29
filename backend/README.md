# NANYÉ — API (backend)

API REST de la boutique NANYÉ : catalogue, comptes clients, commandes avec réservation de
stock, paiement CinetPay, alertes « bientôt disponible » et administration.

- **Pile** : Python 3.12 · FastAPI · SQLAlchemy 2 · Alembic · Pydantic 2 · Argon2id · Pytest
- **Base** : SQLite en local, PostgreSQL en production
- **Aucune dépendance lourde** : pas de Docker obligatoire, pas de Redis, pas de GPU, pas de service payant requis

Le frontend statique (`index.html`, `styles.css`, `app.js`) reste servi séparément ; il appelle
l'API dont l'adresse est définie dans `config.js`.

---

## 1. Installation sous Windows (PowerShell)

Prérequis : [Python 3.12](https://www.python.org/downloads/) (cocher « Add python.exe to PATH »).

```powershell
cd D:\MES_PROJETS\nanye\backend

# Environnement virtuel
python -m venv .venv
.\.venv\Scripts\Activate.ps1
# Si PowerShell refuse l'activation :
#   Set-ExecutionPolicy -Scope CurrentUser RemoteSigned

# Dépendances (versions figées)
python -m pip install --upgrade pip
pip install -r requirements-dev.txt     # production : requirements.txt seulement

# Configuration locale (aucun secret dans le modèle)
Copy-Item .env.example .env
```

## 2. Base de données et premier administrateur

```powershell
# Crée / met à jour le schéma (migrations Alembic)
python -m alembic upgrade head

# Charge le catalogue du site (3 produits) avec un stock initial
python -m app.cli seed-catalog --stock 40

# Crée le premier administrateur : le mot de passe est demandé au clavier,
# il n'apparaît ni dans l'historique PowerShell ni dans les journaux.
python -m app.cli create-admin --email vous@nanye.com --name "Votre nom"

# Facultatif : un compte « équipe » (préparation des commandes et stocks, sans accès à l'argent)
python -m app.cli create-admin --email preparation@nanye.com --name "Équipe" --role staff

# Retirer un accès de gestion (le compte redevient client)
python -m app.cli set-role --email preparation@nanye.com --role customer
```

Aucune route de l'API ne permet de changer de rôle : ces commandes, exécutées sur le serveur,
sont le seul moyen. `create-admin` relancée avec un email existant change le rôle du compte et
redéfinit son mot de passe.

### Rôles

| Rôle | Accès |
|---|---|
| Visiteur | Site public, catalogue, demandes d'alerte |
| Client (`customer`) | Son propre compte : profil, adresse mémorisée, commandes, paiement, sécurité |
| Équipe (`staff`) | Espace de gestion : commandes (préparer, expédier, livrer, annuler une commande non payée), stocks et seuils, consultation des produits |
| Administrateur (`admin`) | Tout, dont chiffre d'affaires, prix et fiches produits, clients, export CSV, réglages, annulation d'une commande payée et remboursement |

Chaque route de l'API vérifie le rôle côté serveur ; les écrans ne font qu'afficher ce qui est autorisé.

Facultatif, **développement uniquement** (refusé en production) :
`python -m app.cli seed-demo` crée un client de démonstration
(`client.demo@nanye.local` / `demo-nanye-2025`).

## 3. Démarrer

```powershell
# Terminal 1 — API (http://localhost:8000, documentation interactive : /api/docs)
cd D:\MES_PROJETS\nanye\backend
.\.venv\Scripts\Activate.ps1
python -m uvicorn app.main:app --reload --port 8000

# Terminal 2 — site (http://localhost:5500)
cd D:\MES_PROJETS\nanye
python -m http.server 5500 --directory public
```

Ouvrez **http://localhost:5500** (et non `127.0.0.1:5500`) : le cookie de session n'est
envoyé que si le site et l'API sont sur le même nom d'hôte (`localhost` ici).

Espace de gestion : **http://localhost:5500/admin/** (aucun lien depuis le site public).

En développement, `PAYMENT_PROVIDER=simulator` affiche une page de paiement **fictive**
(clairement signalée, aucun argent ne circule : accepter, refuser ou abandonner le paiement) et `EMAIL_BACKEND=console` écrit les emails
(lien de réinitialisation) dans le terminal de l'API au lieu de les envoyer.

## 4. Tests et contrôles de sécurité

```powershell
python -m pytest                               # 76 tests, base SQLite isolée et temporaire
python -m bandit -r app                        # analyse statique du code Python
python -m pip_audit -r requirements-dev.txt    # vulnérabilités connues des dépendances
```

OWASP ZAP Baseline (nécessite Docker ou Java ; **à lancer uniquement contre une instance de
test locale**, jamais contre la production sans autorisation) :

```powershell
docker run --rm -t ghcr.io/zaproxy/zaproxy:stable zap-baseline.py `
  -t http://host.docker.internal:8000/api/openapi.json -r zap-api.html
docker run --rm -t ghcr.io/zaproxy/zaproxy:stable zap-baseline.py `
  -t http://host.docker.internal:5500/index.html -r zap-site.html
```

Les résultats et les limites de ces contrôles sont détaillés dans [SECURITY.md](SECURITY.md).

---

## 5. Fonctionnement

### Catalogue
`GET /api/products?category=…`, `GET /api/products/{slug}`, `GET /api/shop-config`.
Le stock exact n'est jamais publié : l'API indique une disponibilité (`in_stock`, `low_stock`,
`out_of_stock`, `coming_soon`) et la quantité maximale commandable.

### Commandes
`POST /api/orders` (compte requis, en-têtes `X-CSRF-Token` et `Idempotency-Key`) :

- le client n'envoie que des **slugs et des quantités** ; tout champ de prix ou de total est refusé (422) ;
- prix, sous-total, frais de livraison et total sont calculés côté serveur ;
- le stock est réservé par une mise à jour conditionnelle atomique (pas de survente, même en parallèle) ;
- une même clé d'idempotence renvoie la même commande (double clic, rechargement) ;
- une commande non payée expire après `ORDER_PAYMENT_TIMEOUT_MINUTES` et libère son stock.

Statuts : `pending_payment → paid → preparing → shipped → delivered`, ou `cancelled`.
`paid` n'est **jamais** fixé à la main ni par le navigateur : seulement après vérification
du paiement auprès du prestataire.

### Paiement (CinetPay)
1. `POST /api/orders/{ref}/pay` crée une transaction (identifiant neuf à chaque tentative) et renvoie l'URL CinetPay.
2. CinetPay appelle `POST /api/payments/cinetpay/notify` : signature `x-token` (HMAC-SHA256) vérifiée,
   puis **vérification serveur à serveur** (`/payment/check`) du statut, du montant et de la devise.
3. Au retour du client, le site appelle `POST /api/orders/{ref}/verify-payment`, qui interroge aussi CinetPay.

Le traitement est idempotent : une notification rejouée n'a aucun effet sur un paiement déjà confirmé.

### Comptes
Inscription, connexion, déconnexion (de cet appareil ou de tous), modification du profil
et du mot de passe, mot de passe oublié (lien à usage unique valable 30 min), suppression du
compte (données personnelles anonymisées, commandes conservées pour la comptabilité).

Sessions : jeton aléatoire dans un cookie `HttpOnly` (`Secure` en production, `SameSite=Lax`),
stocké haché en base, révocable, expiration absolue (7 jours) et d'inactivité (48 h). Chaque
écriture exige l'en-tête `X-CSRF-Token` lié à la session.

### Espace de gestion (`admin/` côté site, `/api/admin/*` côté API)
- **Vue d'ensemble** : chiffre d'affaires encaissé (les ventes simulées sont comptées à part),
  commandes à préparer, en attente, clients, ventes sur six mois, stocks, alertes et raccourcis.
- **Commandes** : recherche (référence, nom, email), filtres, pagination, détail complet
  (articles, paiement, livraison, historique avec auteur de chaque action), changements de statut
  confirmés et contrôlés, remboursement, export CSV (protégé contre l'injection de formules).
- **Produits** : création et modification complètes, retrait du catalogue (archivage).
- **Stocks** : ajustement et seuil d'alerte par produit, demandes d'alerte en attente.
- **Clients** (administrateurs) : liste minimale (sans téléphone ni adresse), fiche avec historique.
- **Réglages** : lecture de la configuration (sans aucun secret), changement de son mot de passe.

### Paiements simulés en ligne (avant l'ouverture des paiements réels)
`PAYMENT_PROVIDER=simulator` et `ALLOW_SIMULATED_PAYMENTS=true` permettent de faire tourner la
boutique en ligne sans encaisser : le site affiche un bandeau « démonstration », chaque commande
indique « paiement simulé » et l'administration sépare ces ventes du chiffre d'affaires réel.
Sans `ALLOW_SIMULATED_PAYMENTS=true`, la production refuse le simulateur.

### Maintenance
```powershell
python -m app.cli expire-orders   # libère le stock des commandes non payées (aussi fait à la volée)
python -m app.cli purge           # supprime sessions et liens de réinitialisation expirés
```

---

## 6. Variables d'environnement

| Variable | Rôle | Production |
|---|---|---|
| `APP_ENV` | `development`, `test` ou `production` | `production` |
| `DATABASE_URL` | URL de la base | PostgreSQL (`postgresql://…`) |
| `CORS_ORIGINS` | Origines du site autorisées (virgules) | `https://nanye.com,https://www.nanye.com` |
| `FRONTEND_BASE_URL` | URL du site (liens email, retour paiement) | `https://nanye.com/index.html` |
| `API_BASE_URL` | URL publique de l'API (notification paiement) | `https://api.nanye.com` |
| `COOKIE_SECURE` | Cookie réservé au HTTPS | `true` |
| `COOKIE_SAMESITE` | `lax` (même domaine) ou `none` (domaines différents) | `lax` |
| `COOKIE_DOMAIN` | Domaine du cookie si besoin | vide en général |
| `SHIPPING_FEE_XOF` | Frais de livraison forfaitaires | à décider |
| `MAX_QUANTITY_PER_ITEM` | Plafond par produit et par commande | `20` |
| `ORDER_PAYMENT_TIMEOUT_MINUTES` | Délai de paiement avant libération du stock | `30` |
| `PAYMENT_PROVIDER` | `disabled`, `simulator` ou `cinetpay` | `cinetpay` (ou `simulator` en démonstration) |
| `ALLOW_SIMULATED_PAYMENTS` | Autorise le simulateur en production (démonstration signalée) | `false` une fois CinetPay actif |
| `CINETPAY_API_KEY`, `CINETPAY_SITE_ID`, `CINETPAY_SECRET_KEY` | Identifiants marchand | **secrets** |
| `EMAIL_BACKEND` | `disabled`, `console` (dev) ou `smtp` | `smtp` |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM` | Envoi des emails | **secrets** |
| `LOG_LEVEL` | Niveau de journalisation | `INFO` |

En production, l'API **refuse de démarrer** si la configuration est permissive : debug actif,
cookie non sécurisé, SQLite, origine CORS `*` ou non HTTPS, simulateur de paiement, emails en
console, identifiants CinetPay ou SMTP manquants.

---

## 7. Déploiement

Architecture recommandée, en restant **sur le même domaine** (cookies `SameSite=Lax`) :

| Élément | Exemple d'hébergement | Adresse |
|---|---|---|
| Site statique | Cloudflare Pages, Netlify, GitHub Pages | `https://nanye.com` |
| API | Render, Railway, Fly.io ou un petit VPS | `https://api.nanye.com` |
| Base | PostgreSQL managé de l'hébergeur | — |

1. Créer la base PostgreSQL et récupérer son URL.
2. Déclarer les variables de la section 6 dans le gestionnaire de secrets de l'hébergeur (jamais dans le dépôt).
3. Commande de construction : `pip install -r requirements.txt`
4. Commande de démarrage (**un seul worker** : le limiteur de débit est en mémoire) :
   ```
   python -m alembic upgrade head && python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1 --proxy-headers --forwarded-allow-ips="*" --no-server-header
   ```
   `--forwarded-allow-ips="*"` n'est acceptable que si l'API n'est joignable **que** via le proxy
   de l'hébergeur ; sur un VPS, indiquer l'adresse du proxy (ex. `127.0.0.1` derrière Nginx/Caddy).
5. Une fois en ligne : `python -m app.cli create-admin --email … ` et `python -m app.cli seed-catalog --stock N`
   depuis la console de l'hébergeur.
6. Dans `config.js` du site : `apiBaseUrl: 'https://api.nanye.com/api'`, puis publier le site.
7. Dans l'espace marchand CinetPay : URL de notification `https://api.nanye.com/api/payments/cinetpay/notify`.
   Tester d'abord avec le mode test du compte CinetPay.
8. Planifier `python -m app.cli purge` (quotidien) si l'hébergeur propose des tâches planifiées.

### Sauvegardes et restauration (PostgreSQL)
```powershell
pg_dump --format=custom --no-owner "$env:DATABASE_URL" -f nanye-AAAAMMJJ.dump      # sauvegarde
pg_restore --clean --no-owner --dbname "$env:DATABASE_URL" nanye-AAAAMMJJ.dump      # restauration
```
Activer en plus les sauvegardes automatiques de l'hébergeur, conserver au moins 7 jours,
**tester une restauration** sur une base de test avant l'ouverture, et stocker les copies
hors de l'hébergeur principal.

### Différences SQLite / PostgreSQL vérifiées
Schéma portable (pas d'énumérations natives, contraintes `CHECK` explicites, dates UTC sans
fuseau, JSON standard). La réservation de stock repose sur un `UPDATE … WHERE stock >= n`
atomique dans les deux moteurs. **Non vérifié ici** : l'exécution des migrations et des tests
sur un vrai serveur PostgreSQL (aucun disponible sur la machine de développement) — à faire
sur la base de préproduction avant l'ouverture.

---

## 8. Checklist de mise en production

- [ ] `APP_ENV=production` ; l'API démarre sans erreur de configuration
- [ ] PostgreSQL managé, migrations appliquées (`alembic upgrade head`), sauvegardes automatiques actives
- [ ] Restauration testée sur une base de préproduction
- [ ] HTTPS sur le site et l'API ; `COOKIE_SECURE=true`
- [ ] `CORS_ORIGINS` limité aux domaines réels du site
- [ ] Secrets (CinetPay, SMTP, base) dans le gestionnaire de l'hébergeur, absents du dépôt
- [ ] Administrateur créé par `create-admin`, mot de passe long et unique ; comptes « équipe » si besoin
- [ ] `ALLOW_SIMULATED_PAYMENTS=false` dès que CinetPay est actif (le bandeau « démonstration » disparaît)
- [ ] Catalogue chargé, prix et stocks vérifiés dans l'administration
- [ ] `SHIPPING_FEE_XOF` fixé selon votre politique de livraison
- [ ] CinetPay : URL de notification déclarée, paiement de test accepté **et** refusé vérifiés
- [ ] SMTP : email de réinitialisation reçu
- [ ] `config.js` pointe vers l'API de production
- [ ] En-têtes de sécurité du site statique configurés (voir SECURITY.md)
- [ ] Un seul worker Uvicorn (ou limiteur partagé si plusieurs)
- [ ] `pytest`, `bandit` et `pip-audit` relancés sur la version déployée
- [ ] Mentions légales, CGV et politique de confidentialité publiées (non fournies ici)

## 9. Informations encore nécessaires

- **Identifiants CinetPay** (`API_KEY`, `SITE_ID`, `SECRET_KEY`) et validation du compte marchand.
- **Choix de l'hébergeur** et des noms de domaine (`nanye.com`, `api.nanye.com`).
- **Serveur SMTP** (fourni par votre hébergeur email, Brevo, Mailjet…).
- **Frais de livraison** et zones desservies.

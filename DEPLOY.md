# Mise en ligne de NANYÉ — Vercel + Neon (offres gratuites)

Ce guide met en ligne une **version de démonstration** complète : site, espace client, espace de
gestion et paiements **simulés** (aucune somme n'est débitée). Durée : environ 30 minutes.

## Architecture

```
https://<projet>.vercel.app
├── /            → site statique (dossier public/, servi par le CDN Vercel)
├── /admin/      → espace de gestion (public/admin/, aucun lien depuis le site)
└── /api/*       → API FastAPI (server.py → backend/app), une fonction Vercel
                    └── PostgreSQL Neon (offre gratuite)
```

Site et API partagent le même domaine : pas de CORS entre domaines, cookie de session « same-site ».

## Limites à connaître (offres gratuites)

| Service | Limite | Conséquence |
|---|---|---|
| Vercel Hobby | **Usage non commercial uniquement** (le traitement de paiements est considéré comme commercial) | Convient pour montrer le projet. Pour la boutique réelle : Vercel Pro (payant) ou un autre hébergeur |
| Vercel Functions | 300 s max par requête, 2 Go de mémoire | Largement suffisant |
| Vercel Functions | Instances multiples et éphémères | La limitation des tentatives de connexion (en mémoire) est affaiblie : à renforcer avant l'ouverture réelle |
| Neon Free | 1 Go de stockage, 100 h de calcul par mois, mise en veille après 5 min | Premier appel après inactivité un peu plus lent (~1 s) |
| Neon Free | Historique de restauration de **6 heures** seulement | Faire des sauvegardes manuelles (`pg_dump`, voir plus bas) |
| Emails | Non configurés | « Mot de passe oublié » n'envoie rien tant qu'un SMTP n'est pas renseigné |

## Étape 1 — Code sur GitHub

Dépôt : https://github.com/MCLBK/Nany-.git (branche `main`). Le fichier `.env` local, les bases
SQLite et l'environnement virtuel ne sont jamais publiés (`.gitignore`).

## Étape 2 — Créer le projet Vercel

1. https://vercel.com → **Sign up with GitHub** (offre Hobby, gratuite).
2. **Add New… → Project** → importer le dépôt `MCLBK/Nany-`.
3. Nom du projet : par exemple `nanye` (l'adresse sera `https://nanye.vercel.app` si elle est libre).
4. **Framework Preset** : laisser la détection (FastAPI) ; **Root Directory** : `./`.
5. Ne pas encore cliquer sur Deploy : ajouter d'abord la base (étape 3) et les variables (étape 4).

## Étape 3 — Base PostgreSQL Neon

Dans le projet Vercel : **Storage → Create Database → Neon** (Marketplace, offre Free), région
Europe (Frankfurt) par exemple, puis **Connect** au projet. Vercel ajoute automatiquement
`DATABASE_URL` (connexion « pooler », utilisée par l'API) et `DATABASE_URL_UNPOOLED` (connexion
directe, utilisée pour les migrations).

> Alternative : créer la base sur https://neon.com (gratuit, sans carte bancaire) et coller l'URL
> « Pooled connection » dans `DATABASE_URL`.

## Étape 4 — Variables d'environnement (Vercel → Settings → Environment Variables)

Remplacer `nanye.vercel.app` par l'adresse réelle du projet. Aucune de ces valeurs n'est un secret,
sauf `DATABASE_URL` (déjà créée par l'intégration Neon).

| Variable | Valeur |
|---|---|
| `APP_ENV` | `production` |
| `CORS_ORIGINS` | `https://nanye.vercel.app` |
| `FRONTEND_BASE_URL` | `https://nanye.vercel.app/index.html` |
| `API_BASE_URL` | `https://nanye.vercel.app` |
| `COOKIE_SECURE` | `true` |
| `COOKIE_SAMESITE` | `lax` |
| `PAYMENT_PROVIDER` | `simulator` |
| `ALLOW_SIMULATED_PAYMENTS` | `true` |
| `EMAIL_BACKEND` | `disabled` |
| `SHIPPING_FEE_XOF` | `0` (ou vos frais forfaitaires) |
| `LOG_LEVEL` | `INFO` |

En production, l'API **refuse de démarrer** si un réglage est permissif (cookie non sécurisé,
origine non HTTPS, base SQLite…) : c'est voulu. Le message apparaît dans **Logs** de Vercel.

## Étape 5 — Initialiser la base (une seule fois, depuis votre PC)

Copier `DATABASE_URL_UNPOOLED` (Vercel → Storage → Neon → `.env.local`, ou console Neon →
« Direct connection »), puis dans PowerShell :

```powershell
cd D:\MES_PROJETS\nanye\backend
.\.venv\Scripts\Activate.ps1
$env:DATABASE_URL = "postgresql://…"          # URL directe Neon (reste dans ce terminal seulement)

python -m alembic upgrade head                 # crée les tables
python -m app.cli seed-catalog --stock 40      # charge les 3 produits du site
python -m app.cli create-admin --email vous@exemple.com --name "Votre nom"   # mot de passe demandé au clavier

Remove-Item Env:DATABASE_URL                   # efface l'URL de la session
```

Ne collez jamais cette URL dans un fichier du projet ni dans une conversation : elle contient le
mot de passe de la base.

## Étape 6 — Déployer et vérifier

Cliquer **Deploy** (ou **Redeploy** si le projet existe déjà). Puis vérifier :

| Adresse | Attendu |
|---|---|
| `https://nanye.vercel.app/` | Accueil, bandeau « Boutique en démonstration » en bas |
| `https://nanye.vercel.app/api/health` | `{"status":"ok"}` |
| `https://nanye.vercel.app/api/products` | Les 3 produits |
| `https://nanye.vercel.app/admin/` | Écran de connexion de la gestion |
| Parcours | Créer un compte client, commander, payer (simulé), voir la commande dans `/admin/` |

## Mises à jour

- **Code** : `git push` sur `main` → Vercel redéploie automatiquement (une prévisualisation est
  créée pour les autres branches).
- **Schéma de base** : si une nouvelle migration Alembic est ajoutée, l'appliquer depuis le PC
  (étape 5, `alembic upgrade head`) **avant** de pousser le code qui l'utilise.
- **Comptes de gestion** : `create-admin` / `set-role` depuis le PC (étape 5), jamais en ligne.

## Sauvegarde et restauration

```powershell
# Sauvegarde (PostgreSQL client installé : https://www.postgresql.org/download/windows/)
pg_dump --format=custom --no-owner "$env:DATABASE_URL" -f nanye-AAAAMMJJ.dump
# Restauration (sur une base vide ou de test d'abord)
pg_restore --clean --no-owner --dbname "$env:DATABASE_URL" nanye-AAAAMMJJ.dump
```

Neon conserve aussi 6 heures d'historique sur l'offre gratuite (restauration depuis la console).

## Passer de la démonstration à la vraie boutique

1. Hébergement compatible avec un usage commercial (Vercel Pro, ou Render / Koyeb + Neon) et nom de domaine.
2. CinetPay : `PAYMENT_PROVIDER=cinetpay`, identifiants marchands en variables, `ALLOW_SIMULATED_PAYMENTS=false`,
   URL de notification `https://<domaine>/api/payments/cinetpay/notify`, test en mode test CinetPay.
3. SMTP : `EMAIL_BACKEND=smtp` et variables `SMTP_*`.
4. Limitation des tentatives partagée entre instances (Redis managé ou pare-feu de l'hébergeur).
5. Checklist complète : `backend/README.md`, section 8.

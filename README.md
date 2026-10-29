# NANYÉ — Plateforme E-commerce Premium
### *La tradition devient chic.*

---

## 📁 Structure du projet

```
nanye/
├── public/                 # Site statique (servi tel quel, en local comme en ligne)
│   ├── index.html          # Site public (toutes les pages)
│   ├── styles.css          # Design system complet
│   ├── app.js              # Logique applicative (routing, panier, appels à l'API)
│   ├── config.js           # Adresse de l'API (localhost en local, /api en ligne)
│   ├── admin/              # Espace de gestion (application séparée, aucun lien public)
│   └── images/             # Photos produit, logo, fonds
├── backend/                # API FastAPI — voir backend/README.md et backend/SECURITY.md
├── server.py               # Point d'entrée de l'API pour Vercel
├── requirements.txt        # Dépendances Python de production
├── vercel.json             # Configuration Vercel (exclusions, en-têtes)
├── DEPLOY.md               # Mise en ligne pas à pas (Vercel + Neon)
├── images/ (dans public/)
│   ├── product-hero.jpg    # Photo produit principale (pot sur fond sombre)
│   ├── product-ingredients.jpg  # Photo produit avec ingrédients
│   ├── lifestyle.jpg       # Photo lifestyle / ambassadrice produit
│   ├── logo-nanye.jpg      # Logo NANYÉ (texte afro-typographique)
│   ├── bg-hero-ustensiles.jpg     # Fond du hero (ustensiles en bois, texte à gauche)
│   ├── bg-motif-legumes.jpg       # Fond crème motif légumes — section « Composition »
│   ├── bg-motif-legumes-clair.jpg # Variante plus pâle — en-tête de la page Recettes
│   └── bg-vague-bois.jpg          # Vague de bois — appel à l'action final, raccord avec le footer
└── README.md               # Ce fichier
```

---

## 🚀 Lancement rapide (Windows, PowerShell)

1. Installer et démarrer l'API : suivre [backend/README.md](backend/README.md) (sections 1 à 3).
2. Servir le site :
```powershell
cd D:\MES_PROJETS\nanye
python -m http.server 5500 --directory public
```
3. Ouvrir **http://localhost:5500** (et non `127.0.0.1`, pour que le cookie de session fonctionne).

Sans API, le site reste consultable : le catalogue s'affiche en mode hors ligne et les achats
sont désactivés avec un message explicite.

---

## 🗂️ Pages disponibles

| Page | ID | Accès |
|---|---|---|
| Homepage | `home` | Par défaut |
| Boutique | `shop` | Menu → Boutique |
| Fiche produit | `product/<slug>` | Clic sur la photo ou le nom d'un produit |
| Commande | `checkout` | Panier → Passer commande |
| Suivi de commande | `order/<référence>` | Mon compte → une commande, ou retour de paiement |
| Nouveau mot de passe | `reset/<jeton>` | Lien reçu par email |
| Notre Histoire | `story` | Menu → Notre Histoire |
| Recettes | `recipes` | Menu → Recettes |
| Distribution | `distribution` | Menu → Distribution |
| Mon Compte | `account` | Bouton "Mon Compte" |

### Espace de gestion (séparé du site)

L'administration est une application distincte : **`/admin/`** (ex. `http://localhost:5500/admin/`).
Aucun lien n'y mène depuis le site public. Elle exige un compte « équipe » ou « administrateur »,
créé uniquement en ligne de commande sur le serveur (voir `backend/README.md`) ; un client qui s'y
connecte est renvoyé vers son espace. Chaque action est de toute façon vérifiée par l'API.

---

## 🛒 Fonctionnalités actives

- **Navigation SPA** — Toutes les pages routées sans rechargement, via l'URL (`index.html#shop`, `#product`…) : liens partageables, bouton retour du navigateur, lien actif dans le menu
- **Panier** — Ajout (quantité de la fiche produit prise en compte), quantité, suppression, total calculé ; fermeture par Échap ; paiement désactivé si le panier est vide
- **Images produit réelles** — Galerie avec changement de photo au clic
- **Catalogue, stocks et prix** — Servis par l'API ; disponibilité réelle, achats impossibles hors stock
- **Produits "Bientôt"** — Demande d'alerte par email enregistrée côté serveur
- **Comptes clients** — Inscription, connexion, historique, profil, mot de passe oublié, suppression du compte
- **Commande et paiement** — Adresse de livraison, totaux calculés par le serveur, paiement CinetPay
- **Toast notifications** — Ajout panier et demandes d'alerte
- **Filtres boutique** — Filtrage réel par catégorie (`data-cat` sur chaque carte) avec état vide
- **Menu mobile responsive** — Plein écran, inclut Mon compte et Administration
- **Fil d'Ariane** — Navigation breadcrumb sur la fiche produit
- **Nav** — Transparente sur le hero de l'accueil, opaque au défilement et sur les autres pages
- **Espace de gestion** (`/admin/`) — Vue d'ensemble, commandes (recherche, filtres, suivi, remboursement), produits, stocks et seuils d'alerte, clients, réglages

---

## 🎨 Design System

### Palette
| Rôle | Couleur | Variable |
|---|---|---|
| Primary | `#F2C300` | `--gold` |
| Secondary | `#5A3725` | `--brown` |
| Background | `#1A120D` | `--bg` |
| Surface | `#2A1B14` | `--surface` |
| Text | `#FFF8F0` | `--text` |
| Muted | `#B7A89A` | `--muted` |
| Success | `#5E7C41` | `--success` |

### Typographie
- **Titres et mots éditoriaux** : Cormorant Garamond, graisses 500–600 (jamais 300 : trop fin sur fond sombre)
- **Texte, interface, boutons et tous les nombres** (prix, totaux, indicateurs, références) : DM Sans 400–500, chiffres tabulaires
- **Italique** : réservé à trois accents de l'accueil (hero, « L'afitin, réinventé », appel final) et aux citations
- **Échelle fluide** : jetons `--fs-display`, `--fs-h1`, `--fs-h2`, `--fs-h3`, `--fs-lead` dans `styles.css`
- **Capitales espacées** : deux valeurs seulement (`--track-label`, `--track-ui`)
- **Chargement** : polices non bloquantes, graisses strictement nécessaires, repli Georgia ajusté
- **Icônes** : Tabler Icons (webfont CDN)

### Fonds crème
Les sections claires reprennent le crème des images fournies (`--cream: #FEF8EA`).
Sur ces fonds, l'accent passe de l'or au brun de la charte (`--brown`), l'or n'y étant pas lisible.

### Animations
Volontairement limitées aux transitions d'interface (survol, panier, toast) ;
désactivées si l'utilisateur a activé « réduire les animations ».

---

## ➕ Lancer un nouveau produit (Épicée, Gingembre…)

Plus besoin de modifier le HTML : dans l'espace de gestion, onglet **Produits**, passer le
produit en « En vente », saisir son prix et son stock, puis enregistrer. Pour lui donner une
photo, déposer le fichier dans `images/` et le renseigner à la création du produit
(« Nouveau produit »), sinon le pot illustré est affiché. Les demandes d'alerte reçues
pour ce produit sont indiquées dans l'onglet **Stocks**.

---

## 🔧 Backend et production

Le backend est en place : FastAPI, PostgreSQL, paiement CinetPay, comptes clients, administration.
Installation, tests, audits de sécurité, déploiement et checklist de mise en production :
[backend/README.md](backend/README.md). Rapport de sécurité : [backend/SECURITY.md](backend/SECURITY.md).

---

## 📞 Contact & Support

**NANYÉ** · Cotonou, Bénin  
WhatsApp Business : +229 XX XX XX XX  
Email : contact@nanye.com  

---

*© 2025 NANYÉ. Tous droits réservés.*  
*"Le goût relevé de la tradition."*

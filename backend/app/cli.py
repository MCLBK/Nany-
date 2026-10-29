"""Commandes d'administration : python -m app.cli <commande>

  create-admin --email EMAIL --name "Nom" [--role staff]
                                              crée (ou promeut) un compte de gestion ; mot de passe demandé au clavier
  set-role --email EMAIL --role ROLE          change le rôle d'un compte existant (customer, staff, admin)
  seed-catalog --stock N                      charge le catalogue du site (produits existants non modifiés)
  seed-demo                                   données de démonstration (interdit en production)
  expire-orders                               annule les commandes non payées hors délai
  purge                                       supprime sessions et liens de réinitialisation expirés
"""
import argparse
import getpass
import sys
from datetime import timedelta

from sqlalchemy import delete, or_, select

from app.core.config import get_settings
from app.core.security import hash_password
from app.db import SessionLocal, utcnow
from app.models import AuthSession, PasswordResetToken, Product, User
from app.schemas import RegisterIn
from app.services.orders import expire_stale_orders

CATALOG = [
    dict(slug="moutarde-naturelle", name="Moutarde Naturelle", category="naturelle", status="available",
         price_xof=3500, format="200 g", sort_order=1,
         tagline="L'essence de l'afitin, sublimée.",
         description="L'authenticité de l'afitin sublimée. Notes profondes, texture onctueuse, caractère sans compromis.",
         long_description=("Notre moutarde phare. Produite à partir de graines de néré sélectionnées, fermentées "
                           "naturellement pendant 72 heures selon les méthodes ancestrales du Bénin. Profil aromatique "
                           "exceptionnel — umami, terreux, légèrement fumé."),
         images=["images/product-hero.jpg", "images/product-ingredients.jpg", "images/lifestyle.jpg"]),
    dict(slug="moutarde-epicee", name="Moutarde Épicée", category="epice", status="coming_soon",
         price_xof=0, format="200 g", sort_order=2, tagline="Une intensité maîtrisée.",
         description="Chaleur progressive, piment akwi du Bénin. Une intensité maîtrisée.", images=[]),
    dict(slug="moutarde-gingembre", name="Moutarde Gingembre", category="herbes", status="coming_soon",
         price_xof=0, format="200 g", sort_order=3, tagline="Fraîcheur et caractère.",
         description="La vivacité du gingembre et la profondeur de l'afitin. Fraîcheur et caractère.", images=[]),
]


def create_admin(email: str, name: str, role: str = "admin") -> None:
    password = getpass.getpass("Mot de passe (10 caractères minimum) : ")
    if password != getpass.getpass("Confirmez le mot de passe : "):
        sys.exit("Les mots de passe ne correspondent pas.")
    data = RegisterIn(email=email, password=password, full_name=name)  # mêmes règles qu'en ligne
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == data.email))
        if user:
            user.role = role
            user.password_hash = hash_password(password)
            print(f"Compte existant passé au rôle {role} : {data.email}")
        else:
            db.add(User(email=data.email, full_name=data.full_name, password_hash=hash_password(password), role=role))
            print(f"Compte {role} créé : {data.email}")
        db.commit()


def set_role(email: str, role: str) -> None:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email.strip().lower()))
        if user is None:
            sys.exit("Aucun compte avec cet email.")
        user.role = role
        db.commit()
    print(f"{email} : rôle {role}")


def seed_catalog(stock: int) -> None:
    with SessionLocal() as db:
        for spec in CATALOG:
            if db.scalar(select(Product.id).where(Product.slug == spec["slug"])):
                print(f"= {spec['slug']} existe déjà (non modifié)")
                continue
            db.add(Product(**spec, stock=stock if spec["status"] == "available" else 0))
            print(f"+ {spec['slug']}")
        db.commit()


def seed_demo() -> None:
    if get_settings().is_production:
        sys.exit("Interdit en production.")
    seed_catalog(stock=100)
    with SessionLocal() as db:
        if not db.scalar(select(User.id).where(User.email == "client.demo@nanye.local")):
            db.add(User(email="client.demo@nanye.local", full_name="Client Démo",
                        password_hash=hash_password("demo-nanye-2025"), role="customer"))
            db.commit()
    print("Compte de démonstration : client.demo@nanye.local / demo-nanye-2025 (développement uniquement)")


def purge() -> None:
    now = utcnow()
    with SessionLocal() as db:
        s = db.execute(delete(AuthSession).where(or_(
            AuthSession.expires_at < now, AuthSession.revoked_at < now - timedelta(days=30)))).rowcount
        t = db.execute(delete(PasswordResetToken).where(PasswordResetToken.expires_at < now - timedelta(days=1))).rowcount
        db.commit()
    print(f"{s} session(s) et {t} lien(s) de réinitialisation supprimés")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="python -m app.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("create-admin")
    a.add_argument("--email", required=True)
    a.add_argument("--name", default="Administrateur")
    a.add_argument("--role", choices=("admin", "staff"), default="admin")
    r = sub.add_parser("set-role")
    r.add_argument("--email", required=True)
    r.add_argument("--role", choices=("customer", "staff", "admin"), required=True)
    c = sub.add_parser("seed-catalog")
    c.add_argument("--stock", type=int, required=True, help="stock initial des produits disponibles")
    sub.add_parser("seed-demo")
    sub.add_parser("expire-orders")
    sub.add_parser("purge")
    args = p.parse_args(argv)
    if args.cmd == "create-admin":
        create_admin(args.email, args.name, args.role)
    elif args.cmd == "set-role":
        set_role(args.email, args.role)
    elif args.cmd == "seed-catalog":
        if args.stock < 0:
            sys.exit("--stock doit être positif")
        seed_catalog(args.stock)
    elif args.cmd == "seed-demo":
        seed_demo()
    elif args.cmd == "expire-orders":
        with SessionLocal() as db:
            print(f"{expire_stale_orders(db)} commande(s) expirée(s)")
    elif args.cmd == "purge":
        purge()


if __name__ == "__main__":
    main()

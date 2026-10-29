from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.ratelimit import client_ip, enforce
from app.db import get_db
from app.models import CATEGORIES, NotifyRequest, Product
from app.schemas import NotifyIn, ProductOut

router = APIRouter(tags=["catalogue"])


def to_public(p: Product) -> ProductOut:
    max_qty = get_settings().max_quantity_per_item
    if p.status == "coming_soon":
        availability, max_quantity = "coming_soon", 0
    elif p.stock <= 0:
        availability, max_quantity = "out_of_stock", 0
    else:
        availability = "low_stock" if p.stock <= p.low_stock_threshold else "in_stock"
        max_quantity = min(p.stock, max_qty)
    # Le stock exact n'est pas publié : seulement une disponibilité et un plafond commandable.
    return ProductOut(
        slug=p.slug, name=p.name, tagline=p.tagline, description=p.description,
        long_description=p.long_description, category=p.category, format=p.format,
        price_xof=p.price_xof, status=p.status, availability=availability,
        max_quantity=max_quantity, images=list(p.images or []),
    )


@router.get("/shop-config")
def shop_config():
    """Réglages publics utiles à l'affichage (aucune donnée sensible)."""
    s = get_settings()
    return {
        "currency": "XOF",
        "shipping_fee_xof": s.shipping_fee_xof,
        "max_quantity_per_item": s.max_quantity_per_item,
        "payment_enabled": s.payment_provider == "cinetpay" or s.simulator_enabled,
        "payment_simulated": s.simulator_enabled,
    }


@router.get("/products", response_model=list[ProductOut])
def list_products(category: Literal[CATEGORIES] | None = None, db: Session = Depends(get_db)):  # type: ignore[valid-type]
    q = select(Product).where(Product.status != "archived").order_by(Product.sort_order, Product.id)
    if category:
        q = q.where(Product.category == category)
    return [to_public(p) for p in db.scalars(q)]


@router.get("/products/{slug}", response_model=ProductOut)
def get_product(slug: str, db: Session = Depends(get_db)):
    p = db.scalar(select(Product).where(Product.slug == slug[:80], Product.status != "archived"))
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Produit introuvable.")
    return to_public(p)


@router.post("/notify", status_code=status.HTTP_202_ACCEPTED)
def request_notification(data: NotifyIn, request: Request, db: Session = Depends(get_db)):
    """Demande d'alerte « bientôt disponible ». Réponse identique que l'email soit déjà inscrit ou non."""
    enforce("notify", client_ip(request))
    product = db.scalar(select(Product).where(Product.slug == data.product_slug))
    if product is None or product.status != "coming_soon":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ce produit n'accepte pas de demande d'alerte.")
    db.add(NotifyRequest(email=data.email.lower(), product_id=product.id))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()  # déjà inscrit : même réponse, pour ne rien révéler
    return {"detail": "Demande enregistrée. Nous vous écrirons à l'ouverture des ventes."}

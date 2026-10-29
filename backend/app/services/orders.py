"""Règles métier des commandes : prix serveur, réservation de stock, statuts."""
import logging
from collections import defaultdict
from datetime import timedelta

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.config import get_settings
from app.core.security import new_order_reference
from app.db import utcnow
from app.models import Order, OrderEvent, OrderItem, Product, User
from app.schemas import OrderCreateIn

log = logging.getLogger("nanye.orders")

# Transitions autorisées pour un administrateur. "paid" n'est atteint que par un paiement vérifié.
ADMIN_TRANSITIONS: dict[str, set[str]] = {
    "pending_payment": {"cancelled"},
    "paid": {"preparing", "cancelled"},
    "preparing": {"shipped", "cancelled"},
    "shipped": {"delivered"},
    "delivered": set(),
    "cancelled": {"refunded"},  # uniquement si la commande avait été payée
    "refunded": set(),
}
# Le personnel (staff) traite les commandes mais ne touche pas à l'argent :
# pas d'annulation d'une commande payée, pas de remboursement.
STAFF_FORBIDDEN = {("paid", "cancelled"), ("preparing", "cancelled"), ("cancelled", "refunded")}
PAID_STATUSES = ("paid", "preparing", "shipped", "delivered")


def _bad(detail: str, code: int = status.HTTP_400_BAD_REQUEST) -> HTTPException:
    return HTTPException(code, detail)


def order_query():
    return select(Order).options(
        selectinload(Order.items), selectinload(Order.events), selectinload(Order.payments)
    )


def add_event(order: Order, to_status: str, actor: str, note: str = "") -> None:
    order.events.append(OrderEvent(from_status=order.status, to_status=to_status, actor=actor, note=note[:300]))
    order.status = to_status


def _reserve(db: Session, product_id: int, qty: int) -> bool:
    """Décrément atomique : ne réussit que si le stock suffit (pas de survente concurrente)."""
    res = db.execute(
        update(Product)
        .where(Product.id == product_id, Product.stock >= qty, Product.status == "available")
        .values(stock=Product.stock - qty)
    )
    return res.rowcount == 1


def _release(db: Session, order: Order) -> None:
    if not order.stock_reserved:
        return
    for item in order.items:
        if item.product_id is not None:
            db.execute(update(Product).where(Product.id == item.product_id).values(stock=Product.stock + item.quantity))
    order.stock_reserved = False


def create_order(db: Session, user: User, data: OrderCreateIn, idempotency_key: str) -> tuple[Order, bool]:
    """Crée la commande. Renvoie (commande, créée) ; une clé déjà utilisée renvoie la commande existante."""
    s = get_settings()
    existing = db.scalar(order_query().where(Order.user_id == user.id, Order.idempotency_key == idempotency_key))
    if existing:
        return existing, False

    # Regroupe les lignes en double et contrôle les quantités
    quantities: dict[str, int] = defaultdict(int)
    for item in data.items:
        quantities[item.product_slug] += item.quantity
    if len(quantities) > s.max_items_per_order:
        raise _bad(f"Une commande ne peut pas contenir plus de {s.max_items_per_order} produits différents.")
    for slug, qty in quantities.items():
        if qty > s.max_quantity_per_item:
            raise _bad(f"Quantité maximale par produit : {s.max_quantity_per_item}.")

    products = {p.slug: p for p in db.scalars(select(Product).where(Product.slug.in_(quantities)))}
    order = Order(
        reference=new_order_reference(),
        user_id=user.id,
        idempotency_key=idempotency_key,
        status="pending_payment",
        currency="XOF",
        subtotal_xof=0,
        shipping_fee_xof=s.shipping_fee_xof,
        total_xof=0,
        ship_name=data.shipping.name,
        ship_phone=data.shipping.phone,
        ship_address=data.shipping.address,
        ship_city=data.shipping.city,
        ship_country=data.shipping.country,
        expires_at=utcnow() + timedelta(minutes=s.order_payment_timeout_minutes),
    )
    subtotal = 0
    try:
        for slug, qty in quantities.items():
            product = products.get(slug)
            if product is None or product.status == "archived":
                raise _bad("Un produit de votre panier n'existe plus.", 422)
            if product.status != "available":
                raise _bad(f"« {product.name} » n'est pas encore disponible à la vente.", status.HTTP_409_CONFLICT)
            if not _reserve(db, product.id, qty):
                raise _bad(f"Stock insuffisant pour « {product.name} ».", status.HTTP_409_CONFLICT)
            line = product.price_xof * qty  # prix lu en base, jamais reçu du client
            subtotal += line
            order.items.append(OrderItem(
                product_id=product.id, product_name=product.name,
                unit_price_xof=product.price_xof, quantity=qty, line_total_xof=line,
            ))
        order.subtotal_xof = subtotal
        order.total_xof = subtotal + order.shipping_fee_xof
        order.events.append(OrderEvent(from_status=None, to_status="pending_payment", actor="customer"))
        db.add(order)
        db.commit()
    except HTTPException:
        db.rollback()  # annule aussi les réservations déjà faites
        raise
    except IntegrityError:
        # Course entre deux soumissions identiques : la contrainte d'unicité tranche.
        db.rollback()
        existing = db.scalar(order_query().where(Order.user_id == user.id, Order.idempotency_key == idempotency_key))
        if existing:
            return existing, False
        raise
    log.info("Commande créée %s (%d lignes, total %d XOF)", order.reference, len(order.items), order.total_xof)
    return db.scalar(order_query().where(Order.id == order.id)), True


def cancel_order(db: Session, order: Order, actor: str, note: str = "") -> None:
    if order.status == "cancelled":
        return
    _release(db, order)
    add_event(order, "cancelled", actor, note)


def admin_set_status(db: Session, order: Order, new_status: str, admin: User, note: str) -> None:
    if new_status not in ADMIN_TRANSITIONS.get(order.status, set()):
        raise _bad(f"Transition interdite : {order.status} → {new_status}.", status.HTTP_409_CONFLICT)
    if new_status == "refunded" and order.paid_at is None:
        raise _bad("Seule une commande qui avait été payée peut être marquée remboursée.", status.HTTP_409_CONFLICT)
    if admin.role != "admin" and (order.status, new_status) in STAFF_FORBIDDEN:
        raise _bad("Action réservée aux administrateurs.", status.HTTP_403_FORBIDDEN)
    actor = f"{admin.role}:{admin.id}"
    if new_status == "cancelled":
        if order.status in PAID_STATUSES:
            note = (note + " — remboursement à traiter auprès du prestataire de paiement").strip(" —")
        cancel_order(db, order, actor, note)
    else:
        add_event(order, new_status, actor, note)
    db.commit()


def expire_stale_orders(db: Session) -> int:
    """Annule les commandes non payées dans le délai et libère leur stock."""
    stale = db.scalars(
        order_query().where(Order.status == "pending_payment", Order.expires_at < utcnow())
    ).all()
    for order in stale:
        cancel_order(db, order, "system", "Délai de paiement dépassé")
    if stale:
        db.commit()
        log.info("%d commande(s) expirée(s)", len(stale))
    return len(stale)


def mark_paid(db: Session, order: Order, note: str) -> None:
    """Appelé uniquement après vérification serveur du paiement."""
    if order.status in PAID_STATUSES:
        return
    if order.status == "cancelled":
        # Paiement confirmé après expiration : on tente de réserver à nouveau le stock.
        reserved: list[OrderItem] = []
        ok = True
        for item in order.items:
            if item.product_id is None:
                continue
            if not _reserve(db, item.product_id, item.quantity):
                ok = False
                break
            reserved.append(item)
        if not ok:  # tout ou rien : on rend ce qui vient d'être pris
            for item in reserved:
                db.execute(update(Product).where(Product.id == item.product_id)
                           .values(stock=Product.stock + item.quantity))
        order.stock_reserved = ok
        if not ok:
            note += " — ATTENTION : stock insuffisant après expiration, traitement manuel requis"
            log.warning("Commande %s payée après expiration sans stock disponible", order.reference)
    order.paid_at = utcnow()
    order.expires_at = None
    add_event(order, "paid", "payment", note)

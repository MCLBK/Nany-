"""Espace de gestion.

Deux niveaux, vérifiés côté serveur sur chaque route :
- staff : commandes (consultation, préparation, expédition), stocks, consultation des produits ;
- admin : tout, dont chiffre d'affaires, prix et catalogue, clients, exports, réglages, remboursements.
"""
import csv
import io
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db import get_db, utcnow
from app.deps import require_admin, require_staff
from app.models import ORDER_STATUSES, NotifyRequest, Order, Product, User
from app.schemas import (
    AdminOrderOut, CustomerDetailOut, CustomerOut, OrderOut, OrderStatusIn, ProductAdminOut,
    ProductCreateIn, ProductUpdateIn, StockIn,
)
from app.services import orders as order_service

router = APIRouter(prefix="/admin", tags=["administration"], dependencies=[Depends(require_staff)])

MONTHS_FR = ["Janv", "Févr", "Mars", "Avr", "Mai", "Juin", "Juil", "Août", "Sept", "Oct", "Nov", "Déc"]
PAID = order_service.PAID_STATUSES


def _month_start(d: datetime, shift: int = 0) -> datetime:
    m = d.year * 12 + (d.month - 1) + shift
    return datetime(m // 12, m % 12 + 1, 1)


def _pct(cur: float, prev: float) -> float | None:
    return None if prev == 0 else round((cur - prev) / prev * 100, 1)


def _admin_order(o: Order) -> AdminOrderOut:
    out = AdminOrderOut.model_validate(o)
    out.customer_email = o.user.email if o.user else None
    out.customer_id = o.user_id
    return out


def _order_summary(o: Order) -> dict:
    return {
        "reference": o.reference, "created_at": o.created_at, "status": o.status,
        "payment_status": o.payment_status, "payment_simulated": o.payment_simulated,
        "ship_name": o.ship_name, "total_xof": o.total_xof,
        "items_count": sum(i.quantity for i in o.items),
    }


# --- Vue d'ensemble -------------------------------------------------------------
@router.get("/overview")
def overview(db: Session = Depends(get_db), me: User = Depends(require_staff)):
    order_service.expire_stale_orders(db)
    now = utcnow()
    first = _month_start(now, -5)
    cur, prev = _month_start(now), _month_start(now, -1)

    # Commandes payées sur 6 mois, réparties entre paiements réels et simulés
    paid_orders = db.scalars(order_service.order_query().where(Order.status.in_(PAID), Order.paid_at >= first)).all()
    months = []
    for i in range(-5, 1):
        a, b = _month_start(now, i), _month_start(now, i + 1)
        in_month = [o for o in paid_orders if a <= o.paid_at < b]
        months.append({
            "label": MONTHS_FR[a.month - 1], "month": a.strftime("%Y-%m"),
            "revenue_xof": sum(o.total_xof for o in in_month if not o.payment_simulated),
            "simulated_xof": sum(o.total_xof for o in in_month if o.payment_simulated),
            "orders": len(in_month),
            "real_orders": sum(1 for o in in_month if not o.payment_simulated),
        })
    this_m, last_m = months[-1], months[-2]

    count = lambda *cond: db.scalar(select(func.count()).select_from(Order).where(*cond)) or 0  # noqa: E731
    products = db.scalars(select(Product).where(Product.status != "archived").order_by(Product.sort_order)).all()
    for_sale = [p for p in products if p.status == "available"]
    data = {
        "orders": {
            "paid_this_month": this_m["orders"],
            "paid_delta_pct": _pct(this_m["orders"], last_m["orders"]),
            "to_process": count(Order.status.in_(("paid", "preparing"))),
            "in_transit": count(Order.status == "shipped"),
            "pending_payment": count(Order.status == "pending_payment"),
            "total": count(),
        },
        "products": {
            "for_sale": len(for_sale),
            "coming_soon": sum(1 for p in products if p.status == "coming_soon"),
            "out_of_stock": [{"slug": p.slug, "name": p.name, "stock": p.stock} for p in for_sale if p.stock <= 0],
            "low_stock": [{"slug": p.slug, "name": p.name, "stock": p.stock, "threshold": p.low_stock_threshold}
                          for p in for_sale if 0 < p.stock <= p.low_stock_threshold],
        },
        "recent_orders": [_order_summary(o) for o in db.scalars(
            order_service.order_query().order_by(Order.created_at.desc()).limit(6))],
        "payment_simulated": get_settings().simulator_enabled,
        "revenue": None,
        "customers": None,
    }
    if me.role == "admin":  # données financières et clients : administrateurs uniquement
        data["revenue"] = {
            "this_month_xof": this_m["revenue_xof"],
            "simulated_this_month_xof": this_m["simulated_xof"],
            "delta_pct": _pct(this_m["revenue_xof"], last_m["revenue_xof"]),
            "average_basket_xof": round(this_m["revenue_xof"] / this_m["real_orders"]) if this_m["real_orders"] else 0,
            "by_month": months,
        }
        customers = select(func.count()).select_from(User).where(User.role == "customer", User.is_active.is_(True))
        data["customers"] = {
            "total": db.scalar(customers) or 0,
            "new_this_month": db.scalar(customers.where(User.created_at >= cur)) or 0,
            "new_last_month": db.scalar(customers.where(User.created_at >= prev, User.created_at < cur)) or 0,
        }
    return data


# --- Commandes ------------------------------------------------------------------
@router.get("/orders")
def list_orders(
    q: str | None = Query(None, max_length=80),
    status_filter: str | None = Query(None, alias="status"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=100_000),
    db: Session = Depends(get_db),
):
    if status_filter and status_filter not in ORDER_STATUSES and status_filter != "to_process":
        raise HTTPException(422, "Statut inconnu.")
    order_service.expire_stale_orders(db)
    conds = []
    if status_filter == "to_process":
        conds.append(Order.status.in_(("paid", "preparing")))
    elif status_filter:
        conds.append(Order.status == status_filter)
    if q and q.strip():
        term = q.strip().lower()
        conds.append(or_(
            func.lower(Order.reference).contains(term, autoescape=True),
            func.lower(Order.ship_name).contains(term, autoescape=True),
            func.lower(User.email).contains(term, autoescape=True),
        ))
    base = select(Order).outerjoin(User, Order.user_id == User.id).where(*conds)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.scalars(
        order_service.order_query().outerjoin(User, Order.user_id == User.id).where(*conds)
        .order_by(Order.created_at.desc()).limit(limit).offset(offset)
    ).all()
    return {"total": total, "items": [_order_summary(o) | {"customer_email": o.user.email if o.user else None}
                                      for o in rows]}


def _get_order(db: Session, reference: str) -> Order:
    order = db.scalar(order_service.order_query().where(Order.reference == reference[:32]))
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Commande introuvable.")
    return order


@router.get("/orders/{reference}", response_model=AdminOrderOut)
def get_order(reference: str, db: Session = Depends(get_db)):
    return _admin_order(_get_order(db, reference))


@router.patch("/orders/{reference}/status", response_model=AdminOrderOut)
def set_status(reference: str, data: OrderStatusIn, db: Session = Depends(get_db), me: User = Depends(require_staff)):
    order = _get_order(db, reference)
    order_service.admin_set_status(db, order, data.status, me, data.note)
    return _admin_order(_get_order(db, reference))


def _csv_safe(value) -> str:
    """Neutralise l'injection de formules (=, +, -, @) à l'ouverture dans un tableur."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


@router.get("/orders.csv", dependencies=[Depends(require_admin)])
def export_orders(db: Session = Depends(get_db)):
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Référence", "Date (UTC)", "Statut", "Paiement", "Client", "Destinataire", "Ville", "Pays",
                "Articles", "Total XOF"])
    for o in db.scalars(order_service.order_query().order_by(Order.created_at.desc()).limit(10_000)):
        items = ", ".join(f"{i.product_name} x{i.quantity}" for i in o.items)
        payment = o.payment_status + (" (simulé)" if o.payment_simulated else "")
        w.writerow([_csv_safe(v) for v in (
            o.reference, o.created_at.strftime("%Y-%m-%d %H:%M"), o.status, payment,
            o.user.email if o.user else "", o.ship_name, o.ship_city, o.ship_country, items, o.total_xof,
        )])
    data = "﻿" + buf.getvalue()  # BOM : accents corrects dans Excel
    return StreamingResponse(
        iter([data]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="commandes-nanye-{utcnow():%Y%m%d}.csv"'},
    )


# --- Produits et stocks -----------------------------------------------------------
@router.get("/products", response_model=list[ProductAdminOut])
def admin_products(db: Session = Depends(get_db)):
    return db.scalars(select(Product).order_by(Product.sort_order, Product.id)).all()


def _product(db: Session, slug: str) -> Product:
    product = db.scalar(select(Product).where(Product.slug == slug[:80]))
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Produit introuvable.")
    return product


@router.post("/products", response_model=ProductAdminOut, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_admin)])
def create_product(data: ProductCreateIn, db: Session = Depends(get_db)):
    product = Product(**data.model_dump())
    db.add(product)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Un produit utilise déjà cet identifiant (slug).")
    return product


@router.patch("/products/{slug}", response_model=ProductAdminOut, dependencies=[Depends(require_admin)])
def update_product(slug: str, data: ProductUpdateIn, db: Session = Depends(get_db)):
    product = _product(db, slug)
    for field, value in data.model_dump(exclude_unset=True).items():
        if value is None:
            raise HTTPException(422, f"Le champ « {field} » ne peut pas être vide.")
        setattr(product, field, value)
    db.commit()
    return product


@router.patch("/products/{slug}/stock", response_model=ProductAdminOut)
def update_stock(slug: str, data: StockIn, db: Session = Depends(get_db)):
    """Ajustement de stock (inventaire, réassort) : ouvert au personnel."""
    product = _product(db, slug)
    product.stock = data.stock
    if data.low_stock_threshold is not None:
        product.low_stock_threshold = data.low_stock_threshold
    db.commit()
    return product


@router.get("/notify-requests")
def notify_summary(db: Session = Depends(get_db)):
    rows = db.execute(
        select(Product.slug, Product.name, func.count(NotifyRequest.id))
        .join(NotifyRequest, NotifyRequest.product_id == Product.id)
        .where(NotifyRequest.notified_at.is_(None))
        .group_by(Product.slug, Product.name)
    ).all()
    return [{"product_slug": s, "product_name": n, "pending": c} for s, n, c in rows]


# --- Clients (administrateurs uniquement) ------------------------------------------
def _customer_stats(db: Session, user_ids: list[int]) -> dict[int, dict]:
    if not user_ids:
        return {}
    rows = db.execute(
        select(
            Order.user_id, func.count(Order.id),
            func.coalesce(func.sum(Order.total_xof).filter(Order.status.in_(PAID)), 0),
            func.count(Order.id).filter(Order.status.in_(PAID)),
            func.max(Order.created_at),
        ).where(Order.user_id.in_(user_ids)).group_by(Order.user_id)
    ).all()
    return {uid: {"orders_count": n, "total_spent_xof": int(spent or 0), "paid_orders_count": pn, "last_order_at": last}
            for uid, n, spent, pn, last in rows}


def _customer_out(u: User, stats: dict) -> dict:
    st = stats.get(u.id, {})
    return {
        "id": u.id, "full_name": u.full_name, "email": u.email, "created_at": u.created_at, "is_active": u.is_active,
        "orders_count": st.get("orders_count", 0), "paid_orders_count": st.get("paid_orders_count", 0),
        "total_spent_xof": st.get("total_spent_xof", 0), "last_order_at": st.get("last_order_at"),
    }


@router.get("/customers", dependencies=[Depends(require_admin)])
def list_customers(
    q: str | None = Query(None, max_length=80),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=100_000),
    db: Session = Depends(get_db),
):
    conds = [User.role == "customer", User.is_active.is_(True)]
    if q and q.strip():
        term = q.strip().lower()
        conds.append(or_(func.lower(User.email).contains(term, autoescape=True),
                         func.lower(User.full_name).contains(term, autoescape=True)))
    total = db.scalar(select(func.count()).select_from(User).where(*conds)) or 0
    users = db.scalars(select(User).where(*conds).order_by(User.created_at.desc()).limit(limit).offset(offset)).all()
    stats = _customer_stats(db, [u.id for u in users])
    # Liste volontairement minimale : ni téléphone ni adresse (visibles dans le détail si nécessaire).
    return {"total": total, "items": [CustomerOut(**_customer_out(u, stats)) for u in users]}


@router.get("/customers/{customer_id}", response_model=CustomerDetailOut, dependencies=[Depends(require_admin)])
def get_customer(customer_id: int, db: Session = Depends(get_db)):
    user = db.get(User, customer_id)
    if user is None or user.role != "customer":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Client introuvable.")
    orders = db.scalars(order_service.order_query().where(Order.user_id == user.id)
                        .order_by(Order.created_at.desc()).limit(100)).all()
    return CustomerDetailOut(**_customer_out(user, _customer_stats(db, [user.id])), phone=user.phone,
                             orders=[OrderOut.model_validate(o) for o in orders])


# --- Réglages (lecture seule : ils se modifient dans la configuration du serveur) -----
@router.get("/settings", dependencies=[Depends(require_admin)])
def shop_settings():
    s = get_settings()
    provider = {"cinetpay": "CinetPay", "simulator": "Simulé (aucun encaissement)", "disabled": "Désactivé"}
    return {
        "currency": "XOF",
        "shipping_fee_xof": s.shipping_fee_xof,
        "max_quantity_per_item": s.max_quantity_per_item,
        "order_payment_timeout_minutes": s.order_payment_timeout_minutes,
        "payment_provider": provider[s.payment_provider] if s.payment_provider != "simulator" or s.simulator_enabled else "Désactivé",
        "payment_simulated": s.simulator_enabled,
        "emails_enabled": s.email_backend == "smtp",
        "session_lifetime_hours": s.session_lifetime_hours,
        "environment": s.app_env,
    }

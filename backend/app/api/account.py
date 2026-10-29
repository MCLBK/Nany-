import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.ratelimit import client_ip, enforce
from app.core.security import hash_password, new_token, verify_password
from app.db import get_db
from app.deps import clear_session_cookie, current_user, revoke_user_sessions
from app.models import Order, User
from app.schemas import (
    DeleteAccountIn, OrderCreateIn, OrderOut, PaymentStartOut, ProfileIn, UserOut,
)
from app.services import orders as order_service
from app.services.payments import start_payment

router = APIRouter(tags=["compte et commandes"])
log = logging.getLogger("nanye.account")


def _own_order(db: Session, user: User, reference: str) -> Order:
    """Contrôle d'accès au niveau de l'objet : une commande d'un autre client renvoie 404."""
    order = db.scalar(order_service.order_query().where(
        Order.reference == reference[:32], Order.user_id == user.id
    ))
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Commande introuvable.")
    return order


# --- Profil ---------------------------------------------------------------
@router.patch("/account/profile", response_model=UserOut)
def update_profile(data: ProfileIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    # Nom, téléphone et adresse mémorisée uniquement ; email et rôle ne le sont jamais par cette route.
    changes = data.model_dump(exclude_unset=True)
    if "full_name" in changes and changes["full_name"] is None:
        raise HTTPException(422, "Le nom ne peut pas être vide.")
    for field, value in changes.items():
        setattr(user, field, value)  # None efface une information facultative
    db.commit()
    return user


@router.delete("/account", status_code=status.HTTP_204_NO_CONTENT)
def delete_account(data: DeleteAccountIn, response: Response, db: Session = Depends(get_db),
                   user: User = Depends(current_user)):
    """Suppression du compte : données personnelles anonymisées, commandes conservées (obligations comptables)."""
    enforce("login_email", user.email)
    if not verify_password(user.password_hash, data.password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Mot de passe incorrect.")
    if user.role == "admin":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Un compte administrateur ne peut pas être supprimé ici.")
    revoke_user_sessions(db, user.id)
    user.email = f"supprime-{user.id}-{new_token(6)}@invalid.local"
    user.full_name = "Compte supprimé"
    user.phone = None
    user.password_hash = hash_password(new_token())
    user.is_active = False
    db.commit()
    clear_session_cookie(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    log.info("Compte supprimé (id=%s)", user.id)
    return response


# --- Commandes du client ----------------------------------------------------
@router.get("/account/orders", response_model=list[OrderOut])
def my_orders(db: Session = Depends(get_db), user: User = Depends(current_user)):
    order_service.expire_stale_orders(db)
    return db.scalars(
        order_service.order_query().where(Order.user_id == user.id).order_by(Order.created_at.desc()).limit(100)
    ).all()


@router.get("/orders/{reference}", response_model=OrderOut)
def get_order(reference: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return _own_order(db, user, reference)


@router.post("/orders", response_model=OrderOut, status_code=status.HTTP_201_CREATED)
def create_order(
    data: OrderCreateIn,
    request: Request,
    response: Response,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=16, max_length=64, pattern=r"^[A-Za-z0-9_-]+$"),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    enforce("order_create", f"user:{user.id}")
    enforce("order_create", client_ip(request))
    order_service.expire_stale_orders(db)
    order, created = order_service.create_order(db, user, data, idempotency_key)
    if not created:
        response.status_code = status.HTTP_200_OK  # même clé : même commande, rien n'est recréé
    return order


@router.post("/orders/{reference}/pay", response_model=PaymentStartOut)
def pay_order(reference: str, request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    enforce("payment_start", f"user:{user.id}")
    order_service.expire_stale_orders(db)
    order = _own_order(db, user, reference)
    url = start_payment(db, order)
    return PaymentStartOut(payment_url=url, provider=get_settings().payment_provider)


@router.post("/orders/{reference}/cancel", response_model=OrderOut)
def cancel_my_order(reference: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    order = _own_order(db, user, reference)
    if order.status != "pending_payment":
        raise HTTPException(status.HTTP_409_CONFLICT, "Seule une commande en attente de paiement peut être annulée.")
    # Si un paiement abouti arrive malgré tout ensuite, mark_paid tente de réserver à nouveau le stock.
    order_service.cancel_order(db, order, "customer", "Annulée par le client")
    db.commit()
    return order

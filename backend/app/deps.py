"""Authentification par session (cookie HttpOnly) et contrôles d'accès."""
import hmac
from datetime import timedelta

from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import new_token, token_digest
from app.db import get_db, utcnow
from app.models import AuthSession, User

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _unauthorized() -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentification requise.")


def create_session(db: Session, response: Response, user: User) -> AuthSession:
    s = get_settings()
    token = new_token()
    now = utcnow()
    sess = AuthSession(
        token_hash=token_digest(token),
        csrf_token=new_token(24),
        user_id=user.id,
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(hours=s.session_lifetime_hours),
    )
    db.add(sess)
    db.flush()
    response.set_cookie(
        s.session_cookie_name,
        token,
        max_age=s.session_lifetime_hours * 3600,
        httponly=True,
        secure=s.cookie_secure,
        samesite=s.cookie_samesite,
        domain=s.cookie_domain,
        path="/",
    )
    return sess


def clear_session_cookie(response: Response) -> None:
    s = get_settings()
    response.delete_cookie(
        s.session_cookie_name, path="/", domain=s.cookie_domain,
        secure=s.cookie_secure, httponly=True, samesite=s.cookie_samesite,
    )


def revoke_user_sessions(db: Session, user_id: int, except_id: int | None = None) -> None:
    now = utcnow()
    for sess in db.scalars(
        select(AuthSession).where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
    ):
        if sess.id != except_id:
            sess.revoked_at = now


def get_session_optional(request: Request, db: Session = Depends(get_db)) -> AuthSession | None:
    s = get_settings()
    token = request.cookies.get(s.session_cookie_name)
    if not token or len(token) > 200:
        return None
    sess = db.scalar(select(AuthSession).where(AuthSession.token_hash == token_digest(token)))
    now = utcnow()
    if (
        sess is None
        or sess.revoked_at is not None
        or sess.expires_at <= now
        or sess.last_seen_at <= now - timedelta(hours=s.session_idle_hours)
        or not sess.user.is_active
    ):
        return None
    if now - sess.last_seen_at > timedelta(minutes=5):  # limite les écritures
        sess.last_seen_at = now
        db.commit()
    return sess


def get_session(sess: AuthSession | None = Depends(get_session_optional)) -> AuthSession:
    if sess is None:
        raise _unauthorized()
    return sess


def check_csrf(request: Request, sess: AuthSession) -> None:
    """Jeton synchronisé lié à la session, transmis dans l'en-tête X-CSRF-Token.

    Un site tiers ne peut ni lire ce jeton (il n'est renvoyé qu'en JSON à une origine
    autorisée par CORS) ni envoyer cet en-tête sans pré-vérification CORS.
    """
    if request.method not in UNSAFE_METHODS:
        return
    sent = request.headers.get("x-csrf-token", "")
    if not sent or not hmac.compare_digest(sent, sess.csrf_token):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Jeton CSRF manquant ou invalide.")


def current_user(request: Request, sess: AuthSession = Depends(get_session)) -> User:
    check_csrf(request, sess)
    return sess.user


BACKOFFICE_ROLES = ("staff", "admin")


def require_staff(user: User = Depends(current_user)) -> User:
    """Espace de gestion : personnel (commandes, stocks) et administrateurs."""
    if user.role not in BACKOFFICE_ROLES:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Accès refusé.")
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    """Actions sensibles : prix, catalogue, clients, exports, réglages, remboursements."""
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Accès refusé.")
    return user

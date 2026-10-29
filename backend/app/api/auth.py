import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.ratelimit import client_ip, enforce
from app.core.security import hash_password, needs_rehash, new_token, token_digest, verify_password
from app.db import get_db, utcnow
from app.deps import (
    check_csrf, clear_session_cookie, create_session, get_session, get_session_optional, revoke_user_sessions,
)
from app.models import AuthSession, PasswordResetToken, User
from app.schemas import ChangePasswordIn, ForgotIn, LoginIn, RegisterIn, ResetIn, SessionOut, UserOut
from app.services.email import send_email

router = APIRouter(prefix="/auth", tags=["authentification"])
log = logging.getLogger("nanye.auth")

BAD_CREDENTIALS = "Email ou mot de passe incorrect."


def _session_out(sess: AuthSession) -> SessionOut:
    return SessionOut(user=UserOut.model_validate(sess.user), csrf_token=sess.csrf_token)


def _check_password_policy(password: str, email: str) -> None:
    if password.lower() == email.lower() or password.lower() in email.lower():
        raise HTTPException(422, "Le mot de passe ne doit pas reprendre votre email.")


@router.post("/register", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
def register(data: RegisterIn, request: Request, response: Response, db: Session = Depends(get_db)):
    enforce("register", client_ip(request))
    _check_password_policy(data.password, data.email)
    if db.scalar(select(User.id).where(User.email == data.email)):
        # Message volontairement neutre ; le rôle n'est jamais fixé par le client.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Inscription impossible avec ces informations. Si vous avez déjà un compte, connectez-vous.",
        )
    user = User(email=data.email, password_hash=hash_password(data.password), full_name=data.full_name, role="customer")
    db.add(user)
    db.flush()
    sess = create_session(db, response, user)
    db.commit()
    log.info("Compte créé (id=%s)", user.id)
    return _session_out(sess)


@router.post("/login", response_model=SessionOut)
def login(data: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    email = data.email.strip().lower()
    enforce("login_ip", client_ip(request))
    enforce("login_email", email)
    user = db.scalar(select(User).where(User.email == email))
    ok = verify_password(user.password_hash if user else None, data.password)
    if not ok or user is None or not user.is_active:
        log.info("Échec de connexion")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, BAD_CREDENTIALS)
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(data.password)
    sess = create_session(db, response, user)  # nouvelle session à chaque connexion (pas de fixation)
    db.commit()
    return _session_out(sess)


@router.get("/me", response_model=SessionOut)
def me(sess: AuthSession = Depends(get_session)):
    return _session_out(sess)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response, db: Session = Depends(get_db),
           sess: AuthSession | None = Depends(get_session_optional)):
    if sess is not None:
        check_csrf(request, sess)
        sess.revoked_at = utcnow()
        db.commit()
    clear_session_cookie(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
def logout_all(request: Request, response: Response, db: Session = Depends(get_db),
               sess: AuthSession = Depends(get_session)):
    check_csrf(request, sess)
    revoke_user_sessions(db, sess.user_id)
    db.commit()
    clear_session_cookie(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post("/password/change", status_code=status.HTTP_204_NO_CONTENT)
def change_password(data: ChangePasswordIn, request: Request, response: Response,
                    db: Session = Depends(get_db), sess: AuthSession = Depends(get_session)):
    check_csrf(request, sess)
    enforce("login_email", sess.user.email)
    if not verify_password(sess.user.password_hash, data.current_password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Mot de passe actuel incorrect.")
    _check_password_policy(data.new_password, sess.user.email)
    sess.user.password_hash = hash_password(data.new_password)
    revoke_user_sessions(db, sess.user_id, except_id=sess.id)  # déconnecte les autres appareils
    db.commit()
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post("/password/forgot", status_code=status.HTTP_202_ACCEPTED)
def forgot_password(data: ForgotIn, request: Request, db: Session = Depends(get_db)):
    email = data.email.strip().lower()
    enforce("password_forgot", client_ip(request))
    enforce("password_forgot", "email:" + email)
    user = db.scalar(select(User).where(User.email == email, User.is_active.is_(True)))
    if user:
        s = get_settings()
        token = new_token()
        db.add(PasswordResetToken(
            token_hash=token_digest(token), user_id=user.id,
            expires_at=utcnow() + timedelta(minutes=s.password_reset_ttl_minutes),
        ))
        db.commit()
        link = f"{s.frontend_base_url}#reset/{token}"  # fragment : jamais transmis aux serveurs
        send_email(
            user.email, "NANYÉ — Réinitialisation de votre mot de passe",
            f"Bonjour {user.full_name},\n\nPour choisir un nouveau mot de passe, ouvrez ce lien "
            f"(valable {s.password_reset_ttl_minutes} minutes, utilisable une seule fois) :\n\n{link}\n\n"
            "Si vous n'êtes pas à l'origine de cette demande, ignorez ce message.\n\nNANYÉ",
        )
    # Réponse identique que le compte existe ou non.
    return {"detail": "Si un compte correspond à cet email, un lien de réinitialisation vient d'être envoyé."}


@router.post("/password/reset", status_code=status.HTTP_204_NO_CONTENT)
def reset_password(data: ResetIn, request: Request, response: Response, db: Session = Depends(get_db)):
    enforce("password_reset", client_ip(request))
    rec = db.scalar(select(PasswordResetToken).where(PasswordResetToken.token_hash == token_digest(data.token)))
    if rec is None or rec.used_at is not None or rec.expires_at <= utcnow():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Lien invalide ou expiré. Faites une nouvelle demande.")
    user = db.get(User, rec.user_id)
    _check_password_policy(data.new_password, user.email)
    user.password_hash = hash_password(data.new_password)
    now = utcnow()
    for other in db.scalars(select(PasswordResetToken).where(
        PasswordResetToken.user_id == user.id, PasswordResetToken.used_at.is_(None)
    )):
        other.used_at = now  # invalide aussi les autres liens en circulation
    revoke_user_sessions(db, user.id)
    db.commit()
    response.status_code = status.HTTP_204_NO_CONTENT
    return response

"""Envoi d'emails transactionnels (bibliothèque standard, aucune dépendance)."""
import logging
import smtplib
import ssl
from email.message import EmailMessage

from app.core.config import get_settings

log = logging.getLogger("nanye.email")


def send_email(to: str, subject: str, body: str) -> bool:
    """Renvoie True seulement si le message a réellement été remis au serveur SMTP."""
    s = get_settings()
    if s.email_backend == "disabled":
        log.warning("Email non envoyé : EMAIL_BACKEND=disabled (sujet : %s)", subject)
        return False
    if s.email_backend == "console":
        # Développement uniquement (interdit en production par la configuration).
        log.info("[EMAIL DE DÉVELOPPEMENT — non envoyé]\nÀ : %s\nSujet : %s\n\n%s", to, subject, body)
        return False

    msg = EmailMessage()
    msg["From"] = s.smtp_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15) as smtp:
            if s.smtp_starttls:
                smtp.starttls(context=ssl.create_default_context())
            if s.smtp_username:
                smtp.login(s.smtp_username, s.smtp_password or "")
            smtp.send_message(msg)
        return True
    except (OSError, smtplib.SMTPException) as exc:
        log.error("Échec d'envoi SMTP (%s)", type(exc).__name__)
        return False

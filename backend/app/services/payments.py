"""Paiements : interface commune, simulateur (développement) et CinetPay.

Principe : une commande ne passe à « payée » qu'après une vérification serveur à
serveur auprès du prestataire (montant, devise, référence). Ni la redirection du
navigateur ni le contenu brut d'une notification ne suffisent.
"""
import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass
from typing import Literal, Protocol

import httpx
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Order, Payment
from app.services.orders import mark_paid

log = logging.getLogger("nanye.payments")

Outcome = Literal["succeeded", "failed", "cancelled", "pending"]


@dataclass
class Verified:
    outcome: Outcome
    amount_xof: int | None
    currency: str | None
    provider_status: str


class PaymentProvider(Protocol):
    name: str

    def start(self, order: Order, payment: Payment) -> str:
        """Crée la transaction chez le prestataire et renvoie l'URL de paiement."""

    def verify(self, payment: Payment) -> Verified:
        """Interroge le prestataire (serveur à serveur) sur l'état réel de la transaction."""


class PaymentUnavailable(Exception):
    pass


# --- Simulateur (développement uniquement) ------------------------------------
class SimulatorProvider:
    """Prestataire fictif : aucun argent ne circule. En production, uniquement si
    ALLOW_SIMULATED_PAYMENTS=true (mode démonstration affiché sur le site)."""

    name = "simulator"
    _results: dict[str, Outcome] = {}

    def start(self, order: Order, payment: Payment) -> str:
        return f"{get_settings().api_base_url}/api/payments/simulator/{payment.transaction_id}"

    def verify(self, payment: Payment) -> Verified:
        outcome = self._results.get(payment.transaction_id, "pending")
        return Verified(outcome, payment.amount_xof if outcome == "succeeded" else None, "XOF", f"SIM_{outcome.upper()}")


# --- CinetPay -----------------------------------------------------------------
# Champs concaténés (dans cet ordre) pour l'empreinte HMAC-SHA256 de l'en-tête x-token,
# selon la documentation CinetPay « Sécuriser la notification ». À revérifier sur la
# documentation en vigueur avant la mise en production.
CINETPAY_TOKEN_FIELDS = (
    "cpm_site_id", "cpm_trans_id", "cpm_trans_date", "cpm_amount", "cpm_currency", "signature",
    "payment_method", "cel_phone_num", "cpm_phone_prefixe", "cpm_language", "cpm_version",
    "cpm_payment_config", "cpm_page_action", "cpm_custom", "cpm_designation", "cpm_error_message",
)


def cinetpay_expected_token(form: dict[str, str], secret: str) -> str:
    data = "".join(form.get(k, "") for k in CINETPAY_TOKEN_FIELDS)
    return hmac.new(secret.encode(), data.encode(), hashlib.sha256).hexdigest()


class CinetPayProvider:
    name = "cinetpay"

    def __init__(self) -> None:
        s = get_settings()
        if not (s.cinetpay_api_key and s.cinetpay_site_id):
            raise PaymentUnavailable("CinetPay n'est pas configuré")
        self.s = s

    def _post(self, path: str, payload: dict) -> dict:
        try:
            with httpx.Client(timeout=self.s.payment_http_timeout_seconds) as client:
                r = client.post(f"{self.s.cinetpay_base_url}{path}", json=payload)
            return r.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.error("CinetPay injoignable (%s)", type(exc).__name__)
            raise PaymentUnavailable("Prestataire de paiement injoignable") from exc

    def start(self, order: Order, payment: Payment) -> str:
        name_parts = order.ship_name.split(" ", 1)
        payload = {
            "apikey": self.s.cinetpay_api_key,
            "site_id": self.s.cinetpay_site_id,
            "transaction_id": payment.transaction_id,
            "amount": payment.amount_xof,
            "currency": "XOF",
            "description": f"Commande {order.reference}",
            "notify_url": f"{self.s.api_base_url}/api/payments/cinetpay/notify",
            "return_url": f"{self.s.frontend_base_url}#order/{order.reference}",
            "channels": self.s.cinetpay_channels,
            "lang": "FR",
            "metadata": order.reference,
            "customer_name": name_parts[-1],
            "customer_surname": name_parts[0],
            "customer_phone_number": order.ship_phone,
            "customer_address": order.ship_address,
            "customer_city": order.ship_city,
            "customer_country": order.ship_country,
        }
        data = self._post("/payment", payload)
        url = (data.get("data") or {}).get("payment_url")
        if str(data.get("code")) != "201" or not url:
            log.error("CinetPay a refusé l'initialisation (code %s)", data.get("code"))
            raise PaymentUnavailable("Initialisation du paiement refusée")
        return url

    def verify(self, payment: Payment) -> Verified:
        data = self._post("/payment/check", {
            "apikey": self.s.cinetpay_api_key,
            "site_id": self.s.cinetpay_site_id,
            "transaction_id": payment.transaction_id,
        })
        info = data.get("data") or {}
        provider_status = str(info.get("status") or data.get("message") or "UNKNOWN")[:40]
        outcome: Outcome = {"ACCEPTED": "succeeded", "REFUSED": "failed", "CANCELED": "cancelled",
                            "CANCELLED": "cancelled"}.get(provider_status.upper(), "pending")
        try:
            amount = int(float(info.get("amount"))) if info.get("amount") is not None else None
        except (TypeError, ValueError):
            amount = None
        return Verified(outcome, amount, info.get("currency"), provider_status)


def get_provider() -> PaymentProvider:
    s = get_settings()
    if s.simulator_enabled:
        return SimulatorProvider()
    if s.payment_provider == "cinetpay":
        return CinetPayProvider()
    raise PaymentUnavailable("Aucun moyen de paiement n'est configuré")


# --- Orchestration --------------------------------------------------------------
def start_payment(db: Session, order: Order) -> str:
    if order.status != "pending_payment":
        raise HTTPException(status.HTTP_409_CONFLICT, "Cette commande n'est pas en attente de paiement.")
    try:
        provider = get_provider()
    except PaymentUnavailable:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "Le paiement en ligne n'est pas encore disponible. Votre commande est enregistrée.")
    # Nouvel identifiant à chaque tentative : une URL de paiement n'est jamais réutilisée.
    payment = Payment(
        order_id=order.id, provider=provider.name, transaction_id="TX" + secrets.token_hex(12).upper(),
        amount_xof=order.total_xof, currency=order.currency, status="initiated",
    )
    db.add(payment)
    db.flush()
    try:
        payment.payment_url = provider.start(order, payment)
    except PaymentUnavailable:
        db.rollback()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Le prestataire de paiement est indisponible. Réessayez plus tard.")
    payment.status = "pending"
    db.commit()
    log.info("Paiement initié %s pour %s via %s", payment.transaction_id, order.reference, provider.name)
    return payment.payment_url


def reconcile(db: Session, payment: Payment, provider: PaymentProvider | None = None) -> Payment:
    """Vérifie la transaction auprès du prestataire et applique le résultat (idempotent)."""
    if payment.status == "succeeded":
        return payment  # état terminal : notifications répétées sans effet
    provider = provider or get_provider()
    result = provider.verify(payment)
    payment.provider_status = result.provider_status
    if result.outcome == "succeeded":
        if result.amount_xof != payment.amount_xof or (result.currency or "").upper() != payment.currency:
            log.error("Montant/devise incohérents pour %s : refusé", payment.transaction_id)
            payment.status = "failed"
            payment.provider_status = "AMOUNT_MISMATCH"
        else:
            payment.status = "succeeded"
            mark_paid(db, payment.order, f"Paiement {payment.provider} {payment.transaction_id} vérifié")
    elif result.outcome in ("failed", "cancelled"):
        payment.status = result.outcome
    db.commit()
    log.info("Paiement %s : %s", payment.transaction_id, payment.status)
    return payment

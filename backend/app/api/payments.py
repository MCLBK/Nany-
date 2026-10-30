import hmac
import html
import logging
from urllib.parse import parse_qsl

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.account import _own_order
from app.core.config import get_settings
from app.core.ratelimit import client_ip, enforce
from app.db import get_db
from app.deps import current_user
from app.models import Order, Payment, User
from app.schemas import OrderOut
from app.services import orders as order_service
from app.services.payments import (
    PaymentUnavailable, SimulatorProvider, cinetpay_expected_token, reconcile,
)

router = APIRouter(tags=["paiements"])
log = logging.getLogger("nanye.payments")


# --- Vérification à la demande (retour du client après paiement) --------------
@router.post("/orders/{reference}/verify-payment", response_model=OrderOut)
def verify_payment(reference: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Le retour navigateur ne prouve rien : on interroge le prestataire, serveur à serveur."""
    enforce("payment_start", f"user:{user.id}")
    order = _own_order(db, user, reference)
    pending = [p for p in order.payments if p.status in ("pending", "initiated")]
    if pending:
        try:
            reconcile(db, pending[-1])
        except PaymentUnavailable:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Vérification momentanément impossible.")
    return db.scalar(order_service.order_query().where(Order.id == order.id))


# --- Notification CinetPay (webhook) ------------------------------------------
@router.get("/payments/cinetpay/notify", include_in_schema=False)
def cinetpay_ping():
    # CinetPay vérifie la disponibilité de l'URL par un GET.
    return PlainTextResponse("ok")


@router.post("/payments/cinetpay/notify", include_in_schema=False)
async def cinetpay_notify(request: Request, db: Session = Depends(get_db)):
    enforce("webhook", client_ip(request))
    s = get_settings()
    if s.payment_provider != "cinetpay" or not s.cinetpay_secret_key:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Introuvable.")
    raw = await request.body()  # taille déjà bornée par le middleware
    form = dict(parse_qsl(raw.decode("utf-8", "replace"), keep_blank_values=True))
    token = request.headers.get("x-token", "")
    expected = cinetpay_expected_token(form, s.cinetpay_secret_key)
    if not token or not hmac.compare_digest(token, expected):
        log.warning("Notification CinetPay rejetée : signature invalide")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Signature invalide.")
    if form.get("cpm_site_id") != s.cinetpay_site_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Site inconnu.")
    payment = db.scalar(select(Payment).where(Payment.transaction_id == form.get("cpm_trans_id", "")[:64]))
    if payment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Transaction inconnue.")
    # La notification sert uniquement de déclencheur : l'état fait foi chez le prestataire.
    try:
        reconcile(db, payment)
    except PaymentUnavailable:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Vérification impossible, réessayez.")
    return PlainTextResponse("ok")


# --- Simulateur ----------------------------------------------------------------
# Disponible en développement, ou en production si ALLOW_SIMULATED_PAYMENTS=true
# (mode démonstration). Il passe par le même chemin de vérification que CinetPay.
def _simulator_enabled() -> None:
    if not get_settings().simulator_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Introuvable.")


SIMULATOR_PAGE = """<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">
<title>Paiement simulé — NANYÉ</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;min-height:100vh;display:grid;place-items:center;padding:16px;
background:#2A1D16;color:#FFF8F0;font:16px/1.6 system-ui,-apple-system,"Segoe UI",sans-serif}}
main{{width:100%;max-width:440px;background:#38271E;border:1px solid rgba(255,248,240,.12);border-radius:14px;overflow:hidden}}
.flag{{background:#E3BC4E;color:#2A1D16;font-size:13px;font-weight:600;padding:10px 24px;letter-spacing:.02em}}
.body{{padding:24px}}h1{{font:400 30px/1.15 Georgia,"Times New Roman",serif;margin:0 0 4px}}
.ref{{color:#CDBFB2;font-size:14px;margin:0 0 20px}}
.line{{display:flex;justify-content:space-between;gap:12px;padding:8px 0;border-bottom:1px solid rgba(255,248,240,.08);font-size:15px}}
.total{{display:flex;justify-content:space-between;align-items:baseline;padding:16px 0 20px;font-weight:600}}
.total span:last-child{{font:400 28px Georgia,serif;font-variant-numeric:lining-nums}}
button{{display:block;width:100%;min-height:50px;border-radius:50px;font:600 14px system-ui,sans-serif;letter-spacing:.06em;
text-transform:uppercase;margin-top:10px;cursor:pointer}}
.ok{{background:#E3BC4E;border:0;color:#2A1D16}}.ko{{background:none;border:1px solid rgba(255,248,240,.3);color:#FFF8F0}}
.cancel{{background:none;border:0;color:#CDBFB2;text-transform:none;letter-spacing:0;font-weight:400;text-decoration:underline}}
p.note{{color:#CDBFB2;font-size:13px;margin:16px 0 0}}
</style></head><body><main>
<div class="flag">Paiement simulé — aucune somme ne sera débitée</div>
<div class="body"><h1>Paiement</h1><p class="ref">Commande {ref}</p>
{lines}
<div class="total"><span>Total</span><span>{total} FCFA</span></div>
<form method="post" action="{action}">
<button class="ok" name="result" value="success">Simuler un paiement accepté</button>
<button class="ko" name="result" value="failure">Simuler un paiement refusé</button>
<button class="cancel" name="result" value="cancel">Abandonner et revenir à la boutique</button>
</form>
<p class="note">Cette page remplace celle du prestataire de paiement tant que les paiements réels ne sont pas activés.</p>
</div></main></body></html>"""


def _fmt(n: int) -> str:
    return f"{n:,}".replace(",", " ")


@router.get("/payments/simulator/{tx}", response_class=HTMLResponse, include_in_schema=False)
def simulator_page(tx: str, db: Session = Depends(get_db)):
    _simulator_enabled()
    payment = db.scalar(select(Payment).where(Payment.transaction_id == tx[:64]))
    if payment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Transaction inconnue.")
    order = payment.order
    lines = "".join(
        f'<div class="line"><span>{html.escape(i.product_name)} × {i.quantity}</span><span>{_fmt(i.line_total_xof)} F</span></div>'
        for i in order.items
    )
    if order.shipping_fee_xof:
        lines += f'<div class="line"><span>Livraison</span><span>{_fmt(order.shipping_fee_xof)} F</span></div>'
    return HTMLResponse(SIMULATOR_PAGE.format(
        ref=html.escape(order.reference), lines=lines, total=_fmt(payment.amount_xof),
        action=f"/api/payments/simulator/{html.escape(payment.transaction_id)}/complete",
    ))


@router.post("/payments/simulator/{tx}/complete", include_in_schema=False)
async def simulator_complete(tx: str, request: Request, db: Session = Depends(get_db)):
    _simulator_enabled()
    form = dict(parse_qsl((await request.body()).decode("utf-8", "replace")))
    payment = db.scalar(select(Payment).where(Payment.transaction_id == tx[:64]))
    if payment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Transaction inconnue.")
    outcome = {"success": "succeeded", "failure": "failed", "cancel": "cancelled"}.get(form.get("result", ""), "failed")
    SimulatorProvider._results[payment.transaction_id] = outcome
    # Même chemin que le vrai prestataire : vérification puis application du résultat.
    reconcile(db, payment, SimulatorProvider())
    SimulatorProvider._results.pop(payment.transaction_id, None)
    return RedirectResponse(f"{get_settings().frontend_base_url}#order/{payment.order.reference}", status_code=303)

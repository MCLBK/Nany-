from urllib.parse import urlencode

import pytest
from conftest import ORIGIN, headers, new_client, order_payload, register, stock_of

from app.core.config import Settings, get_settings
from app.db import SessionLocal
from app.models import Order, Payment
from app.services import payments as pay
from app.services.payments import Verified, cinetpay_expected_token


def _pending_order(c):
    csrf = register(c)
    ref = c.post("/api/orders", headers=headers(csrf), json=order_payload(2)).json()["reference"]
    return csrf, ref


def _start(c, csrf, ref):
    r = c.post(f"/api/orders/{ref}/pay", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.text
    return r.json()["payment_url"].rsplit("/", 1)[1]


def _status(ref):
    with SessionLocal() as db:
        return db.query(Order).filter_by(reference=ref).one().status


# --- Simulateur (même chemin de vérification que le vrai prestataire) -------------------
def test_simulated_payment_success_marks_paid(client):
    csrf, ref = _pending_order(client)
    tx = _start(client, csrf, ref)
    r = client.post(f"/api/payments/simulator/{tx}/complete", data={"result": "success"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith(f"#order/{ref}")
    assert _status(ref) == "paid"


def test_failed_payment_keeps_order_pending(client):
    csrf, ref = _pending_order(client)
    tx = _start(client, csrf, ref)
    client.post(f"/api/payments/simulator/{tx}/complete", data={"result": "failure"}, follow_redirects=False)
    assert _status(ref) == "pending_payment"
    with SessionLocal() as db:
        assert db.query(Payment).filter_by(transaction_id=tx).one().status == "failed"


def test_browser_return_alone_never_marks_paid(client):
    csrf, ref = _pending_order(client)
    _start(client, csrf, ref)
    # Le client revient sur le site (ou forge l'URL de retour) sans paiement confirmé
    r = client.post(f"/api/orders/{ref}/verify-payment", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200 and r.json()["status"] == "pending_payment"


def test_each_attempt_gets_new_transaction(client):
    csrf, ref = _pending_order(client)
    assert _start(client, csrf, ref) != _start(client, csrf, ref)


def test_cannot_pay_twice(client):
    csrf, ref = _pending_order(client)
    tx = _start(client, csrf, ref)
    client.post(f"/api/payments/simulator/{tx}/complete", data={"result": "success"}, follow_redirects=False)
    assert client.post(f"/api/orders/{ref}/pay", headers={"X-CSRF-Token": csrf}).status_code == 409


def test_payment_disabled_is_honest(client, monkeypatch):
    csrf, ref = _pending_order(client)
    monkeypatch.setattr(get_settings(), "payment_provider", "disabled")
    r = client.post(f"/api/orders/{ref}/pay", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 503 and "pas encore disponible" in r.json()["detail"]


def test_amount_mismatch_is_refused(client):
    csrf, ref = _pending_order(client)
    tx = _start(client, csrf, ref)

    class Cheater:
        name = "simulator"

        def verify(self, p):
            return Verified("succeeded", 100, "XOF", "ACCEPTED")

    with SessionLocal() as db:
        p = db.query(Payment).filter_by(transaction_id=tx).one()
        pay.reconcile(db, p, Cheater())
        assert p.status == "failed" and p.provider_status == "AMOUNT_MISMATCH"
    assert _status(ref) == "pending_payment"


def test_payment_after_expiry_rereserves_stock(client):
    csrf, ref = _pending_order(client)
    tx = _start(client, csrf, ref)
    client.post(f"/api/orders/{ref}/cancel", headers={"X-CSRF-Token": csrf})
    assert stock_of("moutarde-naturelle") == 10
    client.post(f"/api/payments/simulator/{tx}/complete", data={"result": "success"}, follow_redirects=False)
    assert _status(ref) == "paid" and stock_of("moutarde-naturelle") == 8


# --- Webhook CinetPay -------------------------------------------------------------
@pytest.fixture
def cinetpay(monkeypatch):
    s = get_settings()
    for k, v in {"payment_provider": "cinetpay", "cinetpay_api_key": "test-api-key",
                 "cinetpay_site_id": "123456", "cinetpay_secret_key": "test-secret"}.items():
        monkeypatch.setattr(s, k, v)
    calls = {"verify": 0, "result": Verified("succeeded", 8000, "XOF", "ACCEPTED")}

    def fake_post(self, path, payload):  # tests simulés : aucun appel réseau vers CinetPay
        if path == "/payment":
            return {"code": "201", "data": {"payment_url": "https://checkout.cinetpay.test/pay/abc"}}
        calls["verify"] += 1
        r = calls["result"]
        return {"code": "00", "data": {"status": r.provider_status, "amount": str(r.amount_xof), "currency": r.currency}}

    monkeypatch.setattr(pay.CinetPayProvider, "_post", fake_post)
    return calls


def _notify(c, tx, secret="test-secret", site="123456", token=None):
    form = {"cpm_site_id": site, "cpm_trans_id": tx, "cpm_trans_date": "2026-10-29 10:00:00",
            "cpm_amount": "8000", "cpm_currency": "XOF", "signature": "sig", "payment_method": "OM"}
    token = token if token is not None else cinetpay_expected_token(form, secret)
    return c.post("/api/payments/cinetpay/notify", content=urlencode(form),
                  headers={"Content-Type": "application/x-www-form-urlencoded", "x-token": token})


def _cinetpay_tx(c, csrf, ref):
    r = c.post(f"/api/orders/{ref}/pay", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200 and r.json()["payment_url"].startswith("https://checkout.cinetpay.test")
    with SessionLocal() as db:
        return db.query(Payment).filter_by(order_id=db.query(Order).filter_by(reference=ref).one().id).one().transaction_id


def test_webhook_rejects_bad_signature(client, cinetpay):
    csrf, ref = _pending_order(client)
    tx = _cinetpay_tx(client, csrf, ref)
    assert _notify(client, tx, token="").status_code == 401
    assert _notify(client, tx, secret="mauvais-secret").status_code == 401
    assert _notify(client, tx, site="999").status_code in (400, 401)
    assert cinetpay["verify"] == 0 and _status(ref) == "pending_payment"


def test_webhook_valid_then_replay_is_idempotent(client, cinetpay):
    csrf, ref = _pending_order(client)
    tx = _cinetpay_tx(client, csrf, ref)
    assert _notify(client, tx).status_code == 200
    assert _status(ref) == "paid" and cinetpay["verify"] == 1
    for _ in range(3):
        assert _notify(client, tx).status_code == 200
    assert cinetpay["verify"] == 1  # paiement terminal : rejeu sans effet
    with SessionLocal() as db:
        events = [e.to_status for e in db.query(Order).filter_by(reference=ref).one().events]
    assert events.count("paid") == 1


def test_webhook_trusts_provider_check_not_notification(client, cinetpay):
    csrf, ref = _pending_order(client)
    tx = _cinetpay_tx(client, csrf, ref)
    cinetpay["result"] = Verified("failed", None, None, "REFUSED")
    assert _notify(client, tx).status_code == 200  # notification signée, mais paiement refusé
    assert _status(ref) == "pending_payment"


def test_webhook_unknown_transaction(client, cinetpay):
    assert _notify(client, "TXINCONNU").status_code == 404


# --- En-têtes, CORS, erreurs, configuration -----------------------------------------
def test_security_headers(client):
    r = client.get("/api/health")
    assert r.json() == {"status": "ok"}
    for h in ("x-content-type-options", "referrer-policy", "content-security-policy", "x-frame-options"):
        assert h in r.headers
    assert "server" not in r.headers or "uvicorn" not in r.headers.get("server", "")


def test_cors_only_for_listed_origins(client):
    ok = client.options("/api/products", headers={**ORIGIN, "Access-Control-Request-Method": "GET"})
    assert ok.headers.get("access-control-allow-origin") == "http://front.test"
    assert ok.headers.get("access-control-allow-credentials") == "true"
    bad = client.options("/api/products", headers={"Origin": "https://evil.test", "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in bad.headers


def test_unlisted_origin_cannot_write(client):
    csrf = register(client)
    r = client.post("/api/orders", headers={**headers(csrf), "Origin": "https://evil.test"}, json=order_payload())
    assert r.status_code == 403


def test_body_size_limited(client):
    r = client.post("/api/auth/login", content=b"{" + b" " * 40_000 + b"}", headers={"Content-Type": "application/json"})
    assert r.status_code == 413


def test_non_json_body_rejected(client):
    r = client.post("/api/auth/login", content="email=a&password=b",
                    headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code == 422


def test_unhandled_errors_are_generic(client, monkeypatch):
    import app.api.catalog as catalog

    def boom(*a, **k):
        raise RuntimeError("detail interne secret: postgres://user:pass@host")

    monkeypatch.setattr(catalog, "to_public", boom)
    r = client.get("/api/products")
    assert r.status_code == 500
    assert "secret" not in r.text and "Traceback" not in r.text and "request_id" in r.json()


def test_simulator_unavailable_outside_simulator_mode(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "payment_provider", "cinetpay")
    assert client.get("/api/payments/simulator/TX123").status_code == 404


def test_production_config_refuses_permissive_settings():
    bad = Settings(app_env="production", debug=True, cookie_secure=False, cors_origins="*",
                   database_url="sqlite:///x.db", payment_provider="simulator", email_backend="console")
    problems = " ".join(bad.production_problems())
    for needle in ("DEBUG", "COOKIE_SECURE", "PostgreSQL", "CORS", "simulator", "console"):
        assert needle in problems
    good = Settings(app_env="production", cookie_secure=True, cors_origins="https://nanye.com",
                    database_url="postgresql://u:p@h/db", frontend_base_url="https://nanye.com/index.html",
                    api_base_url="https://api.nanye.com", payment_provider="cinetpay",
                    cinetpay_api_key="k", cinetpay_site_id="s", cinetpay_secret_key="x", email_backend="disabled")
    assert good.production_problems() == []
    assert good.database_url.startswith("postgresql+psycopg://")


def test_docs_disabled_in_production_flag():
    # La documentation interactive n'est exposée qu'hors production (voir app.main).
    assert get_settings().is_production is False
    assert new_client().get("/api/docs").status_code == 200

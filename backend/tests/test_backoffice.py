"""Espace de gestion : rôles, permissions et nouvelles fonctions."""
from conftest import headers, login, new_client, order_payload, register, stock_of

from app.core.config import Settings, get_settings
from app.core.security import hash_password
from app.db import SessionLocal
from app.models import Payment, User
from app.services import payments as pay
from app.services.payments import Verified


def _account(email: str, role: str) -> tuple:
    with SessionLocal() as db:
        db.add(User(email=email, full_name=role.title(), password_hash=hash_password("motdepasse-equipe"), role=role))
        db.commit()
    c = new_client()
    return c, {"X-CSRF-Token": login(c, email, "motdepasse-equipe")}


def _paid_order(qty: int = 2) -> tuple:
    """Commande payée via le simulateur. Renvoie (client, csrf, référence)."""
    c = new_client()
    csrf = register(c, f"client{qty}@example.com")
    ref = c.post("/api/orders", headers=headers(csrf), json=order_payload(qty)).json()["reference"]
    tx = c.post(f"/api/orders/{ref}/pay", headers={"X-CSRF-Token": csrf}).json()["payment_url"].rsplit("/", 1)[1]
    c.post(f"/api/payments/simulator/{tx}/complete", data={"result": "success"}, follow_redirects=False)
    return c, csrf, ref


# --- Séparation des rôles ------------------------------------------------------------
def test_staff_handles_orders_and_stock_but_not_money_or_customers():
    _, _, ref = _paid_order()
    staff, h = _account("equipe@example.com", "staff")
    ov = staff.get("/api/admin/overview")
    assert ov.status_code == 200
    assert ov.json()["revenue"] is None and ov.json()["customers"] is None  # moindre privilège
    assert staff.get("/api/admin/orders").json()["total"] == 1
    assert staff.patch(f"/api/admin/orders/{ref}/status", headers=h, json={"status": "preparing"}).status_code == 200
    assert staff.patch("/api/admin/products/moutarde-naturelle/stock", headers=h, json={"stock": 30}).status_code == 200
    for path in ("/api/admin/customers", "/api/admin/settings", "/api/admin/orders.csv"):
        assert staff.get(path).status_code == 403, path
    assert staff.patch("/api/admin/products/moutarde-naturelle", headers=h, json={"price_xof": 1}).status_code == 403
    assert staff.post("/api/admin/products", headers=h, json={}).status_code == 403
    r = staff.patch(f"/api/admin/orders/{ref}/status", headers=h, json={"status": "cancelled"})
    assert r.status_code == 403  # annuler une commande payée = argent : administrateur uniquement


def test_customer_cannot_reach_any_backoffice_route(client):
    csrf = register(client)
    h = {"X-CSRF-Token": csrf}
    for path in ("/api/admin/overview", "/api/admin/orders", "/api/admin/orders/NNY-XXXX", "/api/admin/products",
                 "/api/admin/customers", "/api/admin/customers/1", "/api/admin/settings"):
        assert client.get(path).status_code == 403, path
    assert client.patch("/api/admin/products/moutarde-naturelle/stock", headers=h, json={"stock": 999}).status_code == 403
    assert stock_of("moutarde-naturelle") == 10


def test_visitor_gets_401_everywhere_in_backoffice(client):
    for path in ("/api/admin/overview", "/api/admin/orders", "/api/admin/customers", "/api/admin/settings"):
        assert client.get(path).status_code == 401, path


def test_role_cannot_be_raised_from_the_browser(client):
    csrf = register(client)
    h = {"X-CSRF-Token": csrf}
    for body in ({"role": "staff"}, {"role": "admin"}, {"is_active": True}):
        assert client.patch("/api/account/profile", headers=h, json=body).status_code == 422
    assert client.get("/api/auth/me").json()["user"]["role"] == "customer"


# --- Commandes ------------------------------------------------------------------------
def test_refund_flow_admin_only_and_only_for_paid_orders():
    _, _, ref = _paid_order()
    admin, h = _account("admin@example.com", "admin")
    assert admin.patch(f"/api/admin/orders/{ref}/status", headers=h, json={"status": "refunded"}).status_code == 409
    r = admin.patch(f"/api/admin/orders/{ref}/status", headers=h, json={"status": "cancelled", "note": "rupture"})
    assert r.status_code == 200 and stock_of("moutarde-naturelle") == 10
    r = admin.patch(f"/api/admin/orders/{ref}/status", headers=h, json={"status": "refunded"})
    assert r.status_code == 200 and r.json()["status"] == "refunded"
    actors = [e["actor"] for e in r.json()["events"]]
    assert actors[-1].startswith("admin:")
    # Une commande annulée sans avoir été payée ne peut pas être « remboursée »
    c = new_client()
    csrf = register(c, "autre@example.com")
    ref2 = c.post("/api/orders", headers=headers(csrf), json=order_payload(1)).json()["reference"]
    c.post(f"/api/orders/{ref2}/cancel", headers={"X-CSRF-Token": csrf})
    assert admin.patch(f"/api/admin/orders/{ref2}/status", headers=h, json={"status": "refunded"}).status_code == 409


def test_order_search_filters_and_pagination():
    _, _, ref = _paid_order(1)
    c = new_client()
    csrf = register(c, "awa.kone@example.com")
    for _ in range(3):
        c.post("/api/orders", headers=headers(csrf), json=order_payload(1))
    admin, _ = _account("admin@example.com", "admin")
    assert admin.get("/api/admin/orders").json()["total"] == 4
    page = admin.get("/api/admin/orders?limit=2&offset=2").json()
    assert page["total"] == 4 and len(page["items"]) == 2
    assert admin.get("/api/admin/orders?status=to_process").json()["total"] == 1
    assert admin.get("/api/admin/orders?status=pending_payment").json()["total"] == 3
    assert admin.get(f"/api/admin/orders?q={ref[-5:].lower()}").json()["items"][0]["reference"] == ref
    assert admin.get("/api/admin/orders?q=awa.kone").json()["total"] == 3
    assert admin.get("/api/admin/orders?q=%25").json()["total"] == 0  # joker SQL échappé
    assert admin.get("/api/admin/orders?status=inconnu").status_code == 422
    detail = admin.get(f"/api/admin/orders/{ref}").json()
    assert detail["payment_status"] == "succeeded" and detail["ship_address"] and detail["items"]


def test_customer_sees_payment_status_of_own_order():
    c, csrf, ref = _paid_order()
    o = c.get(f"/api/orders/{ref}").json()
    assert o["payment_status"] == "succeeded" and o["payment_simulated"] is True
    assert "actor" not in o["events"][0]  # détails internes réservés à la gestion


# --- Clients ----------------------------------------------------------------------------
def test_customers_list_is_minimal_and_detail_is_complete():
    c, csrf, _ = _paid_order(3)
    c.patch("/api/account/profile", headers={"X-CSRF-Token": csrf}, json={"phone": "+229 97 11 22 33"})
    admin, _ = _account("admin@example.com", "admin")
    _account("equipe@example.com", "staff")
    page = admin.get("/api/admin/customers").json()
    assert page["total"] == 1  # le personnel et les admins ne sont pas des clients
    row = page["items"][0]
    assert row["paid_orders_count"] == 1 and row["total_spent_xof"] == 3 * 3500 + 1000
    assert "phone" not in row and "password_hash" not in str(row)
    detail = admin.get(f"/api/admin/customers/{row['id']}").json()
    assert detail["phone"] == "+229 97 11 22 33" and len(detail["orders"]) == 1
    with SessionLocal() as db:
        staff_id = db.query(User).filter_by(email="equipe@example.com").one().id
    assert admin.get(f"/api/admin/customers/{staff_id}").status_code == 404
    assert admin.get("/api/admin/customers?q=client3").json()["total"] == 1
    assert admin.get("/api/admin/customers?q=personne").json()["total"] == 0


# --- Produits et stocks -------------------------------------------------------------------
def test_stock_threshold_drives_public_availability(client):
    admin, h = _account("admin@example.com", "admin")
    r = admin.patch("/api/admin/products/moutarde-naturelle/stock", headers=h, json={"stock": 8, "low_stock_threshold": 10})
    assert r.status_code == 200 and r.json()["low_stock_threshold"] == 10
    assert client.get("/api/products/moutarde-naturelle").json()["availability"] == "low_stock"
    ov = admin.get("/api/admin/overview").json()
    assert ov["products"]["low_stock"][0]["slug"] == "moutarde-naturelle"
    admin.patch("/api/admin/products/moutarde-naturelle/stock", headers=h, json={"stock": 0})
    assert client.get("/api/products/moutarde-naturelle").json()["availability"] == "out_of_stock"
    assert admin.get("/api/admin/overview").json()["products"]["out_of_stock"][0]["slug"] == "moutarde-naturelle"
    assert admin.patch("/api/admin/products/moutarde-naturelle/stock", headers=h, json={"stock": -1}).status_code == 422


def test_full_product_edit_reflected_publicly(client):
    admin, h = _account("admin@example.com", "admin")
    r = admin.patch("/api/admin/products/moutarde-epicee", headers=h, json={
        "status": "available", "price_xof": 4000, "stock": 12, "description": "Nouvelle description",
        "images": ["images/product-hero.jpg"]})
    assert r.status_code == 200
    p = client.get("/api/products/moutarde-epicee").json()
    assert p["status"] == "available" and p["price_xof"] == 4000 and p["description"] == "Nouvelle description"
    admin.patch("/api/admin/products/moutarde-epicee", headers=h, json={"status": "archived"})
    assert client.get("/api/products/moutarde-epicee").status_code == 404


# --- Profil client -----------------------------------------------------------------------
def test_saved_delivery_address(client):
    csrf = register(client)
    h = {"X-CSRF-Token": csrf}
    r = client.patch("/api/account/profile", headers=h, json={
        "default_address": "Rue 12, Haie Vive", "default_city": "Cotonou", "default_country": "BJ"})
    assert r.status_code == 200 and r.json()["default_city"] == "Cotonou"
    assert client.get("/api/auth/me").json()["user"]["default_country"] == "BJ"
    assert client.patch("/api/account/profile", headers=h, json={"default_country": "benin"}).status_code == 422


# --- Paiements simulés et chiffre d'affaires réel ---------------------------------------------
def test_simulated_payment_cancel_keeps_order_open(client):
    csrf = register(client)
    ref = client.post("/api/orders", headers=headers(csrf), json=order_payload(1)).json()["reference"]
    tx = client.post(f"/api/orders/{ref}/pay", headers={"X-CSRF-Token": csrf}).json()["payment_url"].rsplit("/", 1)[1]
    page = client.get(f"/api/payments/simulator/{tx}")
    assert "aucune somme ne sera débitée" in page.text and "Moutarde Naturelle" in page.text
    client.post(f"/api/payments/simulator/{tx}/complete", data={"result": "cancel"}, follow_redirects=False)
    o = client.get(f"/api/orders/{ref}").json()
    assert o["status"] == "pending_payment" and o["payment_status"] == "cancelled"


def test_simulated_payments_in_production_require_explicit_flag():
    base = dict(app_env="production", cookie_secure=True, cors_origins="https://nanye.com",
                database_url="postgresql://u:p@h/db", frontend_base_url="https://nanye.com/index.html",
                api_base_url="https://api.nanye.com", payment_provider="simulator", email_backend="disabled")
    refused = Settings(**base)
    assert any("ALLOW_SIMULATED_PAYMENTS" in p for p in refused.production_problems())
    assert refused.simulator_enabled is False
    demo = Settings(**base, allow_simulated_payments=True)
    assert demo.production_problems() == [] and demo.simulator_enabled is True


def test_real_revenue_counted_separately_from_simulated(monkeypatch):
    _paid_order(2)  # 8 000 FCFA simulés
    s = get_settings()
    for k, v in {"payment_provider": "cinetpay", "cinetpay_api_key": "k", "cinetpay_site_id": "1",
                 "cinetpay_secret_key": "s"}.items():
        monkeypatch.setattr(s, k, v)
    monkeypatch.setattr(pay.CinetPayProvider, "_post", lambda self, path, payload: (
        {"code": "201", "data": {"payment_url": "https://checkout.cinetpay.test/x"}} if path == "/payment"
        else {"code": "00", "data": {"status": "ACCEPTED", "amount": "4500", "currency": "XOF"}}))
    c = new_client()
    csrf = register(c, "reel@example.com")
    ref = c.post("/api/orders", headers=headers(csrf), json=order_payload(1)).json()["reference"]
    c.post(f"/api/orders/{ref}/pay", headers={"X-CSRF-Token": csrf})
    with SessionLocal() as db:
        p = db.query(Payment).filter_by(provider="cinetpay").one()
        pay.reconcile(db, p)
    admin, _ = _account("admin@example.com", "admin")
    rev = admin.get("/api/admin/overview").json()["revenue"]
    assert rev["this_month_xof"] == 4500 and rev["simulated_this_month_xof"] == 8000
    assert rev["average_basket_xof"] == 4500  # panier moyen calculé sur les seuls paiements réels


def test_profile_optional_fields_can_be_cleared_but_name_cannot(client):
    csrf = register(client)
    h = {"X-CSRF-Token": csrf}
    client.patch("/api/account/profile", headers=h, json={"phone": "+229 97 00 00 01", "default_city": "Cotonou"})
    r = client.patch("/api/account/profile", headers=h, json={"phone": None, "default_city": None})
    assert r.status_code == 200 and r.json()["phone"] is None and r.json()["default_city"] is None
    r = client.patch("/api/account/profile", headers=h, json={"full_name": None})
    assert r.status_code == 422 and "Erreur interne" not in r.text

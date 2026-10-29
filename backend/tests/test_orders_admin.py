from datetime import timedelta

from conftest import headers, login, make_admin, new_client, order_payload, register, stock_of

from app.db import SessionLocal, utcnow
from app.models import Order


def _order(c, csrf, qty=2, key=None):
    return c.post("/api/orders", headers=headers(csrf, key), json=order_payload(qty))


# --- Création de commande -------------------------------------------------------
def test_order_requires_authentication(client):
    assert client.post("/api/orders", json=order_payload()).status_code == 401


def test_order_requires_csrf(client):
    register(client)
    r = client.post("/api/orders", headers={"Idempotency-Key": "a" * 32}, json=order_payload())
    assert r.status_code == 403


def test_valid_order_uses_server_prices_and_reserves_stock(client):
    csrf = register(client)
    r = _order(client, csrf, qty=3)
    assert r.status_code == 201, r.text
    o = r.json()
    assert o["status"] == "pending_payment"
    assert o["subtotal_xof"] == 3 * 3500 and o["shipping_fee_xof"] == 1000 and o["total_xof"] == 11500
    assert o["reference"].startswith("NNY-") and len(o["reference"]) == 14
    assert stock_of("moutarde-naturelle") == 7


def test_client_supplied_price_is_refused(client):
    csrf = register(client)
    payload = order_payload()
    payload["items"][0]["unit_price_xof"] = 1
    assert client.post("/api/orders", headers=headers(csrf), json=payload).status_code == 422
    payload = order_payload()
    payload["total_xof"] = 1
    assert client.post("/api/orders", headers=headers(csrf), json=payload).status_code == 422
    assert stock_of("moutarde-naturelle") == 10


def test_invalid_quantities_refused(client):
    csrf = register(client)
    for qty in (0, -1, 1001):
        assert _order(client, csrf, qty=qty).status_code == 422
    assert _order(client, csrf, qty=21).status_code == 400  # au-delà du plafond par article
    # Doublons de ligne additionnés avant contrôle du plafond
    payload = order_payload(15)
    payload["items"].append({"product_slug": "moutarde-naturelle", "quantity": 15})
    assert client.post("/api/orders", headers=headers(csrf), json=payload).status_code == 400
    assert client.post("/api/orders", headers=headers(csrf), json={**order_payload(), "items": "x"}).status_code == 422


def test_insufficient_stock_refused_atomically(client):
    csrf = register(client)
    assert _order(client, csrf, qty=8).status_code == 201
    r = _order(client, csrf, qty=3)
    assert r.status_code == 409 and "Stock insuffisant" in r.json()["detail"]
    assert stock_of("moutarde-naturelle") == 2  # rien n'a été prélevé par la commande refusée


def test_unavailable_products_cannot_be_bought(client):
    csrf = register(client)
    assert client.post("/api/orders", headers=headers(csrf), json=order_payload(1, "moutarde-epicee")).status_code == 409
    assert client.post("/api/orders", headers=headers(csrf), json=order_payload(1, "ancien-produit")).status_code == 422
    assert client.post("/api/orders", headers=headers(csrf), json=order_payload(1, "inexistant")).status_code == 422


def test_double_submission_returns_same_order(client):
    csrf = register(client)
    key = "cle-idempotence-0123456789"
    first = _order(client, csrf, qty=2, key=key)
    second = _order(client, csrf, qty=2, key=key)
    assert first.status_code == 201 and second.status_code == 200
    assert first.json()["reference"] == second.json()["reference"]
    assert stock_of("moutarde-naturelle") == 8  # stock prélevé une seule fois
    assert len(client.get("/api/account/orders").json()) == 1


def test_idempotency_key_required_and_validated(client):
    csrf = register(client)
    assert client.post("/api/orders", headers={"X-CSRF-Token": csrf}, json=order_payload()).status_code == 422
    bad = {"X-CSRF-Token": csrf, "Idempotency-Key": "court"}
    assert client.post("/api/orders", headers=bad, json=order_payload()).status_code == 422


def test_customers_cannot_see_each_others_orders(client):
    csrf = register(client, "awa@example.com")
    ref = _order(client, csrf).json()["reference"]
    other = new_client()
    csrf2 = register(other, "kofi@example.com")
    assert other.get(f"/api/orders/{ref}").status_code == 404
    assert other.post(f"/api/orders/{ref}/pay", headers={"X-CSRF-Token": csrf2}).status_code == 404
    assert other.post(f"/api/orders/{ref}/cancel", headers={"X-CSRF-Token": csrf2}).status_code == 404
    assert other.get("/api/account/orders").json() == []
    assert client.get(f"/api/orders/{ref}").status_code == 200


def test_cancel_releases_stock(client):
    csrf = register(client)
    ref = _order(client, csrf, qty=4).json()["reference"]
    assert stock_of("moutarde-naturelle") == 6
    r = client.post(f"/api/orders/{ref}/cancel", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert stock_of("moutarde-naturelle") == 10
    assert client.post(f"/api/orders/{ref}/cancel", headers={"X-CSRF-Token": csrf}).status_code == 409


def test_expired_unpaid_orders_release_stock(client):
    csrf = register(client)
    ref = _order(client, csrf, qty=5).json()["reference"]
    with SessionLocal() as db:
        db.query(Order).filter_by(reference=ref).update({"expires_at": utcnow() - timedelta(minutes=1)})
        db.commit()
    orders = client.get("/api/account/orders").json()
    assert orders[0]["status"] == "cancelled"
    assert stock_of("moutarde-naturelle") == 10


# --- Administration -------------------------------------------------------------
def test_admin_routes_refused_to_anonymous_and_customers(client):
    assert client.get("/api/admin/overview").status_code == 401
    csrf = register(client)
    for path in ("/api/admin/overview", "/api/admin/orders", "/api/admin/products", "/api/admin/orders.csv",
                 "/api/admin/notify-requests", "/api/admin/customers", "/api/admin/settings"):
        assert client.get(path).status_code == 403, path
    assert client.post("/api/admin/products", headers={"X-CSRF-Token": csrf}, json={}).status_code == 403
    assert client.patch("/api/admin/products/moutarde-naturelle", headers={"X-CSRF-Token": csrf},
                        json={"price_xof": 1}).status_code == 403


def _admin_client():
    make_admin()
    c = new_client()
    return c, login(c, "admin@example.com", "admin-motdepasse")


def test_admin_stats_reflect_real_data(client):
    csrf = register(client)
    ref = _order(client, csrf, qty=2).json()["reference"]
    pay = client.post(f"/api/orders/{ref}/pay", headers={"X-CSRF-Token": csrf}).json()
    tx = pay["payment_url"].rsplit("/", 1)[1]
    client.post(f"/api/payments/simulator/{tx}/complete", data={"result": "success"}, follow_redirects=False)
    admin, _ = _admin_client()
    s = admin.get("/api/admin/overview").json()
    # Paiement passé par le simulateur : compté à part, jamais comme chiffre d'affaires réel
    assert s["revenue"]["this_month_xof"] == 0 and s["revenue"]["simulated_this_month_xof"] == 8000
    assert s["orders"]["paid_this_month"] == 1 and s["orders"]["to_process"] == 1
    assert s["customers"]["total"] == 1 and s["customers"]["new_this_month"] == 1
    assert len(s["revenue"]["by_month"]) == 6 and s["payment_simulated"] is True
    assert s["recent_orders"][0]["payment_simulated"] is True


def test_admin_status_transitions_enforced(client):
    csrf = register(client)
    ref = _order(client, csrf).json()["reference"]
    admin, acsrf = _admin_client()
    h = {"X-CSRF-Token": acsrf}
    # « payé » ne peut pas être forcé à la main
    assert admin.patch(f"/api/admin/orders/{ref}/status", headers=h, json={"status": "paid"}).status_code == 409
    assert admin.patch(f"/api/admin/orders/{ref}/status", headers=h, json={"status": "shipped"}).status_code == 409
    assert admin.patch(f"/api/admin/orders/{ref}/status", json={"status": "cancelled"}).status_code == 403  # CSRF
    r = admin.patch(f"/api/admin/orders/{ref}/status", headers=h, json={"status": "cancelled", "note": "doublon"})
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert stock_of("moutarde-naturelle") == 10
    assert r.json()["customer_email"] == "awa@example.com"


def test_admin_product_management(client):
    admin, acsrf = _admin_client()
    h = {"X-CSRF-Token": acsrf}
    new = {"slug": "coffret-decouverte", "name": "Coffret Découverte", "category": "coffrets",
           "price_xof": 12000, "stock": 5, "status": "available", "images": ["images/product-hero.jpg"]}
    assert admin.post("/api/admin/products", headers=h, json=new).status_code == 201
    assert admin.post("/api/admin/products", headers=h, json=new).status_code == 409
    assert admin.post("/api/admin/products", headers=h, json={**new, "slug": "x2", "price_xof": -5}).status_code == 422
    for bad in ("javascript:alert(1)", "images/../../etc/passwd", "http://exemple.com/a.jpg"):
        r = admin.post("/api/admin/products", headers=h, json={**new, "slug": "img-test", "images": [bad]})
        assert r.status_code == 422, bad
    r = admin.patch("/api/admin/products/moutarde-naturelle", headers=h, json={"stock": 50, "price_xof": 3600})
    assert r.status_code == 200 and r.json()["stock"] == 50
    assert admin.patch("/api/admin/products/moutarde-naturelle", headers=h, json={"stock": None}).status_code == 422
    assert client.get("/api/products/coffret-decouverte").json()["price_xof"] == 12000


def test_csv_export_neutralizes_formulas(client):
    c = new_client()
    csrf = c.post("/api/auth/register", json={"email": "evil@example.com", "password": "motdepasse-solide",
                                              "full_name": "Mal"}).json()["csrf_token"]
    payload = order_payload(1)
    payload["shipping"]["name"] = "=HYPERLINK(\"http://x\")"
    assert c.post("/api/orders", headers=headers(csrf), json=payload).status_code == 201
    admin, _ = _admin_client()
    r = admin.get("/api/admin/orders.csv")
    assert r.status_code == 200 and "text/csv" in r.headers["content-type"]
    assert "'=HYPERLINK" in r.text and ";=HYPERLINK" not in r.text

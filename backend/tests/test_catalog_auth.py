from conftest import login, make_admin, new_client, register

import app.api.auth as auth_api


# --- Catalogue ------------------------------------------------------------
def test_catalog_lists_public_products_without_stock(client):
    r = client.get("/api/products")
    assert r.status_code == 200
    slugs = [p["slug"] for p in r.json()]
    assert slugs == ["moutarde-naturelle", "moutarde-epicee"]  # l'archivé est masqué
    naturelle = r.json()[0]
    assert "stock" not in naturelle  # le stock exact n'est pas publié
    assert naturelle["availability"] == "in_stock" and naturelle["max_quantity"] == 10
    assert r.json()[1]["availability"] == "coming_soon" and r.json()[1]["max_quantity"] == 0


def test_catalog_filter_and_detail(client):
    assert [p["slug"] for p in client.get("/api/products?category=epice").json()] == ["moutarde-epicee"]
    assert client.get("/api/products?category=inconnue").status_code == 422
    assert client.get("/api/products/moutarde-naturelle").json()["price_xof"] == 3500
    assert client.get("/api/products/ancien-produit").status_code == 404
    assert client.get("/api/products/nexiste-pas").status_code == 404


def test_notify_request_validated_and_not_revealing(client):
    body = {"email": "fan@example.com", "product_slug": "moutarde-epicee"}
    assert client.post("/api/notify", json=body).status_code == 202
    assert client.post("/api/notify", json=body).status_code == 202  # doublon : même réponse
    assert client.post("/api/notify", json={**body, "product_slug": "moutarde-naturelle"}).status_code == 404
    assert client.post("/api/notify", json={**body, "email": "pas-un-email"}).status_code == 422


def test_notify_rate_limited(client):
    for i in range(10):
        client.post("/api/notify", json={"email": f"f{i}@example.com", "product_slug": "moutarde-epicee"})
    r = client.post("/api/notify", json={"email": "x@example.com", "product_slug": "moutarde-epicee"})
    assert r.status_code == 429


# --- Inscription / connexion ---------------------------------------------------
def test_register_sets_httponly_cookie_and_never_returns_hash(client):
    r = client.post("/api/auth/register", json={"email": "Awa@Example.com", "password": "motdepasse-solide",
                                                 "full_name": "Awa"})
    assert r.status_code == 201
    cookie = r.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie
    body = r.json()
    assert body["user"]["email"] == "awa@example.com" and body["user"]["role"] == "customer"
    assert "password" not in r.text and "argon2" not in r.text


def test_register_cannot_choose_role(client):
    r = client.post("/api/auth/register", json={"email": "a@example.com", "password": "motdepasse-solide",
                                                 "full_name": "Awa", "role": "admin"})
    assert r.status_code == 422  # champ inattendu refusé


def test_register_password_rules(client):
    weak = client.post("/api/auth/register", json={"email": "a@example.com", "password": "court", "full_name": "Awa"})
    assert weak.status_code == 422
    same = client.post("/api/auth/register", json={"email": "abcdefghij@example.com",
                                                    "password": "abcdefghij@example.com", "full_name": "Awa"})
    assert same.status_code == 422


def test_duplicate_register_is_generic(client):
    register(client)
    r = new_client().post("/api/auth/register", json={"email": "awa@example.com", "password": "autre-motdepasse",
                                                       "full_name": "Autre"})
    assert r.status_code == 409 and "connectez-vous" in r.json()["detail"]


def test_login_errors_do_not_reveal_account_existence(client):
    register(client)
    c = new_client()
    wrong_pw = c.post("/api/auth/login", json={"email": "awa@example.com", "password": "mauvais-mdp"})
    unknown = c.post("/api/auth/login", json={"email": "inconnu@example.com", "password": "mauvais-mdp"})
    assert wrong_pw.status_code == unknown.status_code == 401
    assert wrong_pw.json() == unknown.json()


def test_login_rate_limited_per_email(client):
    register(client)
    c = new_client()
    for _ in range(5):
        assert c.post("/api/auth/login", json={"email": "awa@example.com", "password": "faux-faux"}).status_code == 401
    # Même le bon mot de passe est refusé tant que la fenêtre n'est pas écoulée
    r = c.post("/api/auth/login", json={"email": "awa@example.com", "password": "motdepasse-solide"})
    assert r.status_code == 429


def test_validation_errors_never_echo_input(client):
    r = client.post("/api/auth/login", json={"email": "a", "password": "S3cret-Ne-Pas-Afficher" * 10})
    assert r.status_code == 422
    assert "S3cret" not in r.text


# --- Sessions -------------------------------------------------------------
def test_me_requires_session(client):
    assert client.get("/api/auth/me").status_code == 401
    register(client)
    assert client.get("/api/auth/me").json()["user"]["email"] == "awa@example.com"


def test_logout_requires_csrf_and_revokes_session(client):
    csrf = register(client)
    cookie = client.cookies.get("nanye_session")
    assert client.post("/api/auth/logout").status_code == 403  # sans jeton CSRF
    assert client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 204
    # Le jeton volé avant la déconnexion ne fonctionne plus
    attacker = new_client()
    attacker.cookies.set("nanye_session", cookie)
    assert attacker.get("/api/auth/me").status_code == 401


def test_forged_or_garbage_cookie_rejected(client):
    client.cookies.set("nanye_session", "x" * 43)
    assert client.get("/api/auth/me").status_code == 401
    client.cookies.set("nanye_session", "y" * 5000)
    assert client.get("/api/auth/me").status_code == 401


def test_password_change_revokes_other_sessions(client):
    csrf = register(client)
    other = new_client()
    login(other, "awa@example.com", "motdepasse-solide")
    r = client.post("/api/auth/password/change", headers={"X-CSRF-Token": csrf},
                    json={"current_password": "motdepasse-solide", "new_password": "nouveau-motdepasse"})
    assert r.status_code == 204
    assert other.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me").status_code == 200


def test_password_reset_flow_single_use(client, monkeypatch):
    register(client)
    sent = {}
    monkeypatch.setattr(auth_api, "send_email", lambda to, subject, body: sent.update(to=to, body=body) or True)
    c = new_client()
    assert c.post("/api/auth/password/forgot", json={"email": "awa@example.com"}).status_code == 202
    assert c.post("/api/auth/password/forgot", json={"email": "personne@example.com"}).status_code == 202
    token = sent["body"].split("#reset/")[1].split()[0]
    assert c.post("/api/auth/password/reset", json={"token": token, "new_password": "tout-nouveau-mdp"}).status_code == 204
    # Lien à usage unique, et toutes les sessions existantes sont révoquées
    assert c.post("/api/auth/password/reset", json={"token": token, "new_password": "encore-un-mdp-x"}).status_code == 400
    assert client.get("/api/auth/me").status_code == 401
    login(new_client(), "awa@example.com", "tout-nouveau-mdp")


def test_profile_update_cannot_escalate(client):
    csrf = register(client)
    h = {"X-CSRF-Token": csrf}
    assert client.patch("/api/account/profile", headers=h, json={"role": "admin"}).status_code == 422
    assert client.patch("/api/account/profile", headers=h, json={"email": "x@example.com"}).status_code == 422
    r = client.patch("/api/account/profile", headers=h, json={"full_name": "Awa K.", "phone": "+229 97 11 22 33"})
    assert r.status_code == 200 and r.json()["role"] == "customer"


def test_account_deletion_anonymizes(client):
    csrf = register(client)
    r = client.request("DELETE", "/api/account", headers={"X-CSRF-Token": csrf}, json={"password": "motdepasse-solide"})
    assert r.status_code == 204
    assert new_client().post("/api/auth/login", json={"email": "awa@example.com",
                                                       "password": "motdepasse-solide"}).status_code == 401


def test_admin_cannot_be_created_via_api(client):
    make_admin()
    csrf = register(client)
    # Aucune route ne permet de changer de rôle
    assert client.patch("/api/account/profile", headers={"X-CSRF-Token": csrf}, json={"role": "admin"}).status_code == 422
    assert client.get("/api/auth/me").json()["user"]["role"] == "customer"


def test_shop_config_is_public_and_minimal(client):
    r = client.get("/api/shop-config")
    assert r.status_code == 200
    assert r.json() == {"currency": "XOF", "shipping_fee_xof": 1000, "max_quantity_per_item": 20,
                        "payment_enabled": True, "payment_simulated": True}

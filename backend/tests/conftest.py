"""Base de test isolée (SQLite temporaire), recréée pour chaque test."""
import os
import tempfile
import uuid

_tmp = tempfile.mkdtemp(prefix="nanye-tests-")
os.environ.update({
    "APP_ENV": "test",
    "DATABASE_URL": f"sqlite:///{_tmp}/test.db",
    "CORS_ORIGINS": "http://front.test",
    "FRONTEND_BASE_URL": "http://front.test/index.html",
    "API_BASE_URL": "http://testserver",
    "PAYMENT_PROVIDER": "simulator",
    "EMAIL_BACKEND": "disabled",
    "ARGON2_MEMORY_COST_KIB": "1024",  # hachage allégé pour la vitesse des tests uniquement
    "ARGON2_TIME_COST": "1",
    "ARGON2_PARALLELISM": "1",
    "SHIPPING_FEE_XOF": "1000",
})

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.ratelimit import limiter  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Product, User  # noqa: E402

ORIGIN = {"Origin": "http://front.test"}


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    limiter.reset()
    with SessionLocal() as db:
        db.add_all([
            Product(slug="moutarde-naturelle", name="Moutarde Naturelle", category="naturelle",
                    status="available", price_xof=3500, stock=10, images=["images/product-hero.jpg"]),
            Product(slug="moutarde-epicee", name="Moutarde Épicée", category="epice",
                    status="coming_soon", price_xof=0, stock=0),
            Product(slug="ancien-produit", name="Ancien", category="naturelle",
                    status="archived", price_xof=2000, stock=5),
        ])
        db.commit()
    yield


def new_client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def client() -> TestClient:
    return new_client()


def register(c: TestClient, email: str = "awa@example.com", password: str = "motdepasse-solide") -> str:
    r = c.post("/api/auth/register", json={"email": email, "password": password, "full_name": "Awa Test"})
    assert r.status_code == 201, r.text
    return r.json()["csrf_token"]


def make_admin(email: str = "admin@example.com", password: str = "admin-motdepasse") -> None:
    with SessionLocal() as db:
        db.add(User(email=email, full_name="Admin", password_hash=hash_password(password), role="admin"))
        db.commit()


def login(c: TestClient, email: str, password: str) -> str:
    r = c.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["csrf_token"]


SHIPPING = {"name": "Awa Test", "phone": "+229 97 00 00 00", "address": "Rue 12, Haie Vive",
            "city": "Cotonou", "country": "BJ"}


def order_payload(qty: int = 2, slug: str = "moutarde-naturelle") -> dict:
    return {"items": [{"product_slug": slug, "quantity": qty}], "shipping": SHIPPING}


def headers(csrf: str, key: str | None = None) -> dict:
    h = {"X-CSRF-Token": csrf}
    h["Idempotency-Key"] = key or uuid.uuid4().hex
    return h


def stock_of(slug: str) -> int:
    with SessionLocal() as db:
        return db.query(Product).filter_by(slug=slug).one().stock

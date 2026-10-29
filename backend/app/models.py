"""Modèles de données. Montants en francs CFA (XOF), entiers : pas de centimes."""
from datetime import datetime

from sqlalchemy import (
    JSON, Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, utcnow

ROLES = ("customer", "staff", "admin")  # staff : commandes et stocks ; admin : tout
PRODUCT_STATUSES = ("available", "coming_soon", "archived")
CATEGORIES = ("naturelle", "epice", "herbes", "coffrets")
ORDER_STATUSES = ("pending_payment", "paid", "preparing", "shipped", "delivered", "cancelled", "refunded")
PAYMENT_STATUSES = ("initiated", "pending", "succeeded", "failed", "cancelled")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint(_in("role", ROLES), name="ck_users_role"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(120))
    phone: Mapped[str | None] = mapped_column(String(30))
    # Adresse de livraison mémorisée (facultative, pré-remplit la commande)
    default_address: Mapped[str | None] = mapped_column(String(200))
    default_city: Mapped[str | None] = mapped_column(String(80))
    default_country: Mapped[str | None] = mapped_column(String(2))
    role: Mapped[str] = mapped_column(String(20), default="customer")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    orders: Mapped[list["Order"]] = relationship(back_populates="user")


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Seule l'empreinte SHA-256 du jeton est stockée : une fuite de la base ne donne pas de session.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)

    user: Mapped[User] = relationship()


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime)


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint("price_xof >= 0", name="ck_products_price"),
        CheckConstraint("stock >= 0", name="ck_products_stock"),
        CheckConstraint(_in("status", PRODUCT_STATUSES), name="ck_products_status"),
        CheckConstraint(_in("category", CATEGORIES), name="ck_products_category"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    tagline: Mapped[str] = mapped_column(String(200), default="")
    description: Mapped[str] = mapped_column(String(400), default="")
    long_description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(20))
    format: Mapped[str] = mapped_column(String(40), default="200 g")
    price_xof: Mapped[int] = mapped_column(Integer)
    stock: Mapped[int] = mapped_column(Integer, default=0)
    low_stock_threshold: Mapped[int] = mapped_column(Integer, default=5, server_default="5")
    status: Mapped[str] = mapped_column(String(20), default="coming_soon")
    images: Mapped[list] = mapped_column(JSON, default=list)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_orders_idempotency"),
        CheckConstraint(_in("status", ORDER_STATUSES), name="ck_orders_status"),
        CheckConstraint("total_xof >= 0", name="ck_orders_total"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    reference: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="pending_payment", index=True)
    currency: Mapped[str] = mapped_column(String(3), default="XOF")
    subtotal_xof: Mapped[int] = mapped_column(Integer)
    shipping_fee_xof: Mapped[int] = mapped_column(Integer, default=0)
    total_xof: Mapped[int] = mapped_column(Integer)
    # Livraison (données minimales nécessaires à l'expédition)
    ship_name: Mapped[str] = mapped_column(String(120))
    ship_phone: Mapped[str] = mapped_column(String(30))
    ship_address: Mapped[str] = mapped_column(String(200))
    ship_city: Mapped[str] = mapped_column(String(80))
    ship_country: Mapped[str] = mapped_column(String(2))
    stock_reserved: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)

    user: Mapped[User | None] = relationship(back_populates="orders")
    items: Mapped[list["OrderItem"]] = relationship(back_populates="order", cascade="all, delete-orphan")
    events: Mapped[list["OrderEvent"]] = relationship(
        back_populates="order", cascade="all, delete-orphan", order_by="OrderEvent.id"
    )
    payments: Mapped[list["Payment"]] = relationship(back_populates="order", order_by="Payment.id")

    @property
    def payment_status(self) -> str:
        """État du dernier paiement : none, initiated, pending, succeeded, failed, cancelled."""
        return self.payments[-1].status if self.payments else "none"

    @property
    def payment_simulated(self) -> bool:
        return any(p.provider == "simulator" and p.status == "succeeded" for p in self.payments)


class OrderItem(Base):
    __tablename__ = "order_items"
    __table_args__ = (CheckConstraint("quantity > 0", name="ck_order_items_qty"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"))
    # Instantané au moment de l'achat : le prix payé ne change pas si le catalogue évolue.
    product_name: Mapped[str] = mapped_column(String(120))
    unit_price_xof: Mapped[int] = mapped_column(Integer)
    quantity: Mapped[int] = mapped_column(Integer)
    line_total_xof: Mapped[int] = mapped_column(Integer)

    order: Mapped[Order] = relationship(back_populates="items")


class OrderEvent(Base):
    __tablename__ = "order_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status: Mapped[str] = mapped_column(String(20))
    actor: Mapped[str] = mapped_column(String(40))
    note: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    order: Mapped[Order] = relationship(back_populates="events")


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (CheckConstraint(_in("status", PAYMENT_STATUSES), name="ck_payments_status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(20))
    transaction_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    amount_xof: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="XOF")
    status: Mapped[str] = mapped_column(String(20), default="initiated")
    provider_status: Mapped[str] = mapped_column(String(40), default="")
    payment_url: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    order: Mapped[Order] = relationship(back_populates="payments")


class NotifyRequest(Base):
    __tablename__ = "notify_requests"
    __table_args__ = (UniqueConstraint("email", "product_id", name="uq_notify_email_product"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime)

"""Schémas d'entrée/sortie.

Entrées : `extra="forbid"` refuse tout champ inattendu (prix, rôle, statut…) au lieu
de l'ignorer silencieusement. Sorties : uniquement les champs publics.
"""
import re
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints, field_validator

from app.models import CATEGORIES, ORDER_STATUSES, PRODUCT_STATUSES


class In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


Name = Annotated[str, StringConstraints(min_length=2, max_length=120)]
Phone = Annotated[str, StringConstraints(min_length=6, max_length=30, pattern=r"^\+?[0-9 ().-]{6,30}$")]
Password = Annotated[str, StringConstraints(min_length=10, max_length=128, strip_whitespace=False)]
Slug = Annotated[str, StringConstraints(min_length=2, max_length=80, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]
Email = Annotated[EmailStr, StringConstraints(max_length=254)]

# Images : chemins relatifs du site ("images/xxx.jpg") ou URL https. Jamais récupérées côté serveur.
_IMAGE_RE = re.compile(r"^(images/[A-Za-z0-9._-]{1,100}|https://[A-Za-z0-9.-]{1,100}/[A-Za-z0-9._~/%-]{1,300})$")


def check_images(v: list[str] | None) -> list[str] | None:
    for url in v or []:
        if not _IMAGE_RE.match(url) or ".." in url:
            raise ValueError("Image invalide : chemin images/… ou URL https attendus")
    return v


# --- Auth ---------------------------------------------------------------
class RegisterIn(In):
    email: Email
    password: Password
    full_name: Name

    @field_validator("email")
    @classmethod
    def _lower(cls, v: str) -> str:
        return v.lower()


class LoginIn(In):
    email: Annotated[str, StringConstraints(min_length=3, max_length=254)]
    password: Annotated[str, StringConstraints(min_length=1, max_length=128, strip_whitespace=False)]


class ForgotIn(In):
    email: Annotated[str, StringConstraints(min_length=3, max_length=254)]


class ResetIn(In):
    token: Annotated[str, StringConstraints(min_length=20, max_length=200)]
    new_password: Password


class ChangePasswordIn(In):
    current_password: Annotated[str, StringConstraints(min_length=1, max_length=128, strip_whitespace=False)]
    new_password: Password


Country = Annotated[str, StringConstraints(min_length=2, max_length=2, pattern=r"^[A-Z]{2}$")]


class ProfileIn(In):
    full_name: Name | None = None
    phone: Phone | None = None
    default_address: Annotated[str, StringConstraints(min_length=4, max_length=200)] | None = None
    default_city: Annotated[str, StringConstraints(min_length=2, max_length=80)] | None = None
    default_country: Country | None = None


class DeleteAccountIn(In):
    password: Annotated[str, StringConstraints(min_length=1, max_length=128, strip_whitespace=False)]


class UserOut(Out):
    email: str
    full_name: str
    phone: str | None
    default_address: str | None = None
    default_city: str | None = None
    default_country: str | None = None
    role: str
    created_at: datetime


class SessionOut(BaseModel):
    user: UserOut
    csrf_token: str


# --- Catalogue ----------------------------------------------------------
class ProductOut(BaseModel):
    slug: str
    name: str
    tagline: str
    description: str
    long_description: str
    category: str
    format: str
    price_xof: int
    status: str
    availability: Literal["in_stock", "low_stock", "out_of_stock", "coming_soon"]
    max_quantity: int
    images: list[str]


class ProductAdminOut(Out):
    slug: str
    name: str
    tagline: str
    description: str
    long_description: str
    category: str
    format: str
    price_xof: int
    stock: int
    low_stock_threshold: int
    status: str
    images: list[str]
    sort_order: int
    updated_at: datetime


class ProductCreateIn(In):
    slug: Slug
    name: Name
    tagline: Annotated[str, StringConstraints(max_length=200)] = ""
    description: Annotated[str, StringConstraints(max_length=400)] = ""
    long_description: Annotated[str, StringConstraints(max_length=4000)] = ""
    category: Literal[CATEGORIES]  # type: ignore[valid-type]
    format: Annotated[str, StringConstraints(min_length=1, max_length=40)] = "200 g"
    price_xof: int = Field(ge=0, le=10_000_000)
    stock: int = Field(default=0, ge=0, le=1_000_000)
    low_stock_threshold: int = Field(default=5, ge=0, le=10_000)
    status: Literal[PRODUCT_STATUSES] = "coming_soon"  # type: ignore[valid-type]
    images: list[str] = Field(default_factory=list, max_length=8)
    sort_order: int = Field(default=0, ge=0, le=10_000)

    @field_validator("images")
    @classmethod
    def _images(cls, v):
        return check_images(v)


class ProductUpdateIn(In):
    name: Name | None = None
    tagline: Annotated[str, StringConstraints(max_length=200)] | None = None
    description: Annotated[str, StringConstraints(max_length=400)] | None = None
    long_description: Annotated[str, StringConstraints(max_length=4000)] | None = None
    category: Literal[CATEGORIES] | None = None  # type: ignore[valid-type]
    format: Annotated[str, StringConstraints(min_length=1, max_length=40)] | None = None
    price_xof: int | None = Field(default=None, ge=0, le=10_000_000)
    stock: int | None = Field(default=None, ge=0, le=1_000_000)
    low_stock_threshold: int | None = Field(default=None, ge=0, le=10_000)
    status: Literal[PRODUCT_STATUSES] | None = None  # type: ignore[valid-type]
    images: list[str] | None = Field(default=None, max_length=8)
    sort_order: int | None = Field(default=None, ge=0, le=10_000)

    @field_validator("images")
    @classmethod
    def _images(cls, v):
        return check_images(v)


# --- Commandes ----------------------------------------------------------
class OrderItemIn(In):
    product_slug: Slug
    quantity: int = Field(ge=1, le=1000)


class ShippingIn(In):
    name: Name
    phone: Phone
    address: Annotated[str, StringConstraints(min_length=4, max_length=200)]
    city: Annotated[str, StringConstraints(min_length=2, max_length=80)]
    country: Annotated[str, StringConstraints(min_length=2, max_length=2, pattern=r"^[A-Z]{2}$")]


class OrderCreateIn(In):
    items: list[OrderItemIn] = Field(min_length=1, max_length=100)
    shipping: ShippingIn


class OrderItemOut(Out):
    product_name: str
    unit_price_xof: int
    quantity: int
    line_total_xof: int


class OrderEventOut(Out):
    to_status: str
    created_at: datetime


class AdminOrderEventOut(OrderEventOut):
    from_status: str | None
    actor: str
    note: str


class OrderOut(Out):
    reference: str
    status: str
    currency: str
    subtotal_xof: int
    shipping_fee_xof: int
    total_xof: int
    ship_name: str
    ship_phone: str
    ship_address: str
    ship_city: str
    ship_country: str
    created_at: datetime
    paid_at: datetime | None
    expires_at: datetime | None
    payment_status: str
    payment_simulated: bool
    items: list[OrderItemOut]
    events: list[OrderEventOut]


class AdminOrderOut(OrderOut):
    customer_email: str | None = None
    customer_id: int | None = None
    events: list[AdminOrderEventOut]


class StockIn(In):
    stock: int = Field(ge=0, le=1_000_000)
    low_stock_threshold: int | None = Field(default=None, ge=0, le=10_000)


class CustomerOut(BaseModel):
    id: int
    full_name: str
    email: str
    created_at: datetime
    is_active: bool
    orders_count: int
    paid_orders_count: int
    total_spent_xof: int
    last_order_at: datetime | None


class CustomerDetailOut(CustomerOut):
    phone: str | None
    orders: list[OrderOut]


class Page(BaseModel):
    total: int
    items: list


class OrderStatusIn(In):
    status: Literal[ORDER_STATUSES]  # type: ignore[valid-type]
    note: Annotated[str, StringConstraints(max_length=300)] = ""


class PaymentStartOut(BaseModel):
    payment_url: str
    provider: str


# --- Notifications ------------------------------------------------------
class NotifyIn(In):
    email: Email
    product_slug: Slug

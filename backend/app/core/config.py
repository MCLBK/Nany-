"""Configuration lue depuis l'environnement (fichier .env en local).

Aucun secret n'a de valeur par défaut. En production, la configuration est
vérifiée au démarrage et l'application refuse de démarrer si elle est permissive.
"""
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["development", "test", "production"] = "development"
    debug: bool = False

    database_url: str = "sqlite:///./nanye.db"

    # CORS : liste explicite, séparée par des virgules (ex. "http://localhost:5500,http://127.0.0.1:5500")
    cors_origins: str = "http://localhost:5500,http://127.0.0.1:5500,http://localhost:3000"

    # URL publique du frontend (liens de réinitialisation, retour de paiement)
    frontend_base_url: str = "http://localhost:5500/index.html"
    # URL publique de l'API (notifications de paiement)
    api_base_url: str = "http://localhost:8000"

    # Sessions
    session_cookie_name: str = "nanye_session"
    session_lifetime_hours: int = Field(default=24 * 7, ge=1, le=24 * 30)
    session_idle_hours: int = Field(default=48, ge=1, le=24 * 30)
    cookie_secure: bool = False
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    cookie_domain: str | None = None

    # Hachage Argon2id (valeurs par défaut = profil RFC 9106 « mémoire réduite »)
    argon2_time_cost: int = 3
    argon2_memory_cost_kib: int = 65536
    argon2_parallelism: int = 4

    # Commandes
    shipping_fee_xof: int = Field(default=0, ge=0)
    max_quantity_per_item: int = Field(default=20, ge=1, le=1000)
    max_items_per_order: int = Field(default=10, ge=1, le=100)
    order_payment_timeout_minutes: int = Field(default=30, ge=5, le=24 * 60)

    # Limites de débit (processus unique, en mémoire)
    rate_limit_enabled: bool = True
    max_body_bytes: int = 32 * 1024

    # Paiement : "disabled", "simulator" (développement uniquement) ou "cinetpay"
    payment_provider: Literal["disabled", "simulator", "cinetpay"] = "disabled"
    # Autorise le simulateur sur un site en ligne (démonstration avant l'ouverture des paiements).
    # Le site et l'administration l'affichent alors clairement ; aucune somme n'est débitée.
    allow_simulated_payments: bool = False
    cinetpay_api_key: str | None = None
    cinetpay_site_id: str | None = None
    cinetpay_secret_key: str | None = None
    cinetpay_base_url: str = "https://api-checkout.cinetpay.com/v2"
    cinetpay_channels: str = "ALL"
    payment_http_timeout_seconds: float = 15.0

    # Emails : "disabled", "console" (développement uniquement) ou "smtp"
    email_backend: Literal["disabled", "console", "smtp"] = "disabled"
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    smtp_starttls: bool = True

    password_reset_ttl_minutes: int = Field(default=30, ge=5, le=24 * 60)
    log_level: str = "INFO"

    @field_validator("database_url")
    @classmethod
    def _normalize_pg_url(cls, v: str) -> str:
        # Les hébergeurs fournissent souvent "postgres://" : on force le pilote psycopg 3.
        if v.startswith("postgres://"):
            v = "postgresql+psycopg://" + v[len("postgres://"):]
        elif v.startswith("postgresql://"):
            v = "postgresql+psycopg://" + v[len("postgresql://"):]
        return v

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip().rstrip("/") for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def simulator_enabled(self) -> bool:
        return self.payment_provider == "simulator" and (not self.is_production or self.allow_simulated_payments)

    def production_problems(self) -> list[str]:
        """Liste des réglages incompatibles avec la production."""
        problems: list[str] = []
        if self.debug:
            problems.append("DEBUG doit être false")
        if not self.cookie_secure:
            problems.append("COOKIE_SECURE doit être true (HTTPS)")
        if self.database_url.startswith("sqlite"):
            problems.append("DATABASE_URL doit pointer vers PostgreSQL")
        if not self.cors_origin_list:
            problems.append("CORS_ORIGINS doit lister les origines du frontend")
        for origin in self.cors_origin_list:
            if origin == "*" or not origin.startswith("https://"):
                problems.append(f"Origine CORS non autorisée en production : {origin!r}")
        if not self.frontend_base_url.startswith("https://") or not self.api_base_url.startswith("https://"):
            problems.append("FRONTEND_BASE_URL et API_BASE_URL doivent être en https")
        if self.payment_provider == "simulator" and not self.allow_simulated_payments:
            problems.append("PAYMENT_PROVIDER=simulator exige ALLOW_SIMULATED_PAYMENTS=true en production")
        if self.payment_provider == "cinetpay" and not (
            self.cinetpay_api_key and self.cinetpay_site_id and self.cinetpay_secret_key
        ):
            problems.append("CINETPAY_API_KEY, CINETPAY_SITE_ID et CINETPAY_SECRET_KEY sont requis")
        if self.email_backend == "console":
            problems.append("EMAIL_BACKEND=console est interdit en production")
        if self.email_backend == "smtp" and not (self.smtp_host and self.smtp_from):
            problems.append("SMTP_HOST et SMTP_FROM sont requis avec EMAIL_BACKEND=smtp")
        if self.cookie_samesite == "none" and not self.cookie_secure:
            problems.append("COOKIE_SAMESITE=none exige COOKIE_SECURE=true")
        return problems


@lru_cache
def get_settings() -> Settings:
    return Settings()

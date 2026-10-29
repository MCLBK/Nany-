import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import get_settings

_s = get_settings()
# argon2-cffi utilise Argon2id par défaut.
_hasher = PasswordHasher(
    time_cost=_s.argon2_time_cost,
    memory_cost=_s.argon2_memory_cost_kib,
    parallelism=_s.argon2_parallelism,
)
# Empreinte factice : la connexion d'un email inconnu coûte le même temps qu'un vrai essai.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


_REF_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # sans 0/O/1/I, lisible au téléphone


def new_order_reference() -> str:
    """Référence non prédictible : 10 caractères aléatoires (~50 bits)."""
    return "NNY-" + "".join(secrets.choice(_REF_ALPHABET) for _ in range(10))

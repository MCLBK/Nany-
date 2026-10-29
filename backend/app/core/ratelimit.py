"""Limiteur de débit en mémoire, à fenêtre glissante.

Limite connue : l'état vit dans le processus. Il protège un déploiement à
UNE instance (un seul worker Uvicorn). Avec plusieurs workers ou instances,
chaque processus compte séparément : il faut alors un stockage partagé
(Redis) ou la limitation du proxy / de l'hébergeur.
"""
import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, status

from app.core.config import get_settings

# (requêtes, fenêtre en secondes)
LIMITS: dict[str, tuple[int, int]] = {
    "login_ip": (20, 15 * 60),
    "login_email": (5, 15 * 60),
    "register": (5, 60 * 60),
    "password_forgot": (5, 60 * 60),
    "password_reset": (10, 60 * 60),
    "order_create": (10, 10 * 60),
    "payment_start": (10, 10 * 60),
    "notify": (10, 60 * 60),
    "webhook": (120, 60),
}


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, bucket: str, key: str) -> bool:
        """Enregistre une tentative ; renvoie False si la limite est dépassée."""
        limit, window = LIMITS[bucket]
        now = time.monotonic()
        with self._lock:
            q = self._hits[(bucket, key)]
            while q and q[0] <= now - window:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            if len(self._hits) > 50_000:  # borne mémoire : purge des clés inactives
                for k in [k for k, v in self._hits.items() if not v]:
                    del self._hits[k]
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = RateLimiter()


def client_ip(request: Request) -> str:
    # request.client.host n'utilise X-Forwarded-For que si Uvicorn est lancé avec
    # --proxy-headers et --forwarded-allow-ips (proxy de confiance explicite).
    return request.client.host if request.client else "unknown"


def enforce(bucket: str, key: str) -> None:
    if not get_settings().rate_limit_enabled:
        return
    if not limiter.hit(bucket, key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Trop de tentatives. Patientez quelques minutes avant de réessayer.",
        )

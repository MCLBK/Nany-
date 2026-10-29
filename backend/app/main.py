import logging
import os
import secrets
import time

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import account, admin, auth, catalog, payments
from app.core.config import get_settings
from app.db import SessionLocal

settings = get_settings()
logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("nanye")

if settings.is_production and (problems := settings.production_problems()):
    raise RuntimeError("Configuration de production refusée :\n- " + "\n- ".join(problems))
if settings.is_production and settings.simulator_enabled:
    log.warning("MODE DÉMONSTRATION : paiements simulés activés en production, aucune somme n'est encaissée.")

app = FastAPI(
    title="NANYÉ API",
    version="1.0.0",
    docs_url=None if settings.is_production else "/api/docs",
    redoc_url=None,
    openapi_url=None if settings.is_production else "/api/openapi.json",
)

# Chemins appelés serveur à serveur ou par formulaire : pas de contrôle d'Origine navigateur.
ORIGIN_EXEMPT_PREFIXES = ("/api/payments/cinetpay/", "/api/payments/simulator/")


@app.middleware("http")
async def guard(request: Request, call_next):
    request_id = secrets.token_hex(6)
    request.state.request_id = request_id
    start = time.perf_counter()

    # 1. Taille maximale du corps de requête
    length = request.headers.get("content-length")
    if length is not None and (not length.isdigit() or int(length) > settings.max_body_bytes):
        return JSONResponse({"detail": "Requête trop volumineuse."}, status_code=413)
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and length is None and \
            request.headers.get("transfer-encoding", "").lower() == "chunked":
        return JSONResponse({"detail": "Longueur de requête requise."}, status_code=411)

    # 2. Défense en profondeur contre le CSRF : origine explicite sur les écritures
    origin = request.headers.get("origin")
    if (
        request.method in ("POST", "PUT", "PATCH", "DELETE")
        and origin
        and origin.rstrip("/") not in settings.cors_origin_list
        and not request.url.path.startswith(ORIGIN_EXEMPT_PREFIXES)
    ):
        return JSONResponse({"detail": "Origine non autorisée."}, status_code=403)

    response = await call_next(request)

    # 3. En-têtes de sécurité (l'API ne sert que du JSON, sauf la page du simulateur)
    h = response.headers
    h["X-Content-Type-Options"] = "nosniff"
    h["Referrer-Policy"] = "no-referrer"
    h["X-Frame-Options"] = "DENY"
    h["Cross-Origin-Resource-Policy"] = "same-site"
    h["Cache-Control"] = h.get("Cache-Control", "no-store")
    if "text/html" in h.get("content-type", ""):
        h["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'"
    else:
        h["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
    if settings.cookie_secure:
        h["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    h["X-Request-ID"] = request_id

    # Journal sans corps, ni cookies, ni chaîne de requête
    log.info("%s %s %s %.0fms rid=%s", request.method, request.url.path, response.status_code,
             (time.perf_counter() - start) * 1000, request_id)
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "X-CSRF-Token", "Idempotency-Key"],
    max_age=600,
)


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    # Ne renvoie jamais les valeurs saisies (un mot de passe ne doit pas ressortir dans une erreur).
    errors = [{"field": ".".join(str(p) for p in e.get("loc", [])[1:]), "message": e.get("msg", "")}
              for e in exc.errors()]
    return JSONResponse({"detail": "Données invalides.", "errors": errors}, status_code=422)


@app.exception_handler(StarletteHTTPException)
async def http_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=getattr(exc, "headers", None))


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    rid = getattr(request.state, "request_id", "-")
    log.exception("Erreur non gérée rid=%s", rid)
    return JSONResponse({"detail": "Erreur interne. Réessayez plus tard.", "request_id": rid}, status_code=500)


@app.get("/api/health", tags=["santé"])
def health():
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        return {"status": "ok"}
    except Exception:
        log.exception("Base de données indisponible")
        return JSONResponse({"status": "unavailable"}, status_code=status.HTTP_503_SERVICE_UNAVAILABLE)


for r in (catalog.router, auth.router, account.router, payments.router, admin.router):
    app.include_router(r, prefix="/api")


if os.environ.get("VERCEL"):
    # Site et API partagent le domaine : si le CDN ne résout pas un dossier vers son
    # index.html, ces redirections y conduisent (sans effet en local).
    @app.get("/", include_in_schema=False)
    def _site_root():
        return RedirectResponse("/index.html", status_code=308)

    @app.get("/admin", include_in_schema=False)
    @app.get("/admin/", include_in_schema=False)
    def _admin_root():
        return RedirectResponse("/admin/index.html", status_code=308)

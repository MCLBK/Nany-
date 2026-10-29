"""Préparation du déploiement (Vercel : site dans public/, API via server.py)."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _run(code: str, **env) -> str:
    """Exécute du code dans un processus neuf (configuration et moteur sont créés à l'import)."""
    full_env = {**os.environ, "ARGON2_MEMORY_COST_KIB": "1024", "ARGON2_TIME_COST": "1", **env}
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=full_env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    return out.stdout


def test_vercel_entrypoint_exposes_api_and_redirects():
    out = _run(
        "import server\n"
        "from fastapi.testclient import TestClient\n"
        "c = TestClient(server.app)\n"
        "print(c.get('/api/health').status_code, c.get('/', follow_redirects=False).headers.get('location'),"
        " c.get('/admin/', follow_redirects=False).headers.get('location'))",
        VERCEL="1", APP_ENV="test", DATABASE_URL="sqlite:///" + str(ROOT / "backend" / "deploy-test.db"),
    )
    (ROOT / "backend" / "deploy-test.db").unlink(missing_ok=True)
    assert out.split() == ["200", "/index.html", "/admin/index.html"]


def test_serverless_postgres_engine_has_no_pool():
    out = _run(
        "import server\nfrom app.db import engine\nprint(type(engine.pool).__name__, engine.url.drivername)",
        VERCEL="1", APP_ENV="test", DATABASE_URL="postgres://u:p@ep-x-pooler.neon.tech/nanye?sslmode=require",
    ).strip()
    assert out == "NullPool postgresql+psycopg"


def test_production_requirements_are_shared():
    assert (ROOT / "backend" / "requirements.txt").read_text(encoding="utf-8").strip().endswith("-r ../requirements.txt")
    root = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    for pkg in ("fastapi==", "SQLAlchemy==", "psycopg-binary==", "argon2-cffi=="):
        assert pkg in root


def test_frontend_uses_same_domain_api_in_production():
    cfg = (ROOT / "public" / "config.js").read_text(encoding="utf-8")
    assert "'/api'" in cfg and "localhost:8000" in cfg
    for page in ("index.html", "admin/index.html"):
        html = (ROOT / "public" / page).read_text(encoding="utf-8")
        assert "config.js" in html


def test_no_secret_files_would_be_published():
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for pattern in (".env", ".venv/", "*.db", "__pycache__/"):
        assert pattern in ignore

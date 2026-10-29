"""Point d'entrée de l'API pour Vercel.

Vercel détecte l'objet FastAPI `app` dans ce fichier et en fait une fonction ;
le site statique est servi depuis public/ par le CDN, sur le même domaine.
En local, lancer plutôt : cd backend ; python -m uvicorn app.main:app --port 8000
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "backend"))

from app.main import app  # noqa: E402,F401

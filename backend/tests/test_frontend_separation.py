"""Séparation des espaces : aucune trace de la gestion dans le site public.

Ces tests lisent les fichiers du frontend ; ils ne remplacent pas les contrôles
serveur (voir test_backoffice.py), qui sont la vraie protection.
"""
import re
from pathlib import Path

FRONT = Path(__file__).resolve().parents[2] / "public"
PUBLIC_FILES = ["index.html", "app.js"]


def _read(name: str) -> str:
    return (FRONT / name).read_text(encoding="utf-8")


def test_public_pages_have_no_admin_link_or_screen():
    for name in PUBLIC_FILES:
        text = _read(name)
        assert not re.search(r"""href=["'][^"']*admin""", text, re.I), name
        assert "#admin" not in text and "page-admin" not in text, name
        assert "/admin/" not in text, name  # aucun appel aux routes de gestion depuis le site public


def test_public_navigation_only_lists_public_and_account_pages():
    html = _read("index.html")
    targets = set(re.findall(r'href="#([a-z]+)', html))
    assert targets <= {"main", "home", "shop", "product", "story", "recipes", "distribution", "account", "order"}, targets


def test_backoffice_is_a_separate_noindex_app():
    html = _read("admin/index.html")
    assert 'name="robots" content="noindex, nofollow"' in html
    assert 'src="admin.js"' in html
    js = _read("admin/admin.js")
    # L'interface vérifie le rôle pour l'affichage, mais chaque appel reste contrôlé par l'API
    assert "['staff', 'admin'].includes" in js
    assert "credentials: 'include'" in js and "X-CSRF-Token" in js

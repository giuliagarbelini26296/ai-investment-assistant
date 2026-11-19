"""Ajustes para a suíte de testes: estáticos sem manifesto (não exige collectstatic)."""

from .settings import *  # noqa: F401,F403

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
WHITENOISE_AUTOREFRESH = True

# Banco de teste sempre em UTF-8 (clusters locais podem ter template1 em SQL_ASCII)
DATABASES["default"]["TEST"] = {"CHARSET": "UTF8", "TEMPLATE": "template0"}  # noqa: F405

# Rotinas de mercado ficam desligadas por padrão nos testes (ligadas explicitamente onde são testadas).
PORTAL = {**PORTAL, "COLETA_MERCADO": False}  # noqa: F405

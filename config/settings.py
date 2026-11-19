"""
Configuração do Portal de Ações.

Tudo que varia por ambiente vem de variáveis de ambiente (arquivo .env no Compose).
Nenhum segredo ou chave de API é definido aqui.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

BASE_DIR = Path(__file__).resolve().parent.parent

# Deliberately opt-in: a configured key alone never activates billable AI requests.
PORTAL_AI_ENABLED = os.environ.get('PORTAL_AI_ENABLED', '').lower() in {'true','1','sim'}
PORTAL_AI_KEY = os.environ.get('OPENAI_API_KEY', '')
PORTAL_AI_MODEL = os.environ.get('PORTAL_AI_MODEL', 'gpt-6.1-sol')
PORTAL_AI_DAILY_TURNS = 20
PORTAL_AI_PROVIDER = os.environ.get('PORTAL_AI_PROVIDER', 'openai').strip().lower()
PORTAL_OLLAMA_ENABLED = os.environ.get('PORTAL_OLLAMA_ENABLED', '').lower() in {'true','1','sim'}
PORTAL_OLLAMA_URL = os.environ.get('PORTAL_OLLAMA_URL', 'http://host.docker.internal:11434')
PORTAL_OLLAMA_MODEL = os.environ.get('PORTAL_OLLAMA_MODEL', 'qwen3.5:4b')
PORTAL_OLLAMA_DAILY_TURNS = 60
PORTAL_CRYPTO_STREAM = os.environ.get('PORTAL_CRYPTO_STREAM', 'true').lower() in {'true','1','sim'}


def env(name: str, default: str | None = None, *, required: bool = False) -> str | None:
    value = os.environ.get(name, default)
    if required and (value is None or value == ""):
        raise RuntimeError(f"Variável de ambiente obrigatória ausente: {name}")
    return value


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "sim", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    raw = os.environ.get(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


def database_from_url(url: str) -> dict:
    """Converte postgres://user:senha@host:porta/banco em configuração do Django.

    Também aceita sqlite:///caminho (usado apenas para desenvolvimento rápido/CI;
    a decisão de arquitetura é PostgreSQL).
    """
    parsed = urlparse(url)
    if parsed.scheme in {"postgres", "postgresql"}:
        return {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": parsed.path.lstrip("/"),
            "USER": parsed.username or "",
            "PASSWORD": parsed.password or "",
            "HOST": parsed.hostname or "",
            "PORT": str(parsed.port or ""),
            "CONN_MAX_AGE": 60,
        }
    if parsed.scheme == "sqlite":
        # sqlite:////caminho/absoluto -> path "//caminho/absoluto"; sqlite:///relativo -> "/relativo"
        path = parsed.netloc + parsed.path if parsed.netloc else parsed.path
        path = "/" + path.lstrip("/") if path.startswith("//") else path.lstrip("/") or "db.sqlite3"
        return {"ENGINE": "django.db.backends.sqlite3", "NAME": path}
    raise RuntimeError(f"DATABASE_URL com esquema não suportado: {parsed.scheme!r}")


# --- Segurança -------------------------------------------------------------------

DEBUG = env_bool("DJANGO_DEBUG", False)
SECRET_KEY = env("DJANGO_SECRET_KEY", "dev-only-insecure-key" if DEBUG else None, required=not DEBUG)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS", "")

SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"

# Em acesso remoto (HTTPS atrás de proxy), ligar via ambiente.
if env_bool("DJANGO_HTTPS", False):
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30

# --- Aplicações ------------------------------------------------------------------

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "apps.accounts",
    "apps.react_portal.apps.ReactPortalConfig",
    "apps.ui",
    "apps.marketdata",
    "apps.portfolio",
    "apps.valuation",
    "apps.alerts",
    "apps.mercado",
    "apps.noticias",
    "apps.insights",
    "apps.managed_portfolios",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.ui.context_processors.portal",
                "apps.react_portal.navigation.portal_chrome",
            ],
            "builtins": ["apps.ui.templatetags.formatos"],
        },
    },
]

# --- Banco de dados --------------------------------------------------------------

DATABASES = {
    "default": database_from_url(env("DATABASE_URL", "postgres://portal:portal@db:5432/portal_acoes") or "")
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Autenticação ----------------------------------------------------------------

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "login"

# --- Internacionalização ---------------------------------------------------------

LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Sao_Paulo"  # apresentação; o banco guarda UTC
USE_I18N = True
USE_TZ = True
USE_THOUSAND_SEPARATOR = True

# --- Arquivos estáticos ----------------------------------------------------------

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}


# --- Portal ----------------------------------------------------------------------

PORTAL = {
    "NOME": env("PORTAL_NOME", "Portal de Ações"),
    # Modo demonstração: dados sintéticos rotulados; nunca cotações reais.
    "MODO_DEMO": env_bool("PORTAL_MODO_DEMO", True),
    "VERSAO": "1.6.1-react",
    # Rotinas do menu Mercado no coletor (candles de 1 ano, rodízio do universo, analistas, Focus).
    "COLETA_MERCADO": env_bool("PORTAL_COLETA_MERCADO", True),
}

# Fontes de dados: chaves só por ambiente. Vazio = fonte sem credencial (sandbox/público).
FONTES = {
    "CHAVES": {
        "twelvedata": env("TWELVEDATA_API_KEY", ""),
        "awesomeapi": env("AWESOMEAPI_API_KEY", ""),
        "brapi": env("BRAPI_API_KEY", ""),
        "coingecko": env("COINGECKO_API_KEY", ""),
        # Inteligência de mercado (opcionais): analistas, notícias, resultados, fundamentos.
        "finnhub": env("FINNHUB_API_KEY", ""),
        "alphavantage": env("ALPHAVANTAGE_API_KEY", ""),
    },
    # brapi (B3): ações por requisição e intervalo da coleta conforme o plano (Gratuito: 1 e 30 min)
    "BRAPI_SIMBOLOS_POR_REQUISICAO": env("BRAPI_SIMBOLOS_POR_REQUISICAO", "1"),
    "BRAPI_INTERVALO_MIN": env("BRAPI_INTERVALO_MIN", "30"),
    # ações dos EUA da carteira: intervalo do ciclo de cotações e fonte (twelvedata | finnhub)
    "ACOES_EUA_INTERVALO_MIN": env("ACOES_EUA_INTERVALO_MIN", "15"),
    "FONTE_COTACAO_EUA": env("FONTE_COTACAO_EUA", "twelvedata"),
}

# Notificações de alertas fora do portal. Vazio = canal desligado.
NOTIFICACOES = {
    "TELEGRAM_BOT_TOKEN": env("TELEGRAM_BOT_TOKEN", ""),
    "TELEGRAM_CHAT_ID": env("TELEGRAM_CHAT_ID", ""),
    # Grupo privado de consulta. A administração permanece no chat particular acima.
    "TELEGRAM_GRUPO_CHAT_ID": env("TELEGRAM_GRUPO_CHAT_ID", ""),
    # Token do feed consultado pelo script de notificação do macOS (scripts/mac/).
    "FEED_TOKEN": env("PORTAL_FEED_TOKEN", ""),
    # Endereço do portal usado nos links das mensagens.
    "URL_PORTAL": (env("PORTAL_URL", "http://127.0.0.1:8000") or "").rstrip("/"),
    # Horários (São Paulo) do resumo diário da carteira; vazio desliga.
    "RESUMO_HORARIOS": env("RESUMO_HORARIOS", "08:00,18:30"),
    # Notificações do app (Web Push). Chaves VAPID vazias = recurso desligado.
    "PUSH_VAPID_PUBLIC_KEY": env("PORTAL_VAPID_PUBLIC_KEY", ""),
    "PUSH_VAPID_PRIVATE_KEY": env("PORTAL_VAPID_PRIVATE_KEY", ""),
    "PUSH_CONTATO": env("PORTAL_VAPID_SUBJECT", "https://portal.example.com"),
}

# A central atualiza em segundo plano; páginas nunca chamam as fontes ou o tradutor.
NOTICIAS = {
    "INTERVALO_MIN": max(15, int(env("NOTICIAS_INTERVALO_MIN", "15") or "15")),
    "MODELO_TRADUCAO": env("NOTICIAS_MODELO_TRADUCAO", str(BASE_DIR / "translation-model")),
}

# --- Logging ---------------------------------------------------------------------

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "plain": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"},
    },
    "filters": {"segredos": {"()": "config.log_filters.MascararSegredos"}},
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "plain", "filters": ["segredos"]}
    },
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO")},
}

# Same-origin links preserve local and protected external access.
ID3_LEGACY_URL = ""
ID3_API_LOGIN_URL = "/conta/entrar/?next=/"

# Read-only IEX market data, opt-in after credentials are configured.
PORTAL_ALPACA_STREAM = env_bool('PORTAL_ALPACA_STREAM', False)
ALPACA_API_KEY = os.environ.get('ALPACA_API_KEY', '')
ALPACA_SECRET_KEY = os.environ.get('ALPACA_SECRET_KEY', '')

# Gemini remains disabled until a server-side key and free-tier project are confirmed.
PORTAL_GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
PORTAL_GEMINI_ENABLED = os.environ.get("PORTAL_GEMINI_ENABLED", "").lower() in {"true", "1", "sim"}
PORTAL_GEMINI_FREE_TIER_CONFIRMED = os.environ.get("PORTAL_GEMINI_FREE_TIER_CONFIRMED", "").lower() in {"true", "1", "sim"}

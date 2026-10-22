"""Configurações do Django a partir de variáveis de ambiente."""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


def env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-insecure-key-troque-em-producao")
DEBUG = env_bool("DJANGO_DEBUG", False)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS", "")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "apps.accounts",
    "apps.courses",
    "apps.media",
    "apps.panel",
    "apps.web",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "apps.accounts.middleware.SessaoUnicaMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.accounts.middleware.MustChangePasswordMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.web.identidade.identidade",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "cremepe"),
        "USER": os.environ.get("POSTGRES_USER", "cremepe"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", ""),
        "HOST": os.environ.get("POSTGRES_HOST", "db"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": 60,
        "TEST": {"NAME": os.environ.get("POSTGRES_DB", "cremepe") + "_test"},
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Belem"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = Path(os.environ.get("STATIC_ROOT", BASE_DIR / "staticfiles"))
STATICFILES_DIRS = [BASE_DIR / "static"]

# Nome do arquivo estático com hash do conteúdo (player.<hash>.js). Assim o
# cache longo do nginx nunca entrega uma versão antiga do player.
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if DEBUG
            else "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"
        ),
    },
}

MEDIA_URL = "/media/"
MEDIA_ROOT = Path(os.environ.get("MEDIA_ROOT", BASE_DIR / "media"))

LOGIN_URL = "/entrar/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/entrar/"

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = env_bool("SECURE_COOKIES", False)
CSRF_COOKIE_SECURE = env_bool("SECURE_COOKIES", False)
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
X_FRAME_OPTIONS = "DENY"

# Nomes proprios de cookie. Alguns componentes do ambiente (extensoes de
# navegador, proxies locais, antivirus com inspecao de HTTPS) reescrevem os
# cookies padrao do Django ("sessionid" e "csrftoken") com valores antigos —
# o que fazia o login nao pegar e a API responder 401. Com nomes proprios,
# esses componentes nao interferem.
SESSION_COOKIE_NAME = "cremepe_sid"
CSRF_COOKIE_NAME = "cremepe_csrftoken"

# Exige a troca da senha inicial padrão no primeiro acesso do médico.
# ATENÇÃO: deixar `false` só é aceitável em ambiente de DESENVOLVIMENTO/TESTES.
EXIGIR_TROCA_SENHA = env_bool("EXIGIR_TROCA_SENHA", True)

# Blocos de upload de até 16 MB precisam caber em memória.
DATA_UPLOAD_MAX_MEMORY_SIZE = env_int("DATA_UPLOAD_MAX_MEMORY_SIZE", 64 * 1024 * 1024)
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024

# --- Regras de progresso do aluno -------------------------------------------
PROGRESS_MAX_RATE = env_float("PROGRESS_MAX_RATE", 1.25)
PROGRESS_GRACE_SECONDS = env_float("PROGRESS_GRACE_SECONDS", 5.0)
SEGMENT_TOLERANCE_SECONDS = env_float("SEGMENT_TOLERANCE_SECONDS", 12.0)
HEARTBEAT_INTERVAL_SECONDS = env_int("HEARTBEAT_INTERVAL_SECONDS", 10)

# --- Upload / mídia ---------------------------------------------------------
UPLOAD_CHUNK_SIZE = env_int("UPLOAD_CHUNK_SIZE", 16 * 1024 * 1024)
# A playlist VOD e montada uma unica vez e o hls.js reutiliza as URLs dos
# segmentos durante toda a reproducao. Com TTL curto (60 s), videos longos
# passavam a receber 403 depois de um minuto. Oito horas cobrem a duracao
# dos cursos; o acesso continua validado por sessao/token e pelo limite
# de avanco a cada segmento.
HLS_TOKEN_TTL_SECONDS = env_int("HLS_TOKEN_TTL_SECONDS", 8 * 3600)
# Validade do token de API entregue ao player (independe do cookie).
API_TOKEN_TTL_SECONDS = env_int("API_TOKEN_TTL_SECONDS", 8 * 3600)
HLS_SEGMENT_SECONDS = env_int("HLS_SEGMENT_SECONDS", 4)
# Como gerar o streaming a partir do arquivo original:
#   auto     -> transcodifica se a fonte for maior que 720p (recomendado)
#   remux    -> apenas repackage (rápido, mantém a qualidade original)
#   transcode-> sempre gera 720p/540p/360p (mais leve, melhor para o aluno)
VIDEO_PIPELINE = os.environ.get("VIDEO_PIPELINE", "auto").strip().lower()
VIDEO_MAX_HEIGHT = env_int("VIDEO_MAX_HEIGHT", 720)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {"format": "[{levelname}] {asctime} {name} {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "simple"},
    },
    "root": {"handlers": ["console"], "level": os.environ.get("LOG_LEVEL", "INFO")},
}

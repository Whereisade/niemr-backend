"""
Django settings for config project.

Deployment targets:
- Render (web service)
- Supabase Postgres (DATABASE_URL)
- Supabase Storage (S3 protocol via django-storages)
"""

import os
from datetime import timedelta
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------
# Environment helpers
# ---------------------------------------------------------------------
def env_bool(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "y", "on")


IS_RENDER = os.getenv("RENDER") is not None

# SECURITY WARNING: keep the secret key used in production secret!
# Render blueprint often uses SECRET_KEY, local dev may use DJANGO_SECRET_KEY.
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY") or os.getenv("SECRET_KEY") or "dev-secret"

# Render should run with DEBUG=False; local dev defaults to True unless set.
DEBUG = env_bool("DJANGO_DEBUG", default=(not IS_RENDER))

# Hosts / CSRF
# - You can set ALLOWED_HOSTS="api.example.com,niemr-api.onrender.com"
# - On Render, RENDER_EXTERNAL_HOSTNAME is auto-injected.
_allowed_hosts = [h.strip() for h in os.getenv("ALLOWED_HOSTS", "").split(",") if h.strip()]
render_host = os.getenv("RENDER_EXTERNAL_HOSTNAME", "").strip()
if render_host:
    _allowed_hosts.append(render_host)

if DEBUG and not _allowed_hosts:
    ALLOWED_HOSTS = ["*"]
else:
    ALLOWED_HOSTS = sorted(set(_allowed_hosts))

# CSRF trusted origins are required for Django admin on custom domains
_csrf_trusted = [o.strip() for o in os.getenv("CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()]
if render_host:
    _csrf_trusted.append(f"https://{render_host}")
CSRF_TRUSTED_ORIGINS = sorted(set(_csrf_trusted))

# Hard fail in production if secrets are missing
if not DEBUG and SECRET_KEY == "dev-secret":
    raise RuntimeError("SECRET_KEY is not set for production. Set DJANGO_SECRET_KEY or SECRET_KEY.")

# ---------------------------------------------------------------------
# Email (Resend SMTP)
# ---------------------------------------------------------------------
EMAILS_PROVIDER = (os.getenv("EMAILS_PROVIDER", "SMTP") or "SMTP").upper()
EMAILS_DELIVERY_MODE = (os.getenv("EMAILS_DELIVERY_MODE", "THREAD") or "THREAD").upper()

# HTTP timeout (seconds) used by API-based providers (e.g., Resend)
EMAILS_HTTP_TIMEOUT = int(os.getenv("EMAILS_HTTP_TIMEOUT", "10"))
# Optional Resend settings (only required if EMAILS_PROVIDER=RESEND)
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
RESEND_FROM = os.getenv("RESEND_FROM", "no-reply@niemr.app")
if EMAILS_PROVIDER == "SMTP" and not RESEND_API_KEY:
    raise RuntimeError("RESEND_API_KEY is required when EMAILS_PROVIDER=RESEND.")

EMAILS_WEBHOOK_SECRET = os.getenv("EMAILS_WEBHOOK_SECRET", "")  # optional Resend webhook signature secret
EMAILS_MAX_RETRIES = int(os.getenv("EMAILS_MAX_RETRIES", "6"))
EMAILS_RETRY_BACKOFF_SEC = int(os.getenv("EMAILS_RETRY_BACKOFF_SEC", "120"))

# Frontend base URL used for links (e.g., password reset)
FRONTEND_BASE_URL = (os.getenv("FRONTEND_BASE_URL") or os.getenv("FRONTEND_URL") or "http://localhost:3000").rstrip("/")

# Email transport (Google SMTP by default)
EMAIL_BACKEND = os.getenv("EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
EMAIL_PORT = int(os.getenv("SMTP_PORT", "587"))
EMAIL_USE_TLS = (os.getenv("SMTP_USE_TLS", "1").lower() in {"1", "true", "yes", "on"})
EMAIL_USE_SSL = (os.getenv("SMTP_USE_SSL", "0").lower() in {"1", "true", "yes", "on"})
if EMAIL_USE_SSL:
    EMAIL_USE_TLS = False

EMAIL_HOST_USER = os.getenv("SMTP_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("SMTP_PASSWORD", "")
DEFAULT_FROM_EMAIL = os.getenv("SMTP_FROM_EMAIL", os.getenv("DEFAULT_FROM_EMAIL", "Niemr <no-reply@mail.niemr.africa>"))

# Default topics that should send email notifications even if the user hasn't
# explicitly enabled email in preferences (in-app remains enabled by default).
NOTIFICATIONS_EMAIL_DEFAULT_TOPICS = [
    # Appointments / encounters
    "APPT_REMINDER",
    "APPOINTMENT_REMINDER",
    "APPOINTMENT_CONFIRMED",
    "APPOINTMENT_RESCHEDULED",
    "APPOINTMENT_CANCELLED",
    "APPOINTMENT_NO_SHOW",
    "APPOINTMENT_COMPLETED",
    "ENCOUNTER_COMPLETED",
    "STAFF_ASSIGNED",
    # Labs / imaging / pharmacy
    "LAB_RESULT_READY",
    "IMAGING_REPORT_READY",
    "PRESCRIPTION_READY",
    # Operations / alerts
    "VITAL_ALERT",
    "SYSTEM_ANNOUNCEMENT",
]


# ---------------------------------------------------------------------
# Application definition
# ---------------------------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "core",
    "accounts",
    "facilities",
    "patients",
    "vitals",
    "encounters",
    "labs",
    "imaging",
    "pharmacy",
    "appointments",
    "billing",
    "attachments",
    "notifications",
    "providers",
    "audit",
    "emails",
    "reports",
    "drf_spectacular",
    "drf_spectacular_sidecar",
    "storages",
    "rest_framework_nested",
    "system_admin",
    "outreach",
    # "corsheaders",
]

AUTH_USER_MODEL = "accounts.User"

# Reports: WeasyPrint may require OS deps. Default to disabled on Render unless enabled.
REPORTS_ENABLE_PDF = env_bool("REPORTS_ENABLE_PDF", default=(not IS_RENDER))
REPORTS_BRAND = {
    "name": os.getenv("REPORTS_BRAND_NAME", "NIEMR"),
    "address": os.getenv("REPORTS_BRAND_ADDR", "123 Health St, Lagos"),
    "phone": os.getenv("REPORTS_BRAND_PHONE", "+234-800-000-0000"),
    "email": os.getenv("REPORTS_BRAND_EMAIL", "care@niemr.app"),
}

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "accounts.authentication.HeaderJWTAuthentication",
    ),
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "NIEMR API",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    "SECURITY": [{"bearerAuth": []}],
    "COMPONENTS": {
        "securitySchemes": {
            "bearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}
        }
    },
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=int(os.getenv("ACCESS_TOKEN_LIFETIME_MIN", "30"))),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=int(os.getenv("REFRESH_TOKEN_LIFETIME_DAYS", "7"))),
    "AUTH_HEADER_TYPES": ("Bearer", "JWT"),
}

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "audit.middleware.AuditRequestMiddleware",
]

ROOT_URLCONF = "config.urls"

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
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"



# ---------------------------------------------------------------------
# Database (Supabase Postgres)
# ---------------------------------------------------------------------
# In production set DATABASE_URL (prefer Supabase pooler); local dev can fall back to sqlite.
if os.getenv("DATABASE_URL"):
    DATABASES = {
        "default": dj_database_url.config(
            env="DATABASE_URL",
            conn_max_age=int(os.getenv("DB_CONN_MAX_AGE", "60")),
            ssl_require=True,
        )
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

# ---------------------------------------------------------------------
# Password validation
# ---------------------------------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ---------------------------------------------------------------------
# Internationalization
# ---------------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = os.getenv("TZ", "Africa/Lagos")
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------
# Static & Media
# ---------------------------------------------------------------------
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_ROOT = BASE_DIR / "media"

# Supabase Storage (S3 protocol)
SUPABASE_PROJECT_REF = os.getenv("SUPABASE_PROJECT_REF", "").strip()
AWS_ACCESS_KEY_ID = os.getenv("SUPABASE_S3_ACCESS_KEY_ID", "").strip() or os.getenv("AWS_ACCESS_KEY_ID", "").strip()
AWS_SECRET_ACCESS_KEY = os.getenv("SUPABASE_S3_SECRET_ACCESS_KEY", "").strip() or os.getenv("AWS_SECRET_ACCESS_KEY", "").strip()
AWS_STORAGE_BUCKET_NAME = os.getenv("SUPABASE_BUCKET", "media").strip()

SUPABASE_S3_REGION = os.getenv("SUPABASE_S3_REGION", "").strip() or os.getenv("AWS_S3_REGION_NAME", "").strip() or "us-east-1"

USE_SUPABASE_S3 = bool(SUPABASE_PROJECT_REF and AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY and AWS_STORAGE_BUCKET_NAME)

# Storage behavior
AWS_S3_SIGNATURE_VERSION = "s3v4"
AWS_S3_ADDRESSING_STYLE = "path"   # Supabase S3 expects path-style URLs
AWS_DEFAULT_ACL = None             # Avoid x-amz-acl (often unsupported in S3-compat layers)

# If your bucket is private, enable signed URLs.
AWS_QUERYSTRING_AUTH = env_bool("SUPABASE_MEDIA_SIGNED", default=False)

if USE_SUPABASE_S3:
    AWS_S3_ENDPOINT_URL = f"https://{SUPABASE_PROJECT_REF}.storage.supabase.co/storage/v1/s3"
    AWS_S3_REGION_NAME = SUPABASE_S3_REGION

    # MEDIA_URL for public bucket (no querystrings)
    if not AWS_QUERYSTRING_AUTH:
        MEDIA_URL = (
            f"https://{SUPABASE_PROJECT_REF}.storage.supabase.co/"
            f"storage/v1/object/public/{AWS_STORAGE_BUCKET_NAME}/"
        )
    else:
        MEDIA_URL = "/media/"

    STORAGES = {
        "default": {"BACKEND": "storages.backends.s3boto3.S3Boto3Storage"},
        "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
    }
else:
    # Local dev fallback. On Render you should ALWAYS configure Supabase Storage env vars.
    if IS_RENDER:
        raise RuntimeError(
            "Supabase Storage env vars are missing. "
            "Set SUPABASE_PROJECT_REF, SUPABASE_S3_ACCESS_KEY_ID, SUPABASE_S3_SECRET_ACCESS_KEY, and SUPABASE_BUCKET."
        )

    MEDIA_URL = "/media/"
    STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
    }

# ---------------------------------------------------------------------
# Production security (Render)
# ---------------------------------------------------------------------
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", default=True)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

    # Keep HSTS conservative by default; tune once domain is stable.
    SECURE_HSTS_SECONDS = int(os.getenv("SECURE_HSTS_SECONDS", "0"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", default=False)
    SECURE_HSTS_PRELOAD = env_bool("SECURE_HSTS_PRELOAD", default=False)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
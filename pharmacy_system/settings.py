"""Production-ready Django settings for SmartPOS."""
from pathlib import Path
import os
from decouple import config, Csv
import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent

# -----------------------------------------------------------------------------
# Core / security
# -----------------------------------------------------------------------------
SECRET_KEY = config("SECRET_KEY", default="")
DEBUG = config("DEBUG", default=False, cast=bool)
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "django-insecure-local-development-only-change-this-long-development-key-2026"
    else:
        raise RuntimeError("SECRET_KEY must be set when DEBUG=False.")

ALLOWED_HOSTS = config("ALLOWED_HOSTS", default="127.0.0.1,localhost", cast=Csv())
RAILWAY_PUBLIC_DOMAIN = config("RAILWAY_PUBLIC_DOMAIN", default="").strip()
DAILY_REPORT_PUBLIC_BASE_URL = config("DAILY_REPORT_PUBLIC_BASE_URL", default=(f"https://{RAILWAY_PUBLIC_DOMAIN}" if RAILWAY_PUBLIC_DOMAIN else "")).strip().rstrip("/")
if RAILWAY_PUBLIC_DOMAIN and RAILWAY_PUBLIC_DOMAIN not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(RAILWAY_PUBLIC_DOMAIN)
# Railway's deploy health check calls the service with this Host header.
if "healthcheck.railway.app" not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append("healthcheck.railway.app")

INSTALLED_APPS = [
    "django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
    "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles",
    "django.contrib.humanize", "widget_tweaks",
    "pharmacies", "accounts", "inventory", "sales", "dashboard",
    "sms_integration", "core", "hq", "notifications", "customer_registry", "chat", "assistant", "rewards",
    "payments", "expenses", "daily_reports", "feedback", "testimonials", "layaway", "marketplace",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.gzip.GZipMiddleware",  # compress HTML/JSON responses (static files are pre-compressed by WhiteNoise)
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "core.middleware.ServiceRecoveryMiddleware",  # runs before DB-backed sessions/auth
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "core.seo.SearchVisibilityMiddleware",  # noindex for the signed-in app and temporary railway hosts
    "core.middleware.MaintenanceOwnerOnlyMiddleware",  # owner-only gate on the bypass domain
    "hq.impersonation.ImpersonationMiddleware",  # platform owner entering a customer workspace (audited)
    "accounts.middleware.SingleDeviceSessionMiddleware",  # one active device per account (accounts/single_device.py)
    "accounts.middleware.PharmacyContextMiddleware",
    "accounts.subscription_middleware.SubscriptionLockMiddleware",
    "accounts.middleware.AssistantPermissionMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "accounts.middleware.ForcePasswordChangeMiddleware",
    "accounts.middleware.ForcePinSetupMiddleware",  # every user must create a PIN (see accounts/device_pin.py)
    "accounts.middleware.DeviceIdleLockMiddleware",  # lock trusted POS sessions without logging users out
]

ROOT_URLCONF = "pharmacy_system.urls"

# Search-engine presentation (see core/seo.py)
SITE_URL = config("SITE_URL", default="https://smartbiashara.store").rstrip("/")
SITE_NAME = config("SITE_NAME", default="SmartPOS")
SITE_ALTERNATE_NAMES = config("SITE_ALTERNATE_NAMES", default="SmartBiashara")
GOOGLE_SITE_VERIFICATION = config("GOOGLE_SITE_VERIFICATION", default="")
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.debug",
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
        "pharmacy_system.context_processors.site_settings",
        "core.seo.seo_context",
        "hq.impersonation.impersonation_context",
        "feedback.context_processors.feedback_prompt",
        "marketplace.context.seller_workspace",
    ]},
}]
WSGI_APPLICATION = "pharmacy_system.wsgi.application"

# -----------------------------------------------------------------------------
# Database: Railway PostgreSQL is the production database.
# Local development can continue using SQLite.
# -----------------------------------------------------------------------------
DATABASE_URL = config("DATABASE_URL", default="").strip()
if DATABASE_URL:
    DATABASES = {"default": dj_database_url.parse(
        DATABASE_URL, conn_max_age=600, conn_health_checks=True, ssl_require=not DEBUG
    )}
    if "postgres" in DATABASES["default"].get("ENGINE", ""):
        # Keep idle connections alive through Railway's proxy so Django does not reuse a
        # connection the server already dropped ("connection already closed").
        DATABASES["default"].setdefault("OPTIONS", {}).update({
            "keepalives": 1, "keepalives_idle": 30, "keepalives_interval": 10, "keepalives_count": 5,
        })
elif not DEBUG:
    raise RuntimeError("DATABASE_URL is required for production. Attach Railway PostgreSQL and expose DATABASE_URL.")
else:
    SQLITE_PATH = config("SQLITE_PATH", default=str(BASE_DIR / "db.sqlite3"))
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.sqlite3", "NAME": SQLITE_PATH,
        "OPTIONS": {
            "init_command": "PRAGMA journal_mode=WAL;PRAGMA synchronous=NORMAL;PRAGMA busy_timeout=5000;PRAGMA foreign_keys=ON;",
            "timeout": 20,
        },
    }}

# Customer-facing name of the online payment tab/button. Change via env var PAYMENT_BRAND_NAME.
PAYMENT_BRAND_NAME = config("PAYMENT_BRAND_NAME", default="SmartPOS Pay")

AUTH_USER_MODEL = "accounts.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Africa/Dar_es_Salaam"  # Tanzania business timezone (EAT, UTC+3). Do not replace with server/UTC time.
USE_I18N = True
USE_TZ = True

# Late entry (sales typed in after the day they really happened, e.g. paper notebook).
# How many days back a sale may be recorded. 0 disables the feature.
LATE_ENTRY_MAX_DAYS = config("LATE_ENTRY_MAX_DAYS", default=3, cast=int)

# -----------------------------------------------------------------------------
# Static files
# -----------------------------------------------------------------------------
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

# -----------------------------------------------------------------------------
# Media: S3-compatible bucket in production; Android-safe local storage when
# no bucket is configured. This supports AWS S3, Cloudflare R2 and other
# S3-compatible buckets.
# -----------------------------------------------------------------------------
AWS_STORAGE_BUCKET_NAME = config("AWS_STORAGE_BUCKET_NAME", default="").strip()
AWS_ACCESS_KEY_ID = config("AWS_ACCESS_KEY_ID", default="").strip()
AWS_SECRET_ACCESS_KEY = config("AWS_SECRET_ACCESS_KEY", default="").strip()
AWS_S3_ENDPOINT_URL = config("AWS_S3_ENDPOINT_URL", default="").strip()
AWS_S3_REGION_NAME = config("AWS_S3_REGION_NAME", default="auto").strip()
AWS_S3_CUSTOM_DOMAIN = config("AWS_S3_CUSTOM_DOMAIN", default="").strip()
USE_S3 = bool(AWS_STORAGE_BUCKET_NAME and AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY)

if USE_S3:
    STORAGES = {
        "default": {"BACKEND": "storages.backends.s3.S3Storage"},
        "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
    }
    AWS_DEFAULT_ACL = None
    AWS_S3_FILE_OVERWRITE = False
    AWS_QUERYSTRING_AUTH = config("AWS_QUERYSTRING_AUTH", default=False, cast=bool)
    AWS_S3_SIGNATURE_VERSION = "s3v4"
    AWS_S3_ADDRESSING_STYLE = config("AWS_S3_ADDRESSING_STYLE", default="auto")
    AWS_S3_OBJECT_PARAMETERS = {"CacheControl": "max-age=86400"}
    MEDIA_URL = (f"https://{AWS_S3_CUSTOM_DOMAIN}/" if AWS_S3_CUSTOM_DOMAIN else "/media/")
else:
    STORAGES = {
        "default": {"BACKEND": "chat.storage.AndroidSafeFileSystemStorage"},
        "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
    }
    MEDIA_ROOT = Path(config("MEDIA_ROOT", default=str(BASE_DIR / "media")))
    MEDIA_URL = "/media/"

# Paid digital downloads (PDF books, videos...) live in PRIVATE storage that is never served from MEDIA_URL.
# With S3/R2 configured they use a private bucket prefix + short-lived signed URLs. Without a bucket they
# use this folder, which must sit on persistent storage (e.g. a Railway volume) and OUTSIDE MEDIA_ROOT.
if not USE_S3:
    PRIVATE_MEDIA_ROOT = Path(config("PRIVATE_MEDIA_ROOT", default=str(Path(MEDIA_ROOT).parent / (Path(MEDIA_ROOT).name + "_private"))))

# Marketplace static assets are referenced with a ?v= version in the template, so browsers can keep
# them for a long time (faster repeat visits). Bump the ?v= value in marketplace/base.html when changing them.
def _whitenoise_long_cache(headers, path, url):
    if path.replace("\\", "/").find("/marketplace/") != -1:
        headers["Cache-Control"] = "public, max-age=2592000"


WHITENOISE_ADD_HEADERS_FUNCTION = _whitenoise_long_cache

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# -----------------------------------------------------------------------------
# Durable background jobs (Celery + Redis). Customer SMS campaigns never run
# inside the web request process.
# -----------------------------------------------------------------------------
CELERY_BROKER_URL = config("REDIS_URL", default="").strip() or "redis://127.0.0.1:6379/0"
CELERY_RESULT_BACKEND = config("CELERY_RESULT_BACKEND", default="").strip() or CELERY_BROKER_URL
CELERY_TASK_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = config("CELERY_TASK_TIME_LIMIT", default=120, cast=int)
CELERY_TASK_SOFT_TIME_LIMIT = config("CELERY_TASK_SOFT_TIME_LIMIT", default=90, cast=int)
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CASH_AUTO_CLOSE_ENABLED = config("CASH_AUTO_CLOSE_ENABLED", default=True, cast=bool)
CASH_AUTO_CLOSE_DESCRIPTION = "Automatic calendar day-end locking is enabled by default; physical cash closing always requires manager verification."
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
# Premium Daily Sales Intelligence runs every minute; the task itself selects
# managers whose Africa/Dar_es_Salaam local delivery time is due.
# ---- Per-branch subscription policy (staged, never an abrupt lock) ----------
# due date -> GRACE (works normally, strong warning) -> READ_ONLY (view/reports,
# no new sales or changes) -> LOCKED (only billing, backup/export reachable by the owner).
BRANCH_BILLING_RENEWAL_WINDOW_DAYS = config("BRANCH_BILLING_RENEWAL_WINDOW_DAYS", default=10, cast=int)
BRANCH_BILLING_GRACE_DAYS = config("BRANCH_BILLING_GRACE_DAYS", default=5, cast=int)
BRANCH_BILLING_READONLY_DAYS = config("BRANCH_BILLING_READONLY_DAYS", default=25, cast=int)

CELERY_BEAT_SCHEDULE = {
    "system-notice-bell-delivery": {
        "task": "notifications.tasks.dispatch_due_system_notices",
        "schedule": 60.0,
    },
    "branch-billing-notices": {
        "task": "notifications.tasks.process_branch_billing",
        "schedule": 3600.0,
    },
    "premium-daily-sales-intelligence": {
        "task": "daily_reports.tasks.dispatch_due_daily_reports",
        "schedule": 60.0,
    },
    "cleanup-expired-daily-sales-reports": {
        "task": "daily_reports.tasks.cleanup_expired_reports",
        "schedule": 3600.0,
    },
    "professional-meal-reminders": {
        "task": "notifications.tasks.dispatch_due_meal_reminders",
        "schedule": 60.0,
    },
    "professional-web-push-delivery": {
        "task": "notifications.tasks.dispatch_pending_web_push_notifications",
        "schedule": 30.0,
    },
    "professional-branch-automations": {
        "task": "notifications.tasks.run_professional_automations",
        "schedule": 300.0,
    },
    "professional-cash-day-end": {
        "task": "notifications.tasks.auto_close_completed_cash_days",
        "schedule": 60.0,
    },
    "sales-integrity-checks": {
        "task": "notifications.tasks.run_sales_integrity_checks",
        "schedule": 1800.0,
    },
}

# Returns / voids: how long after a sale an EMPLOYEE may still request a return.
# Shop Owners / Supervisors are never limited by this window.
SALE_RETURN_EMPLOYEE_WINDOW_HOURS = config("SALE_RETURN_EMPLOYEE_WINDOW_HOURS", default=24, cast=int)

# PIN sign-in on trusted devices (accounts/device_pin.py). Mandatory for every user
# except Branch Display kiosks; set DEVICE_PIN_REQUIRED=False to switch the feature off.
DEVICE_PIN_REQUIRED = config("DEVICE_PIN_REQUIRED", default=True, cast=bool)
DEVICE_PIN_COOKIE_DAYS = config("DEVICE_PIN_COOKIE_DAYS", default=90, cast=int)
DEVICE_PIN_IDLE_DAYS = config("DEVICE_PIN_IDLE_DAYS", default=30, cast=int)
DEVICE_PIN_MAX_DEVICES = config("DEVICE_PIN_MAX_DEVICES", default=5, cast=int)
# Single-device sign-in: an account can be active on one device at a time.
SINGLE_DEVICE_LOGIN_ENABLED = config("SINGLE_DEVICE_LOGIN_ENABLED", default=True, cast=bool)
# Minutes of inactivity after which an abandoned device frees the account (0 = only logout/session expiry).
SINGLE_DEVICE_IDLE_RELEASE_MINUTES = config("SINGLE_DEVICE_IDLE_RELEASE_MINUTES", default=15, cast=int)
SINGLE_DEVICE_TOUCH_SECONDS = config("SINGLE_DEVICE_TOUCH_SECONDS", default=60, cast=int)
DEVICE_PIN_IDLE_LOCK_MINUTES = config("DEVICE_PIN_IDLE_LOCK_MINUTES", default=5, cast=int)
DEVICE_PIN_IP_RATE_LIMIT_ATTEMPTS = config("DEVICE_PIN_IP_RATE_LIMIT_ATTEMPTS", default=12, cast=int)
DEVICE_PIN_IP_RATE_LIMIT_WINDOW_SECONDS = config("DEVICE_PIN_IP_RATE_LIMIT_WINDOW_SECONDS", default=60, cast=int)

# Google Sign-In ("Continue with Google"). Create an OAuth client (type: Web application) in Google Cloud
# Console, add your site URL under "Authorized JavaScript origins", and set GOOGLE_CLIENT_ID. Empty = hidden.
GOOGLE_CLIENT_ID = config("GOOGLE_CLIENT_ID", default="").strip()
# Django's default (same-origin) cuts the link between a page and the popup it opens, so Google's popup can
# never hand the sign-in result back ("Processing request..." forever). Google requires this value.
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin-allow-popups"

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "dashboard:home"
LOGOUT_REDIRECT_URL = "accounts:login"
SESSION_COOKIE_AGE = 60 * 60 * 8
# Keep an active cashier session alive while the POS is open. The POS sends
# a lightweight authenticated heartbeat, so normal use does not unexpectedly
# end in a checkout "session expired" error. Closing the browser still ends
# the session as configured below.
SESSION_SAVE_EVERY_REQUEST = True
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
X_FRAME_OPTIONS = "DENY"

SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

CSRF_TRUSTED_ORIGINS = config("CSRF_TRUSTED_ORIGINS", default="", cast=Csv())
if RAILWAY_PUBLIC_DOMAIN:
    railway_origin = f"https://{RAILWAY_PUBLIC_DOMAIN}"
    if railway_origin not in CSRF_TRUSTED_ORIGINS:
        CSRF_TRUSTED_ORIGINS.append(railway_origin)

if not DEBUG:
    SECURE_SSL_REDIRECT = config("SECURE_SSL_REDIRECT", default=True, cast=bool)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
    SECURE_HSTS_SECONDS = config("SECURE_HSTS_SECONDS", default=31536000, cast=int)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = config("SECURE_HSTS_PRELOAD", default=True, cast=bool)
    # The platform health check calls /health/ over plain HTTP inside Railway's network.
    SECURE_REDIRECT_EXEMPT = [r"^health/$"]

# -----------------------------------------------------------------------------
# Service recovery / maintenance
# -----------------------------------------------------------------------------
# Toggle this in Railway Variables when planned maintenance is required.
# The recovery middleware runs before sessions/auth so this page still works
# even when the database is temporarily unavailable.
MAINTENANCE_MODE = config("MAINTENANCE_MODE", default=False, cast=bool)
MAINTENANCE_MESSAGE = config("MAINTENANCE_MESSAGE", default="We are carrying out a brief service improvement. Please try again shortly.").strip()
MAINTENANCE_RETRY_SECONDS = config("MAINTENANCE_RETRY_SECONDS", default=60, cast=int)
# Owner-only domain(s) that keep working while MAINTENANCE_MODE is on, e.g.
#   MAINTENANCE_BYPASS_HOSTS=owner.yourdomain.com
# Attach the domain to the Railway service. On these hosts only superusers may use the
# system during maintenance (everyone else still sees the maintenance page after login).
MAINTENANCE_BYPASS_HOSTS = [h.strip().lower() for h in config("MAINTENANCE_BYPASS_HOSTS", default="", cast=Csv()) if h.strip()]
for _host in MAINTENANCE_BYPASS_HOSTS:
    if _host not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(_host)
    if f"https://{_host}" not in CSRF_TRUSTED_ORIGINS:
        CSRF_TRUSTED_ORIGINS.append(f"https://{_host}")

LOW_STOCK_DEFAULT_THRESHOLD = 10
EXPIRY_WARNING_DAYS = 30
OTP_VALID_MINUTES = 5

# NOTE: handler403/404/500 are NOT read from settings.py by Django — they
# must be defined in the ROOT_URLCONF module. See pharmacy_system/urls.py.

BACKUP_DIR = Path(config("BACKUP_DIR", default=str(BASE_DIR / "backups")))

# -----------------------------------------------------------------------------
# Help Assistant (floating chatbot — see docs/ASSISTANT.md)
# Free by default: with ANTHROPIC_API_KEY empty, it runs on the local offline
# FAQ engine (assistant/offline.py) — no API calls, no cost. Setting a key
# upgrades it to Claude-powered answers (assistant/knowledge.py) automatically.
# The widget only appears when ASSISTANT_ENABLED is true.
# -----------------------------------------------------------------------------
ANTHROPIC_API_KEY = config("ANTHROPIC_API_KEY", default="").strip()
ASSISTANT_ENABLED = config("ASSISTANT_ENABLED", default=True, cast=bool)
ASSISTANT_MODEL = config("ASSISTANT_MODEL", default="claude-haiku-4-5-20251001").strip()
ASSISTANT_MAX_TOKENS = config("ASSISTANT_MAX_TOKENS", default=600, cast=int)
ASSISTANT_TIMEOUT = config("ASSISTANT_TIMEOUT", default=25, cast=int)  # seconds; keep below GUNICORN_TIMEOUT
# Cost protection (counters live in Django's cache; with several gunicorn workers on the
# default in-memory cache each worker counts separately, so effective limits are approximate).
ASSISTANT_PUBLIC_PER_10_MIN = config("ASSISTANT_PUBLIC_PER_10_MIN", default=12, cast=int)
ASSISTANT_PUBLIC_PER_DAY = config("ASSISTANT_PUBLIC_PER_DAY", default=60, cast=int)
ASSISTANT_PUBLIC_GLOBAL_PER_DAY = config("ASSISTANT_PUBLIC_GLOBAL_PER_DAY", default=1000, cast=int)
ASSISTANT_STAFF_PER_HOUR = config("ASSISTANT_STAFF_PER_HOUR", default=40, cast=int)
ASSISTANT_STAFF_PER_DAY = config("ASSISTANT_STAFF_PER_DAY", default=300, cast=int)

# Customer care (see docs/ASSISTANT.md)
# Technical Support numbers the assistant gives out (comma-separated, international format).
SUPPORT_PHONES = [
    p.strip() for p in config("SUPPORT_PHONES", default="+255610117824,+255678828519").split(",") if p.strip()
]
SUPPORT_AGENT_NAME = config("SUPPORT_AGENT_NAME", default="Mutech")
SUPPORT_WHATSAPP_PHONE = config("SUPPORT_WHATSAPP_PHONE", default="+255610117824")
SUPPORT_ADMIN_USERNAME = config("SUPPORT_ADMIN_USERNAME", default="")
# If a customer stays this many seconds without asking anything, the assistant checks in on them.
ASSISTANT_IDLE_SECONDS = config("ASSISTANT_IDLE_SECONDS", default=60, cast=int)

# -----------------------------------------------------------------------------
# Cache / sessions / logging (see docs/SCALING_AND_PRODUCTION.md)
# With REDIS_URL set (Railway Redis service) the cache is SHARED by every gunicorn
# worker: typing indicators, chatbot rate limits and the notification-cleanup
# throttle behave correctly no matter which worker answers. Without it, each worker
# keeps its own in-memory cache (fine for development / a single small worker).
# A Redis outage never takes the site down: cache errors are ignored (IGNORE_EXCEPTIONS).
# -----------------------------------------------------------------------------
REDIS_URL = config("REDIS_URL", default="").strip()
if REDIS_URL:
    CACHES = {
        "default": {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": REDIS_URL,
            "KEY_PREFIX": "pharmacy",
            "OPTIONS": {
                "CLIENT_CLASS": "django_redis.client.DefaultClient",
                "SOCKET_CONNECT_TIMEOUT": 2,
                "SOCKET_TIMEOUT": 2,
                "IGNORE_EXCEPTIONS": True,
            },
        }
    }
    DJANGO_REDIS_LOG_IGNORED_EXCEPTIONS = True
    # Read sessions from the cache first (falls back to the database): one less DB query per request.
    SESSION_ENGINE = "django.contrib.sessions.backends.cached_db"
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "pharmacy-default",
        }
    }

LOG_LEVEL = config("LOG_LEVEL", default="INFO").strip().upper()
# --- Field-level encryption of payment data (V117.11) -----------------------------------------------
# Comma-separated base64 keys. FIRST key encrypts, all keys can decrypt (rotation). Create one with
#   python manage.py generate_field_key
# Keep it ONLY in Railway Variables (never in git) and back it up: without it encrypted data is unreadable.
FIELD_ENCRYPTION_KEYS = config("FIELD_ENCRYPTION_KEYS", default="", cast=Csv())

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {"redact_sensitive": {"()": "core.logfilters.RedactSensitiveFilter"}},
    "formatters": {
        "standard": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "standard", "filters": ["redact_sensitive"]},
    },
    "root": {"handlers": ["console"], "level": LOG_LEVEL},
}


# Optional production Web Push (VAPID). If these are absent, the in-app bell
# continues to work and the browser simply does not register background push.
WEBPUSH_VAPID_PUBLIC_KEY = config("WEBPUSH_VAPID_PUBLIC_KEY", default="").strip()
WEBPUSH_VAPID_PRIVATE_KEY = config("WEBPUSH_VAPID_PRIVATE_KEY", default="").strip()
WEBPUSH_VAPID_CLAIMS_EMAIL = config("WEBPUSH_VAPID_CLAIMS_EMAIL", default="mailto:admin@example.com").strip()


# --- Barcode name look-up for the Register sheet (V54.77) - FREE services, no API key needed -------------
# Open Food Facts, Open Beauty Facts, Open Products Facts, then UPCitemdb (free: ~100 requests/day) as backup. Set BARCODE_LOOKUP_ENABLED=False to turn off.
BARCODE_LOOKUP_ENABLED = config("BARCODE_LOOKUP_ENABLED", default=True, cast=bool)
BARCODE_LOOKUP_TIMEOUT = config("BARCODE_LOOKUP_TIMEOUT", default=3.0, cast=float)      # seconds per service
BARCODE_LOOKUP_BUDGET = config("BARCODE_LOOKUP_BUDGET", default=7.0, cast=float)        # seconds for all services together
BARCODE_LOOKUP_NOT_FOUND_DAYS = config("BARCODE_LOOKUP_NOT_FOUND_DAYS", default=7, cast=int)
BARCODE_LOOKUP_UPC_DAILY_LIMIT = config("BARCODE_LOOKUP_UPC_DAILY_LIMIT", default=90, cast=int)
BARCODE_LOOKUP_USER_AGENT = config("BARCODE_LOOKUP_USER_AGENT", default="SmartPOS/54 (barcode name lookup)")


# Marketplace property listings: sellers publish their own adverts instantly (no owner review).
# Set env MARKETPLACE_LISTING_AUTO_APPROVE=0 to require platform-owner approval again.
MARKETPLACE_LISTING_AUTO_APPROVE = config("MARKETPLACE_LISTING_AUTO_APPROVE", default=True, cast=bool)

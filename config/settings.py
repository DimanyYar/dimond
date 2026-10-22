"""Settings for every environment. Values that differ between machines come from env vars.

See .env.example for the full list. Nothing secret is stored in the repository.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None:
        raise RuntimeError(f"Environment variable {name} is required")
    return value


def env_bool(name: str, default: bool = False) -> bool:
    return env(name, str(default)).lower() in {"1", "true", "yes"}


DEBUG = env_bool("DJANGO_DEBUG")
SECRET_KEY = env("DJANGO_SECRET_KEY")
ALLOWED_HOSTS = env("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
# The Django admin lives at an unguessable path, never /admin/ (see PRD, "PM workspace vs admin").
ADMIN_URL = env("DJANGO_ADMIN_URL", "admin-local/")
# Sign-in links in emails point here; never taken from the request's Host header.
SITE_URL = env("SITE_URL", "http://localhost:8000").rstrip("/")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "apps.core",
    "apps.accounts",
    "apps.requests",
    "apps.files",
    "apps.tracker",
    "apps.notifications",
]

AUTH_USER_MODEL = "accounts.User"

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.core.middleware.NotFoundPage",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.tracker.context_processors.sync",
                "apps.notifications.context_processors.mail",
            ],
        },
    },
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("POSTGRES_DB", "dimond"),
        "USER": env("POSTGRES_USER", "dimond"),
        "PASSWORD": env("POSTGRES_PASSWORD"),
        "HOST": env("POSTGRES_HOST", "localhost"),
        "PORT": env("POSTGRES_PORT", "5432"),
    }
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = False
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

# Attachments. Private: no address serves this folder; files go out only through
# /files/<id>/ after an access check (PRD: files and attachment links). MEDIA_URL stays unset.
MEDIA_ROOT = BASE_DIR / env("FILES_ROOT", "uploads")

EMAIL_HOST = env("EMAIL_HOST", "localhost")
EMAIL_PORT = int(env("EMAIL_PORT", "1025"))
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "Dimon <dimon@localhost>")
# Mail goes over SMTP (Mailpit locally) until a Resend key is set; then through Resend's API
# (ADR 0009). Switching providers is this setting, not code.
# Resend's free plan sends up to 100 emails a day; Logs · Emails counts against this number.
EMAIL_DAILY_LIMIT = int(env("EMAIL_DAILY_LIMIT", "100"))
# Seconds to wait for the mail server: "Try now" on Logs runs in the page's request.
EMAIL_TIMEOUT = 10
RESEND_API_KEY = env("RESEND_API_KEY", "")
if RESEND_API_KEY:
    # Resend refuses any sender outside a verified domain, so the local default would make
    # every email fail quietly; stop at start-up instead.
    if DEFAULT_FROM_EMAIL.rstrip(">").endswith("@localhost"):
        raise RuntimeError("Set DEFAULT_FROM_EMAIL to an address on the domain verified in Resend")
    EMAIL_BACKEND = "anymail.backends.resend.EmailBackend"
    # EMAIL_TIMEOUT covers SMTP only; anymail's own default is 30 seconds.
    ANYMAIL = {"RESEND_API_KEY": RESEND_API_KEY, "REQUESTS_TIMEOUT": EMAIL_TIMEOUT}
# Resend's delivery reports (ADR 0021): the signing secret of the webhook set up in Resend.
# Empty turns the webhook off.
ANYMAIL_RESEND_SIGNING_SECRET = env("RESEND_WEBHOOK_SECRET", "")

# Jira access for every client project. Which tracker is used is switched in the admin
# (TrackerSettings); only the address and the account live here, never in the database.
TRACKER_JIRA_URL = env("TRACKER_JIRA_URL", "")
TRACKER_JIRA_USERNAME = env("TRACKER_JIRA_USERNAME", "")
TRACKER_JIRA_PASSWORD = env("TRACKER_JIRA_PASSWORD", "")
# The secret part of the address Jira's webhook calls (ADR 0020); empty turns the webhook off.
TRACKER_JIRA_WEBHOOK_TOKEN = env("TRACKER_JIRA_WEBHOOK_TOKEN", "")

LOGIN_URL = "sign_in"
SESSION_COOKIE_AGE = 60 * 60 * 24 * 30
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
CSRF_FAILURE_VIEW = "apps.accounts.views.stale_form"
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO")},
}

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from django.core.management.utils import get_random_secret_key


BASE_DIR = Path(__file__).resolve().parent.parent

DEBUG = os.environ.get("DJANGO_DEBUG") == "1"
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("Set DJANGO_SECRET_KEY when DJANGO_DEBUG is disabled.")
    SECRET_KEY = get_random_secret_key()

ALLOWED_HOSTS = ["localhost", "127.0.0.1", "[::1]"]
ALLOWED_HOSTS.extend(
    host.strip()
    for host in os.environ.get("DJANGO_ALLOWED_HOSTS", "").split(",")
    if host.strip()
)

INSTALLED_APPS = [
    "config.admin.HotelAdminConfig",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "reception",  # Extends clearsessions to prune detached reception conversations.
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "bookings",
    "guest_accounts",
    "model_providers",
    "telephony",
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

if os.environ.get("DJANGO_TRUST_PROXY_PROTO") == "1":
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

ROOT_URLCONF = "config.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "config" / "templates"],
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

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("DJANGO_DB_PATH", BASE_DIR / "db.sqlite3"),
        "OPTIONS": {"transaction_mode": "IMMEDIATE", "timeout": 10},
    },
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Europe/Warsaw"
USE_I18N = True
USE_TZ = True
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

BOOKING_API_KEY = os.environ.get("BOOKING_API_KEY", "")
HOTEL_MODEL_API_KEY = os.environ.get("HOTEL_MODEL_API_KEY", "")
HOTEL_MODEL_BASE_URL = os.environ.get("HOTEL_MODEL_BASE_URL", "https://api.openai.com/v1")
HOTEL_OLLAMA_URL = os.environ.get("HOTEL_OLLAMA_URL", "http://host.docker.internal:11434")
try:
    HOTEL_OLLAMA_TIMEOUT = int(os.environ.get("HOTEL_OLLAMA_TIMEOUT", "30"))
except ValueError as error:
    raise ImproperlyConfigured("HOTEL_OLLAMA_TIMEOUT must be an integer number of seconds.") from error
if not 1 <= HOTEL_OLLAMA_TIMEOUT <= 120:
    raise ImproperlyConfigured("HOTEL_OLLAMA_TIMEOUT must be between 1 and 120 seconds.")

HOTEL_TELEPHONY_PUBLIC_BASE_URL = os.environ.get("HOTEL_TELEPHONY_PUBLIC_BASE_URL", "").rstrip("/")
HOTEL_PHONE_RECEPTION_NUMBER = os.environ.get("HOTEL_PHONE_RECEPTION_NUMBER", "").strip()
HOTEL_TWILIO_AUTH_TOKEN = os.environ.get("HOTEL_TWILIO_AUTH_TOKEN", "")
HOTEL_PIPER_URL = os.environ.get("HOTEL_PIPER_URL", "").rstrip("/")
HOTEL_ASR_URL = os.environ.get("HOTEL_ASR_URL", "").rstrip("/")
HOTEL_SPEECH_TRAFFIC_DB = os.environ.get("HOTEL_SPEECH_TRAFFIC_DB", "")
HOTEL_SPEECH_ROUTE_TARGETS = os.environ.get("HOTEL_SPEECH_ROUTE_TARGETS", "")
HOTEL_SPEECH_HOST_POLICIES = os.environ.get("HOTEL_SPEECH_HOST_POLICIES") or "{}"
HOTEL_SPEECH_ADMISSION_POLICIES = os.environ.get("HOTEL_SPEECH_ADMISSION_POLICIES") or "{}"
HOTEL_SPEECH_CONTROLLER_URL = os.environ.get("HOTEL_SPEECH_CONTROLLER_URL", "")
HOTEL_SPEECH_CONTROL_URL = os.environ.get("HOTEL_SPEECH_CONTROL_URL", "")
HOTEL_SPEECH_CONTROL_TOKEN = os.environ.get("HOTEL_SPEECH_CONTROL_TOKEN", "")
HOTEL_SPEECH_CONTROL_RECOVERY_ENABLED = os.environ.get("HOTEL_SPEECH_CONTROL_RECOVERY_ENABLED") == "1"
HOTEL_SPEECH_CONTROL_SWITCH_ENABLED = os.environ.get("HOTEL_SPEECH_CONTROL_SWITCH_ENABLED") == "1"
HOTEL_SPEECH_CONTROL_HOST = os.environ.get("HOTEL_SPEECH_CONTROL_HOST", "")
HOTEL_SPEECH_CONTROLLER_TOKEN = os.environ.get("HOTEL_SPEECH_CONTROLLER_TOKEN", "")
HOTEL_SPEECH_CONTROLLER_HOST = os.environ.get("HOTEL_SPEECH_CONTROLLER_HOST", "")
HOTEL_GOOGLE_GUEST_SIGNIN_ENABLED = os.environ.get("HOTEL_GOOGLE_GUEST_SIGNIN_ENABLED") == "1"
HOTEL_GOOGLE_CLIENT_ID = os.environ.get("HOTEL_GOOGLE_CLIENT_ID", "").strip()

HOTEL_PARSER_TRAINING_ENABLED = os.environ.get("HOTEL_PARSER_TRAINING_ENABLED") == "1"
try:
    HOTEL_PIPER_TIMEOUT = int(os.environ.get("HOTEL_PIPER_TIMEOUT", "10"))
except ValueError as error:
    raise ImproperlyConfigured("HOTEL_PIPER_TIMEOUT must be an integer number of seconds.") from error
if not 1 <= HOTEL_PIPER_TIMEOUT <= 120:
    raise ImproperlyConfigured("HOTEL_PIPER_TIMEOUT must be between 1 and 120 seconds.")

EMAIL_HOST = os.environ.get("HOTEL_SMTP_HOST", "").strip()
EMAIL_HOST_USER = os.environ.get("HOTEL_SMTP_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("HOTEL_SMTP_PASSWORD", "")
EMAIL_USE_SSL = os.environ.get("HOTEL_SMTP_SSL") == "1"
EMAIL_USE_TLS = bool(EMAIL_HOST) and not EMAIL_USE_SSL
EMAIL_TIMEOUT = 10
DEFAULT_FROM_EMAIL = os.environ.get("HOTEL_CONFIRMATION_FROM", "").strip()
try:
    EMAIL_PORT = int(os.environ.get("HOTEL_SMTP_PORT") or ("465" if EMAIL_USE_SSL else "587"))
except ValueError as error:
    raise ImproperlyConfigured("HOTEL_SMTP_PORT must be an integer port number.") from error
if not 1 <= EMAIL_PORT <= 65535:
    raise ImproperlyConfigured("HOTEL_SMTP_PORT must be between 1 and 65535.")
HOTEL_TWILIO_ACCOUNT_SID = os.environ.get("HOTEL_TWILIO_ACCOUNT_SID", "").strip()
HOTEL_TWILIO_SMS_FROM = os.environ.get("HOTEL_TWILIO_SMS_FROM", "").strip()
HOTEL_CONFIRMATION_WAIT_METHODS = tuple(
    method for method in ("email", "sms")
    if os.environ.get(f"HOTEL_CONFIRMATION_WAIT_{method.upper()}") == "1"
)

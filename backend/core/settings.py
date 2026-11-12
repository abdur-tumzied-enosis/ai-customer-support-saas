import os
import sys
from pathlib import Path
import environ
from django.core.exceptions import ImproperlyConfigured

from core.storage import r2_endpoint, r2_storages


BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
	DEBUG=(bool, False),
	SECRET_KEY=(str, "dev-secret-key"),
	ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
)
environ.Env.read_env(os.path.join(BASE_DIR.parent, ".env"))  # ../.env

DEBUG = env("DEBUG")
SECRET_KEY = env("SECRET_KEY")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")
# Production must not boot on the dev placeholders
if not DEBUG and SECRET_KEY in ("dev-secret-key", "change-me"):
	raise ImproperlyConfigured("Set SECRET_KEY (or DEBUG=true for local dev)")

INSTALLED_APPS = [
	"django.contrib.admin",
	"django.contrib.auth",
	"django.contrib.contenttypes",
	"django.contrib.sessions",
	"django.contrib.messages",
	"django.contrib.staticfiles",
	"corsheaders",
	"rest_framework",
	"accounts",
	"channels_app",
	"agents",
	"knowledge",
	"orders",
	"subscriptions",
	"usage",
	"billing",
	"analytics",
]

MIDDLEWARE = [
	"django.middleware.security.SecurityMiddleware",
	"django.contrib.sessions.middleware.SessionMiddleware",
	"corsheaders.middleware.CorsMiddleware",
	"django.middleware.common.CommonMiddleware",
	"django.middleware.csrf.CsrfViewMiddleware",
	"django.contrib.auth.middleware.AuthenticationMiddleware",
	"django.contrib.messages.middleware.MessageMiddleware",
	"django.middleware.clickjacking.XFrameOptionsMiddleware",
	"core.middleware.RequestLoggingMiddleware",
	"core.middleware.ExceptionHandlingMiddleware",
]

ROOT_URLCONF = "core.urls"

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
			],
		},
	},
]

WSGI_APPLICATION = "core.wsgi.application"
ASGI_APPLICATION = "core.asgi.application"

DATABASES = {
	"default": {
		"ENGINE": "django.db.backends.postgresql",
		"NAME": env("POSTGRES_DB", default="auto_magic_pro"),
		"USER": env("POSTGRES_USER", default="postgres"),
		"PASSWORD": env("POSTGRES_PASSWORD", default="postgres"),
		"HOST": env("POSTGRES_HOST", default="localhost"),
		"PORT": env("POSTGRES_PORT", default="5432"),
		"CONN_MAX_AGE": 60,  # reuse connections across requests instead of one per request
		"CONN_HEALTH_CHECKS": True,  # drop a reused connection that died (Postgres restart) instead of erroring
	}
}

# Custom User Model
AUTH_USER_MODEL = 'accounts.User'

AUTH_PASSWORD_VALIDATORS = [
	{"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
	{"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
	{"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
	{"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
# Uploads (documents, photos, audio) live in a private Cloudflare R2 bucket: `local` in dev, `main` in prod
# (docs/r2-storage.md). Tests use in-memory storage and never need R2 credentials.
TESTING = "test" in sys.argv
STORAGES = r2_storages(os.environ, DEBUG, TESTING)
R2_ENDPOINT_URL = "" if TESTING else r2_endpoint(os.environ.get("R2_ACCOUNT_ID", ""))

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Celery / Redis
REDIS_URL = env("REDIS_URL", default="redis://localhost:6379/0")
# Shared across processes: the per-conversation reply lock (channels_app.reply) needs it
CACHES = {"default": {"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": REDIS_URL}}
# Tests get an in-memory broker: a task a test queues must never reach the live workers (its rows are in the test DB)
CELERY_BROKER_URL = "memory://" if TESTING else REDIS_URL
CELERY_RESULT_BACKEND = "cache+memory://" if TESTING else REDIS_URL
# Customer replies get their own queue/worker so document ingestion can't delay them (docker-compose: celery-replies)
CELERY_TASK_ROUTES = {
    "channels_app.reply.process_reply": {"queue": "replies"},
    "channels_app.reply.deliver_outbound": {"queue": "replies"},
    "channels_app.reply.recover_pending_deliveries": {"queue": "replies"},
    "channels_app.media_store.store_message_media": {"queue": "replies"},  # on the reply path for photos
}
STUCK_JOB_THRESHOLD_MINUTES =env.int("STUCK_JOB_THRESHOLD_MINUTES", default=120)
CELERY_BEAT_SCHEDULE = {
    'recover-stuck-ingestion-jobs': {
        'task': 'knowledge.tasks.recover_stuck_jobs',
        'schedule': 600,  # every 10 minutes
    },
    'summarize-idle-episodes': {
        'task': 'channels_app.memory.summarize_idle_episodes',
        'schedule': 1800,
    },
    'cleanup-abandoned-uploads': {
        'task': 'knowledge.tasks.cleanup_abandoned_uploads',
        'schedule': 900,
    },
    'expire-stale-drafts': {
        'task': 'orders.tasks.expire_stale_drafts',
        'schedule': 3600,  # hourly; each project sets how long a draft lives
    },
    'recover-stuck-replies': {
        'task': 'channels_app.reply.recover_stuck_replies',
        'schedule': 300,
    },
    'recover-pending-deliveries': {
        'task': 'channels_app.reply.recover_pending_deliveries',
        'schedule': 30,
    },
    'billing-reconcile': {
        'task': 'billing.tasks.reconcile',
        'schedule': 600,
    },
    'sync-product-sources': {
        'task': 'knowledge.tasks.sync_product_sources',
        'schedule': 300,  # every 5 minutes: runs the sources that are due (hourly/daily each)
    },
    'propose-instructions': {
        'task': 'channels_app.memory.propose_instructions',
        'schedule': 86400,  # daily
    },
    'sync-openrouter-prices': {
        'task': 'agents.tasks.sync_openrouter_prices',
        'schedule': 86400,  # daily
    },
}

# Qdrant
QDRANT_URL = env("QDRANT_URL", default="http://localhost:6333")
QDRANT_API_KEY = env("QDRANT_API_KEY", default=None)  # Optional: for production authentication
# Product image search. Master switch (platform kill switch); per-tenant grant is Project.image_search_allowed.
IMAGE_SEARCH_ENABLED = env.bool("IMAGE_SEARCH_ENABLED", default=False)
IMAGE_SEARCH_MIN_SCORE = env.float("IMAGE_SEARCH_MIN_SCORE", default=0.80)
# cohere/rerank-5-fast relevance (0..1) below which a retrieved chunk counts as unrelated. Tuned on the v3.5 scores
# from the Bengali/English eval (docs/evals/bengali_retrieval.json): relevant 0.28..0.89, unrelated 0.01..0.49. 0.2 keeps
# every relevant chunk. Not yet measured for rerank-5-fast: re-tune it on real questions.
RERANK_MIN_SCORE = env.float("RERANK_MIN_SCORE", default=0.2)

# Meta / Facebook
META_APP_ID = env("META_APP_ID", default="")
META_APP_SECRET = env("META_APP_SECRET", default="")
FB_VERIFY_TOKEN = env("FB_VERIFY_TOKEN", default="verify-token")
PUBLIC_BASE_URL = env("PUBLIC_BASE_URL", default="")  # how Meta reaches this backend (ngrok/domain); "" → request host

# Google / Gemini
GOOGLE_API_KEY = env("GOOGLE_API_KEY", default="")
GOOGLE_USE_VERTEXAI = env.bool("GOOGLE_USE_VERTEXAI", default=True)  # True: Vertex AI express mode (API key); False: Gemini Developer API
GOOGLE_CLOUD_PROJECT = env("GOOGLE_CLOUD_PROJECT", default=None)  # Vertex AI only
GOOGLE_CLOUD_LOCATION = env("GOOGLE_CLOUD_LOCATION", default="global")  # Vertex AI only
OPENROUTER_API_KEY = env("OPENROUTER_API_KEY", default="")
LLM_PROVIDER = env("LLM_PROVIDER", default="google")  # "google" or "openrouter"
LLM_MODEL = env("LLM_MODEL", default=None)  # None → provider default (see agents/llm.py)
LLM_FALLBACK_MODEL = env("LLM_FALLBACK_MODEL", default=None)  # set to retry failed reply calls on this model
LLM_FALLBACK_PROVIDER = env("LLM_FALLBACK_PROVIDER", default=None)  # None → LLM_PROVIDER
# Reply temperature; None → provider default. ~0.2 suits factual support, but Google advises leaving
# Gemini 3 models at their default 1.0 (lower can loop or degrade), so only set it for other models.
LLM_TEMPERATURE = env.float("LLM_TEMPERATURE", default=None)
# Gemini 3+ thinking for customer replies: low/medium/high ("minimal" → 400 on gemini-3.8-flash); None → model
# default. Lower = faster replies, so each reply worker answers more customers. Check `manage.py run_reply_evals`
# before lowering it: on 2026-10-25 "low" cut output tokens 3-5x but missed a catalog answer the default gave.
LLM_THINKING_LEVEL = env("LLM_THINKING_LEVEL", default=None)
HANDOFF_AUTO_RESUME_HOURS = env.int("HANDOFF_AUTO_RESUME_HOURS", default=24)
HANDOFF_MESSAGE = env("HANDOFF_MESSAGE", default="I've passed this to our team — a person will reply here soon.")
HANDOFF_MESSAGE_BN = env("HANDOFF_MESSAGE_BN", default="আপনার বিষয়টি আমাদের টিমকে জানিয়েছি — একজন প্রতিনিধি শীঘ্রই এখানে উত্তর দেবেন।")  # sent when the customer writes in Bengali script
MEMORY_LLM_MODEL = env("MEMORY_LLM_MODEL", default=None)  # background memory work; None → LLM_MODEL
EPISODE_GAP_HOURS = env.int("EPISODE_GAP_HOURS", default=12)  # silence that closes a conversation episode
# Contact memory extraction (one LLM call) runs this long after the first reply of a burst and covers the whole
# burst, instead of once per reply. Recent turns are in the reply's history anyway, so the delay costs nothing.
MEMORY_UPDATE_DELAY_SECONDS = env.int("MEMORY_UPDATE_DELAY_SECONDS", default=300)
# Website widget limits (channels_app.widget), DRF rate strings. The widget cap bounds LLM spend per site: the
# allowed-origins list can't stop scripts calling the API directly.
WIDGET_THROTTLE_RATES = {
	"session": env("WIDGET_SESSION_RATE", default="60/hour"),  # new visitors per IP (mobile carriers share IPs)
	"visitor_messages": env("WIDGET_VISITOR_MESSAGE_RATE", default="10/min"),
	"visitor_polls": "120/min",  # the open panel polls every 1-10s
	"widget_messages": env("WIDGET_MESSAGE_RATE", default="2000/day"),
}
REPLY_DEBOUNCE_SECONDS = {  # channels not listed: 0
	"facebook": env.int("FACEBOOK_REPLY_DEBOUNCE_SECONDS", default=3),
	"instagram": env.int("INSTAGRAM_REPLY_DEBOUNCE_SECONDS", default=3),
	"whatsapp": env.int("WHATSAPP_REPLY_DEBOUNCE_SECONDS", default=3),
}

# Django REST Framework
REST_FRAMEWORK = {
	"DEFAULT_AUTHENTICATION_CLASSES": [
		"rest_framework.authentication.SessionAuthentication",
	],
	"DEFAULT_PERMISSION_CLASSES": [
		"rest_framework.permissions.IsAuthenticated",
	],
	"DEFAULT_RENDERER_CLASSES": [
		"rest_framework.renderers.JSONRenderer",
	],
	# Reverse proxies in front of gunicorn (production: 1). Unset, every client shares the proxy's IP and
	# per-IP throttles (auth, widget sessions) become global.
	"NUM_PROXIES": env.int("NUM_PROXIES", default=None),
}

# CORS Settings
# The dashboard's origin(s), e.g. https://app.example.com. The widget is served from this backend, so it needs none.
CORS_ALLOWED_ORIGINS = env.list(
	"CORS_ALLOWED_ORIGINS",
	default=["http://localhost:3000", "http://localhost:3001", "http://localhost:3002", "http://127.0.0.1:3000", "http://127.0.0.1:3002"],
)

CORS_ALLOW_CREDENTIALS = True

# CSRF Settings for API
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=CORS_ALLOWED_ORIGINS)
# The dashboard reads the csrftoken cookie from JS; when it runs on a sibling subdomain (app. vs api.), set this
# to the shared parent domain (".example.com") so the cookie is visible there.
CSRF_COOKIE_DOMAIN = env("CSRF_COOKIE_DOMAIN", default=None)

ADMIN_URL = env("ADMIN_URL", default="admin/")  # production: something unguessable

# The public dashboard/marketing site (frontend). Widget "Powered by" + privacy links, emails.
SITE_URL = env("SITE_URL", default="http://localhost:3000").rstrip("/")
PRODUCT_NAME = env("PRODUCT_NAME", default="TumarAgent")
EMAIL_LOGO_URL = env("EMAIL_LOGO_URL", default="")  # public PNG for the email header (app_logo/lockup_black.png); empty → text wordmark

# Outgoing email (accounts.tasks.send_email): Gmail SMTP with an app password (Google account → Security → 2-Step
# Verification → App passwords). No password set → emails print to the backend/celery log instead.
EMAIL_HOST = env("EMAIL_HOST", default="smtp.gmail.com")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_USE_TLS = True
EMAIL_TIMEOUT = 20
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend" if EMAIL_HOST_PASSWORD else "django.core.mail.backends.console.EmailBackend"
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default=f"{PRODUCT_NAME} <{EMAIL_HOST_USER or 'noreply@localhost'}>")
PASSWORD_RESET_TIMEOUT = 60 * 60  # reset links last an hour

# HTTPS hardening; TLS terminates at the reverse proxy, which must set X-Forwarded-Proto
if not DEBUG:
	SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
	SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)
	SECURE_REDIRECT_EXEMPT = [r"^health/$"]  # container health checks speak plain http
	SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=60 * 60 * 24 * 30)
	SESSION_COOKIE_SECURE = True
	CSRF_COOKIE_SECURE = True

# Login / signup / password reset attempts per IP (accounts.views)
AUTH_THROTTLE_RATE = env("AUTH_THROTTLE_RATE", default="10/min")

# Logging Configuration
LOGGING = {
	"version": 1,
	"disable_existing_loggers": False,
	"formatters": {
		"verbose": {
			"format": "[{asctime}] [{levelname}] {name} {filename}:{lineno} {message}",
			"style": "{",
			"datefmt": "%Y-%m-%d %H:%M:%S",
		},
		"simple": {
			"format": "[{asctime}] [{levelname}] {filename}:{lineno} {message}",
			"style": "{",
			"datefmt": "%Y-%m-%d %H:%M:%S",
		},
	},
	"handlers": {
		"console": {
			"class": "logging.StreamHandler",
			"formatter": "verbose",
		},
	},
	"root": {
		"handlers": ["console"],
		"level": "INFO",
	},
	"loggers": {
		"django": {
			"handlers": ["console"],
			"level": "INFO",
			"propagate": False,
		},
		"django.request": {
			"handlers": ["console"],
			"level": "INFO",
			"propagate": False,
		},
		"core": {
			"handlers": ["console"],
			"level": "INFO",
			"propagate": False,
		},
		"accounts": {
			"handlers": ["console"],
			"level": "INFO",
			"propagate": True,
		},
		"accounts.views": {
			"handlers": ["console"],
			"level": "INFO",
			"propagate": False,
		},
	},
}



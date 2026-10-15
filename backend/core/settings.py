import os
from pathlib import Path
import environ


BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
	DEBUG=(bool, True),
	SECRET_KEY=(str, "dev-secret-key"),
	ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1", "*"]),
)
environ.Env.read_env(os.path.join(BASE_DIR.parent, ".env"))  # ../.env

DEBUG = env("DEBUG")
SECRET_KEY = env("SECRET_KEY")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")

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
	"subscriptions",
	"usage",
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

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Celery / Redis
REDIS_URL = env("REDIS_URL", default="redis://localhost:6379/0")
# Shared across processes: the per-conversation reply lock (channels_app.reply) needs it
CACHES = {"default": {"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": REDIS_URL}}
CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
# Customer replies get their own queue/worker so document ingestion can't delay them (docker-compose: celery-replies)
CELERY_TASK_ROUTES = {
    "channels_app.reply.process_reply": {"queue": "replies"},
    "channels_app.reply.redeliver": {"queue": "replies"},
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
    'recover-stuck-replies': {
        'task': 'channels_app.reply.recover_stuck_replies',
        'schedule': 300,
    },
    'propose-instructions': {
        'task': 'channels_app.memory.propose_instructions',
        'schedule': 86400,  # daily
    },
}

# Qdrant
QDRANT_URL = env("QDRANT_URL", default="http://localhost:6333")
QDRANT_API_KEY = env("QDRANT_API_KEY", default=None)  # Optional: for production authentication
# Cross-encoder score below which a retrieved chunk counts as unrelated. ms-marco-MiniLM-L-6-v2 logits:
# relevant chunks ~ -5..+3, unrelated ~ -10..-11. Re-tune if RERANKER_MODEL changes.
RERANK_MIN_SCORE = env.float("RERANK_MIN_SCORE", default=-7.0)

# Meta / Facebook
META_APP_ID = env("META_APP_ID", default="")
META_APP_SECRET = env("META_APP_SECRET", default="")
FB_VERIFY_TOKEN = env("FB_VERIFY_TOKEN", default="verify-token")

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
HANDOFF_AUTO_RESUME_HOURS = env.int("HANDOFF_AUTO_RESUME_HOURS", default=24)
HANDOFF_MESSAGE = env("HANDOFF_MESSAGE", default="I've passed this to our team — a person will reply here soon.")
MEMORY_LLM_MODEL = env("MEMORY_LLM_MODEL", default=None)  # background memory work; None → LLM_MODEL
EPISODE_GAP_HOURS = env.int("EPISODE_GAP_HOURS", default=12)  # silence that closes a conversation episode
REPLY_DEBOUNCE_SECONDS = {  # channels not listed: 0
	"facebook": env.int("FACEBOOK_REPLY_DEBOUNCE_SECONDS", default=3),
	"instagram": env.int("INSTAGRAM_REPLY_DEBOUNCE_SECONDS", default=3),
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
}

# CORS Settings
CORS_ALLOWED_ORIGINS = [
	"http://localhost:3000",
	"http://localhost:3002",
	"http://127.0.0.1:3000",
	"http://127.0.0.1:3002",
]

CORS_ALLOW_CREDENTIALS = True

# CSRF Settings for API
CSRF_TRUSTED_ORIGINS = [
	"http://localhost:3000",
	"http://127.0.0.1:3000",
]

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



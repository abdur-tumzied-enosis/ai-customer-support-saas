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
	# "core.middleware.RequestLoggingMiddleware",
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
CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL

# Qdrant
QDRANT_URL = env("QDRANT_URL", default="http://localhost:6333")

# Meta / Facebook
META_APP_ID = env("META_APP_ID", default="")
META_APP_SECRET = env("META_APP_SECRET", default="")
FB_VERIFY_TOKEN = env("FB_VERIFY_TOKEN", default="verify-token")

# Google / Gemini
GOOGLE_API_KEY = env("GOOGLE_API_KEY", default="")

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
	"http://127.0.0.1:3000",
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
			"format": "[{asctime}] [{levelname}] {name} {message}",
			"style": "{",
			"datefmt": "%Y-%m-%d %H:%M:%S",
		},
		"simple": {
			"format": "[{asctime}] [{levelname}] {message}",
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
	},
}



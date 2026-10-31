from django.conf import settings
from django.contrib import admin
from django.http import HttpResponse
from django.urls import path, include

urlpatterns = [
	path("health/", lambda request: HttpResponse("ok")),  # liveness only: no DB/Redis checks
	path(settings.ADMIN_URL, admin.site.urls),
	path("api/auth/", include("accounts.urls")),
	path("webhooks/facebook/", include("channels_app.urls_facebook")),
	path("api/kb/", include("knowledge.urls")),
	path("api/channels/", include("channels_app.urls_api")),
	path("api/analytics/", include("analytics.urls")),
	path("", include("channels_app.urls_widget")),  # public widget endpoints
]

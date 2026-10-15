from django.contrib import admin
from django.urls import path, include

urlpatterns = [
	path("admin/", admin.site.urls),
	path("api/auth/", include("accounts.urls")),
	path("webhooks/facebook/", include("channels_app.urls_facebook")),
	path("api/kb/", include("knowledge.urls")),
	path("api/channels/", include("channels_app.urls_api")),
	path("api/analytics/", include("analytics.urls")),
]



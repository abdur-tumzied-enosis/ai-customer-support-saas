from django.contrib import admin
from django.urls import path, include

from channels_app.views_facebook import facebook_webhook

urlpatterns = [
	path("admin/", admin.site.urls),
	path("api/auth/", include("accounts.urls")),
	path("webhooks/facebook/", include("channels_app.urls_facebook")),
	path("webhooks/instagram/", facebook_webhook, name="instagram-webhook"),  # same handler; Platform.webhook_path
	path("api/kb/", include("knowledge.urls")),
	path("api/channels/", include("channels_app.urls_api")),
	path("api/analytics/", include("analytics.urls")),
]



from django.conf import settings
from django.contrib import admin
from django.contrib.staticfiles.urls import staticfiles_urlpatterns
from django.http import HttpResponse
from django.urls import path, include

from accounts.urls import project_urlpatterns

urlpatterns = [
	path("health/", lambda request: HttpResponse("ok")),  # liveness only: no DB/Redis checks
	path(settings.ADMIN_URL, admin.site.urls),
	path("api/auth/", include("accounts.urls")),
	path("api/projects/", include(project_urlpatterns)),
	path("webhooks/meta/", include("channels_app.urls_facebook")),
	path("api/kb/", include("knowledge.urls")),
	path("api/orders/", include("orders.urls")),
	path("api/billing/", include("billing.urls")),
	path("api/channels/", include("channels_app.urls_api")),
	path("api/analytics/", include("analytics.urls")),
	path("", include("channels_app.urls_widget")),  # public widget endpoints
]
# DEBUG only (no-op otherwise): gunicorn serves no static files locally; prod nginx serves /static/ itself
urlpatterns += staticfiles_urlpatterns()

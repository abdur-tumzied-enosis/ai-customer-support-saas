from django.urls import path
from .views_facebook import facebook_webhook

urlpatterns = [
	path("", facebook_webhook, name="facebook-webhook"),
]



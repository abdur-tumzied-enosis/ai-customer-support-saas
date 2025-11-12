from django.urls import path
from .api import FacebookConfigView, ChannelCredentialsListView

urlpatterns = [
	path("facebook/config", FacebookConfigView.as_view(), name="facebook-config"),
	path("credentials", ChannelCredentialsListView.as_view(), name="channel-credentials-list"),
]



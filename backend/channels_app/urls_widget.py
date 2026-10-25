from django.urls import path
from . import widget

urlpatterns = [
	path("widget/<str:name>", widget.asset, name="widget-asset"),
	path("embed/<str:key>/", widget.embed, name="widget-embed"),
	path("api/widget/<str:key>/session", widget.WidgetSessionView.as_view(), name="widget-session"),
	path("api/widget/messages", widget.WidgetMessagesView.as_view(), name="widget-messages"),
]

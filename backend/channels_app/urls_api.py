from django.urls import path
from .api import (
	FacebookConfigView,
	ChannelCredentialsListView,
	ConversationListView,
	ConversationDetailView,
	ConversationMessageListView,
	MessageStatusView,
)

urlpatterns = [
	path("facebook/config", FacebookConfigView.as_view(), name="facebook-config"),
	path("credentials", ChannelCredentialsListView.as_view(), name="channel-credentials-list"),
	path("conversations", ConversationListView.as_view(), name="conversation-list"),
	path("conversations/<uuid:conversation_id>", ConversationDetailView.as_view(), name="conversation-detail"),
	path("conversations/<uuid:conversation_id>/messages", ConversationMessageListView.as_view(), name="conversation-messages"),
	path("messages/<uuid:message_id>/status", MessageStatusView.as_view(), name="message-status"),
]



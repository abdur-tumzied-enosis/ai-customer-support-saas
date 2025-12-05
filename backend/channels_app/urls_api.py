from django.urls import path
from .api import (
	PlatformListView,
	FacebookConfigView,
	ChannelCredentialsListView,
	ChannelListCreateView,
	ChannelDetailView,
	ConversationListView,
	ConversationDetailView,
	ConversationMessageListView,
	MessageStatusView,
)

urlpatterns = [
	# Platform endpoints
	path("platforms", PlatformListView.as_view(), name="platform-list"),
	
	# Channel endpoints (new CRUD)
	path("channels", ChannelListCreateView.as_view(), name="channel-list-create"),
	path("channels/<uuid:channel_id>", ChannelDetailView.as_view(), name="channel-detail"),
	
	# Legacy endpoints
	path("facebook/config", FacebookConfigView.as_view(), name="facebook-config"),
	path("credentials", ChannelCredentialsListView.as_view(), name="channel-credentials-list"),
	
	# Conversation endpoints
	path("conversations", ConversationListView.as_view(), name="conversation-list"),
	path("conversations/<uuid:conversation_id>", ConversationDetailView.as_view(), name="conversation-detail"),
	path("conversations/<uuid:conversation_id>/messages", ConversationMessageListView.as_view(), name="conversation-messages"),
	path("messages/<uuid:message_id>/status", MessageStatusView.as_view(), name="message-status"),
]



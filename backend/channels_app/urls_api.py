from django.urls import path
from .widget import WidgetDetailView, WidgetListCreateView
from .api import (
	PlatformListView,
	FacebookConfigView,
	ChannelCredentialsListView,
	ChannelListCreateView,
	ChannelDetailView,
	ConversationListView,
	ConversationDetailView,
	ConversationMessageListView,
	ConversationPhotoView, ConversationPhotoPresignView,
	MessageStatusView,
	MessagePhotoView,
	ConversationHandoffView,
	ConversationResumeView,
	ConversationStaffReplyView,
	ConversationMemoryView,
	ProjectWebhookView,
	ProjectWebhookRotateView,
	ProjectWebhookTestView,
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
	path("conversations/<uuid:conversation_id>/photo", ConversationPhotoView.as_view(), name="conversation-photo"),
	path("conversations/<uuid:conversation_id>/photo/presign", ConversationPhotoPresignView.as_view(), name="conversation-photo-presign"),
	path("messages/<uuid:message_id>/status", MessageStatusView.as_view(), name="message-status"),
	path("messages/<uuid:message_id>/photo", MessagePhotoView.as_view(), name="message-photo"),
	path("conversations/<uuid:conversation_id>/handoff", ConversationHandoffView.as_view(), name="conversation-handoff"),
	path("conversations/<uuid:conversation_id>/resume", ConversationResumeView.as_view(), name="conversation-resume"),
	path("conversations/<uuid:conversation_id>/staff-reply", ConversationStaffReplyView.as_view(), name="conversation-staff-reply"),
	path("conversations/<uuid:conversation_id>/memory", ConversationMemoryView.as_view(), name="conversation-memory"),
	path("webhook", ProjectWebhookView.as_view(), name="project-webhook"),
	path("webhook/rotate-secret", ProjectWebhookRotateView.as_view(), name="project-webhook-rotate"),
	path("webhook/test", ProjectWebhookTestView.as_view(), name="project-webhook-test"),
	path("widgets", WidgetListCreateView.as_view(), name="widget-list-create"),
	path("widgets/<uuid:widget_id>", WidgetDetailView.as_view(), name="widget-detail"),
]



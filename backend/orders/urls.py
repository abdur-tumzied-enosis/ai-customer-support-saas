from django.urls import path

from .api import (
	NotificationsView, ActionDetailView, ActionListView, ActionProgressView, ActionRunListView, ActionRunRetryView, ActionRunView, ActionTestView,
	BulkStatusView, DisableView, EnableView, FromChatView, InstructionResolveView, InstructionReviewView, OrderDetailView,
	OrdersExportView, OrdersListView, RequirementsView, SetupRunView, SetupStatusView, SettingsView, TestModeView,
)

urlpatterns = [
	path("", OrdersListView.as_view(), name="order-list"),
	path("export.csv", OrdersExportView.as_view(), name="order-export"),
	path("<uuid:order_id>/", OrderDetailView.as_view(), name="order-detail"),
	path("bulk-status/", BulkStatusView.as_view(), name="order-bulk-status"),
	path("from-chat/", FromChatView.as_view(), name="order-from-chat"),
	path("settings/", SettingsView.as_view(), name="order-settings"),
	path("requirements/", RequirementsView.as_view(), name="order-requirements"),
	path("enable/", EnableView.as_view(), name="order-enable"),
	path("disable/", DisableView.as_view(), name="order-disable"),
	path("test-mode/", TestModeView.as_view(), name="order-test-mode"),
	path("setup/run/", SetupRunView.as_view(), name="order-setup-run"),
	path("setup/", SetupStatusView.as_view(), name="order-setup-status"),
	path("instructions/review/", InstructionReviewView.as_view(), name="order-instructions-review"),
	path("instructions/resolve/", InstructionResolveView.as_view(), name="order-instructions-resolve"),
	path("notifications/", NotificationsView.as_view(), name="order-notifications"),
	path("actions/", ActionListView.as_view(), name="order-actions"),
	path("actions/<int:action_id>/", ActionDetailView.as_view(), name="order-action-detail"),
	path("actions/<int:action_id>/run/", ActionRunView.as_view(), name="order-action-run"),
	path("actions/<int:action_id>/progress/", ActionProgressView.as_view(), name="order-action-progress"),
	path("actions/<int:action_id>/test/", ActionTestView.as_view(), name="order-action-test"),
	path("actions/runs/", ActionRunListView.as_view(), name="order-action-runs"),
	path("actions/runs/<int:run_id>/retry/", ActionRunRetryView.as_view(), name="order-action-run-retry"),
]

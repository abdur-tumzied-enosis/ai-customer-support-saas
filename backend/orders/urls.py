from django.urls import path

from .api import DisableView, EnableView, RequirementsView, SetupRunView, SetupStatusView, SettingsView, TestModeView

urlpatterns = [
	path("settings/", SettingsView.as_view(), name="order-settings"),
	path("requirements/", RequirementsView.as_view(), name="order-requirements"),
	path("enable/", EnableView.as_view(), name="order-enable"),
	path("disable/", DisableView.as_view(), name="order-disable"),
	path("test-mode/", TestModeView.as_view(), name="order-test-mode"),
	path("setup/run/", SetupRunView.as_view(), name="order-setup-run"),
	path("setup/", SetupStatusView.as_view(), name="order-setup-status"),
]

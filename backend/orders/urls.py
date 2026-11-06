from django.urls import path

from .api import DisableView, EnableView, RequirementsView, SettingsView, TestModeView

urlpatterns = [
	path("settings/", SettingsView.as_view(), name="order-settings"),
	path("requirements/", RequirementsView.as_view(), name="order-requirements"),
	path("enable/", EnableView.as_view(), name="order-enable"),
	path("disable/", DisableView.as_view(), name="order-disable"),
	path("test-mode/", TestModeView.as_view(), name="order-test-mode"),
]

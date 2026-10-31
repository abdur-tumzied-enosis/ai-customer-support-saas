from django.urls import path
from .views import (
	SignUpView, LoginView, LogoutView, CurrentUserView, CsrfTokenView,
	ProfileView, ChangePasswordView, ExportDataView, DeleteAccountView,
)

urlpatterns = [
	path("signup/", SignUpView.as_view(), name="signup"),
	path("login/", LoginView.as_view(), name="login"),
	path("logout/", LogoutView.as_view(), name="logout"),
	path("me/", CurrentUserView.as_view(), name="current-user"),
	path("csrf/", CsrfTokenView.as_view(), name="csrf-token"),
	path("profile/", ProfileView.as_view(), name="profile"),
	path("password/", ChangePasswordView.as_view(), name="change-password"),
	path("export/", ExportDataView.as_view(), name="export-data"),
	path("delete/", DeleteAccountView.as_view(), name="delete-account"),
]


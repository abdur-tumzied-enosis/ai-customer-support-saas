from django.urls import path
from .views import SignUpView, LoginView, LogoutView, CurrentUserView, CsrfTokenView

urlpatterns = [
	path("signup/", SignUpView.as_view(), name="signup"),
	path("login/", LoginView.as_view(), name="login"),
	path("logout/", LogoutView.as_view(), name="logout"),
	path("me/", CurrentUserView.as_view(), name="current-user"),
	path("csrf/", CsrfTokenView.as_view(), name="csrf-token"),
]


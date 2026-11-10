from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.exceptions import ValidationError
from rest_framework.throttling import SimpleRateThrottle
from django.conf import settings
from django.contrib.auth import get_user_model, login, logout, update_session_auth_hash
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import default_token_generator
from django.core import serializers
from django.core.cache import cache
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.files.storage import default_storage
from django.core.serializers.json import DjangoJSONEncoder
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.utils.decorators import method_decorator
from .serializers import SignUpSerializer, LoginSerializer, UserSerializer, ProjectSettingsSerializer
from .models import Project, ProjectUser
from .tasks import queue_email
from billing.service import grant_signup_bonus
import hmac
import logging
import secrets

logger = logging.getLogger(__name__)
User = get_user_model()


class AuthThrottle(SimpleRateThrottle):
	"""Per-IP cap on credential endpoints (brute force, signup spam), signed in or not."""

	scope = "auth"

	def get_rate(self):
		return settings.AUTH_THROTTLE_RATE

	def get_cache_key(self, request, view):
		return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}


def _signed_in(request, user, message: str) -> Response:
	login(request, user)
	projects = ProjectUser.objects.filter(user=user).select_related("project")
	projects_data = [
		{
			"id": pu.project.id,
			"name": pu.project.name,
			"slug": pu.project.slug,
			"role": pu.role,
		}
		for pu in projects
	]
	return Response(
		{
			"success": True,
			"message": message,
			"data": {
				"user": UserSerializer(user).data,
				"projects": projects_data,
			},
		},
		status=status.HTTP_200_OK,
	)


# --- Email verification (6-digit code) and password reset (signed link) ---------------------------------------

OTP_TTL_SECONDS = 10 * 60
OTP_MAX_ATTEMPTS = 5  # then the code is dropped and a new one must be requested
OTP_RESEND_COOLDOWN_SECONDS = 60


def send_verification_code(user) -> None:
	code = f"{secrets.randbelow(10**6):06d}"
	cache.set(f"email-otp:{user.pk}", {"code": code, "attempts": 0}, OTP_TTL_SECONDS)
	queue_email(
		user.email,
		f"{code} is your {settings.PRODUCT_NAME} verification code",
		f"Hi {user.first_name},\n\nYour verification code is {code}. It expires in 10 minutes.\n\n"
		"If you didn't create an account, you can ignore this email.",
	)


def check_verification_code(user, code: str) -> bool:
	# ponytail: attempt count is read-modify-write; parallel guesses can slip a few extra past it, the IP throttle caps that
	key = f"email-otp:{user.pk}"
	entry = cache.get(key)
	if not entry:
		return False
	if hmac.compare_digest(entry["code"], code):
		cache.delete(key)
		return True
	entry["attempts"] += 1
	if entry["attempts"] >= OTP_MAX_ATTEMPTS:
		cache.delete(key)
	else:
		cache.set(key, entry, OTP_TTL_SECONDS)
	return False


def _unverified_user(email: str):
	return User.objects.filter(email__iexact=(email or "").strip(), email_verified=False, is_active=True).first()


@method_decorator(ensure_csrf_cookie, name="dispatch")
class VerifyEmailView(APIView):
	permission_classes = [AllowAny]
	throttle_classes = [AuthThrottle]

	def post(self, request):
		user = _unverified_user(request.data.get("email"))
		if not user or not check_verification_code(user, str(request.data.get("code") or "").strip()):
			raise ValidationError({"code": "Invalid or expired code"})
		user.email_verified = True
		user.save(update_fields=["email_verified"])
		grant_signup_bonus(user)
		return _signed_in(request, user, "Email verified")


class ResendVerificationView(APIView):
	permission_classes = [AllowAny]
	throttle_classes = [AuthThrottle]

	def post(self, request):
		user = _unverified_user(request.data.get("email"))
		if user and cache.add(f"email-otp-sent:{user.pk}", 1, OTP_RESEND_COOLDOWN_SECONDS):
			send_verification_code(user)
		# Same answer either way: no account enumeration
		return Response({"success": True, "message": "If that account needs verifying, a new code is on its way."})


class PasswordResetRequestView(APIView):
	permission_classes = [AllowAny]
	throttle_classes = [AuthThrottle]

	def post(self, request):
		email = (request.data.get("email") or "").strip()
		user = User.objects.filter(email__iexact=email, is_active=True).first() if email else None
		if user:
			uid = urlsafe_base64_encode(force_bytes(user.pk))
			token = default_token_generator.make_token(user)
			link = f"{settings.SITE_URL}/reset-password?uid={uid}&token={token}"
			queue_email(
				user.email,
				f"Reset your {settings.PRODUCT_NAME} password",
				f"Hi {user.first_name},\n\nReset your password here (valid for 1 hour):\n{link}\n\n"
				"If you didn't ask for this, you can ignore this email; your password stays the same.",
			)
		return Response({"success": True, "message": "If an account with that email exists, we've sent a reset link."})


class PasswordResetConfirmView(APIView):
	permission_classes = [AllowAny]
	throttle_classes = [AuthThrottle]

	def post(self, request):
		try:
			user = User.objects.get(pk=force_str(urlsafe_base64_decode(request.data.get("uid") or "")))
		except (User.DoesNotExist, ValueError, DjangoValidationError):
			user = None
		# The token is tied to the password hash, so it stops working once used
		if not user or not default_token_generator.check_token(user, request.data.get("token") or ""):
			raise ValidationError({"token": "This reset link is invalid or has expired"})
		new_password = request.data.get("new_password") or ""
		try:
			validate_password(new_password, user)
		except DjangoValidationError as e:
			raise ValidationError({"new_password": e.messages})
		user.set_password(new_password)
		flipped = not user.email_verified
		user.email_verified = True  # the link reached their inbox
		user.save(update_fields=["password", "email_verified"])
		if flipped:
			grant_signup_bonus(user)
		return Response({"success": True, "message": "Password reset. You can sign in now."})


@method_decorator(ensure_csrf_cookie, name="dispatch")
class SignUpView(APIView):
	permission_classes = [AllowAny]
	throttle_classes = [AuthThrottle]

	def post(self, request):
		serializer = SignUpSerializer(data=request.data)
		if serializer.is_valid():
			result = serializer.save()
			user = result.get("user")
			if not user:
				raise ValidationError("Failed to create user")

			# Signed in only once the emailed code is entered (VerifyEmailView)
			send_verification_code(user)

			response_data = {
				"success": True,
				"message": "User created successfully",
				"data": {
					"user": UserSerializer(user).data,
					"verification_required": True,
				},
			}

			return Response(response_data, status=status.HTTP_201_CREATED)
		
		# Raise ValidationError to use custom exception handler
		raise ValidationError(serializer.errors)


@method_decorator(ensure_csrf_cookie, name="dispatch")
class LoginView(APIView):
	permission_classes = [AllowAny]
	throttle_classes = [AuthThrottle]

	def post(self, request):
		serializer = LoginSerializer(data=request.data)
		if serializer.is_valid():
			user = serializer.validated_data["user"]
			if not user.email_verified:
				send_verification_code(user)  # password proven, so a fresh code is safe to send
				return Response(
					{
						"success": False,
						"error": {
							"code": "email_unverified",
							"message": "Verify your email: we sent you a new code.",
							"details": None,
						},
					},
					status=status.HTTP_403_FORBIDDEN,
				)
			return _signed_in(request, user, "Login successful")
		
		# Raise ValidationError to use custom exception handler
		raise ValidationError(serializer.errors)


@method_decorator(ensure_csrf_cookie, name="dispatch")
class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from django.contrib.auth import logout

        try:
            logout(request)
        except Exception as e:
            pass

        return Response(
            {
                "success": True,
                "message": "Logout successful",
            },
            status=status.HTTP_200_OK,
        )


@method_decorator(ensure_csrf_cookie, name="dispatch")
class CurrentUserView(APIView):
	permission_classes = [IsAuthenticated]

	def get(self, request):
		# Get user's projects
		projects = ProjectUser.objects.filter(user=request.user).select_related("project")
		projects_data = [
			{
				"id": pu.project.id,
				"name": pu.project.name,
				"slug": pu.project.slug,
				"role": pu.role,
			}
			for pu in projects
		]

		return Response(
			{
				"success": True,
				"data": {
					"user": UserSerializer(request.user).data,
					"projects": projects_data,
				},
			},
			status=status.HTTP_200_OK,
		)


@method_decorator(ensure_csrf_cookie, name="dispatch")
class CsrfTokenView(APIView):
	"""
	Simple endpoint to ensure CSRF cookie is set.
	This can be called on page load to ensure CSRF token is available.
	"""
	permission_classes = [AllowAny]

	def get(self, request):
		return Response(
			{
				"success": True,
				"message": "CSRF cookie set",
			},
			status=status.HTTP_200_OK,
		)



# --- Account settings: profile, password, export, delete (GDPR access + erasure) ------------------------------

class ProfileView(APIView):
	permission_classes = [IsAuthenticated]

	def patch(self, request):
		user = request.user
		for field in ("first_name", "last_name"):
			if field in request.data:
				value = str(request.data[field] or "").strip()
				if not 1 <= len(value) <= 150:
					raise ValidationError({field: "Must be 1-150 characters"})
				setattr(user, field, value)
		user.save(update_fields=["first_name", "last_name"])
		return Response({"success": True, "data": {"user": UserSerializer(user).data}})


class ChangePasswordView(APIView):
	permission_classes = [IsAuthenticated]
	throttle_classes = [AuthThrottle]

	def post(self, request):
		user = request.user
		if not user.check_password(request.data.get("current_password") or ""):
			raise ValidationError({"current_password": "Incorrect password"})
		new_password = request.data.get("new_password") or ""
		try:
			validate_password(new_password, user)
		except DjangoValidationError as e:
			raise ValidationError({"new_password": e.messages})
		user.set_password(new_password)
		user.save(update_fields=["password"])
		update_session_auth_hash(request, user)  # this session stays signed in; every other session is signed out
		return Response({"success": True, "message": "Password changed"})


def _project_data(project) -> dict:
	"""What a member gets in an export. Channel credentials and webhook secrets stay out: secrets, not personal data."""
	from channels_app.models import Conversation, CustomerContact, Message, Widget
	from knowledge.models import CustomInstruction, Document, KnowledgeBase

	return {
		"knowledge_bases": KnowledgeBase.objects.filter(project=project),
		"documents": Document.objects.filter(kb__project=project),
		"instructions": CustomInstruction.objects.filter(project=project),
		"widgets": Widget.objects.filter(project=project),
		"contacts": CustomerContact.objects.filter(project=project),
		"conversations": Conversation.objects.filter(project=project),
		"messages": Message.objects.filter(project=project).order_by("created_at"),
	}


class ExportDataView(APIView):
	permission_classes = [IsAuthenticated]
	throttle_classes = [AuthThrottle]

	def get(self, request):
		# ponytail: built in-request; move to a Celery job + emailed link when a project's messages reach ~100k
		user = request.user
		data = {
			"exported_at": timezone.now(),
			"user": {
				"id": user.id, "email": user.email, "first_name": user.first_name, "last_name": user.last_name,
				"date_joined": user.date_joined, "last_login": user.last_login,
				"terms_version": user.terms_version, "terms_accepted_at": user.terms_accepted_at,
			},
			"projects": [
				{
					"id": m.project.id, "name": m.project.name, "role": m.role, "created_at": m.project.created_at,
					**{name: serializers.serialize("python", qs) for name, qs in _project_data(m.project).items()},
				}
				for m in ProjectUser.objects.filter(user=user).select_related("project")
			],
		}
		response = JsonResponse(data, encoder=DjangoJSONEncoder, json_dumps_params={"indent": 2})
		response["Content-Disposition"] = f'attachment; filename="account-export-{timezone.now():%Y-%m-%d}.json"'
		return response


def purge_project(project) -> None:
	"""Delete a project plus what DB cascades miss: Qdrant collections and stored KB files. The outside cleanup runs
	first, so a Qdrant outage fails the request before any row is gone, and a retry finishes the job."""
	from agents.vector_store import delete_collection
	from core.storage import delete_prefix
	from knowledge.models import Document, ProductImage

	for kb in project.knowledge_bases.all():
		delete_collection(kb.vector_collection)
		delete_collection(kb.vector_collection + "_web")
		delete_collection(kb.image_collection)
	for doc in Document.objects.filter(kb__project=project).exclude(file__isnull=True).exclude(file=""):
		default_storage.delete(doc.file.name)
	for img in ProductImage.objects.filter(product__kb__project=project):
		img._delete_file()  # display + thumb
	delete_prefix(f"chat_media/{project.slug}/")  # customers' photos and voice notes
	# PROTECT FKs onto KnowledgeBase block the cascade even though these rows would go with the project
	project.widgets.all().delete()
	project.conversations.all().delete()
	project.delete()


class DeleteAccountView(APIView):
	permission_classes = [IsAuthenticated]
	throttle_classes = [AuthThrottle]

	def post(self, request):
		user = request.user
		if not user.check_password(request.data.get("password") or ""):
			raise ValidationError({"password": "Incorrect password"})
		for membership in ProjectUser.objects.filter(user=user).select_related("project"):
			if not membership.project.members.exclude(user=user).exists():  # shared projects stay with the team
				purge_project(membership.project)
		user_id = user.pk
		logout(request)
		user.delete()
		logger.info("Account deleted: %s", user_id)
		return Response({"success": True, "message": "Account deleted"})


def _project_settings_dict(project: Project) -> dict:
	return {"project_id": str(project.id), "name": project.name, "currency": project.currency}


class ProjectSettingsView(APIView):
	permission_classes = [IsAuthenticated]

	def _membership(self, request, project_id, admin: bool):
		try:
			qs = ProjectUser.objects.filter(project_id=project_id, user=request.user)
			if admin:
				qs = qs.filter(role="admin")
			return qs.select_related("project").first()
		except (DjangoValidationError, ValueError):
			return None

	def _denied(self):
		return Response(
			{"success": False, "error": {"code": "access_denied", "message": "No access to this project"}},
			status=status.HTTP_403_FORBIDDEN,
		)

	def get(self, request):
		pu = self._membership(request, request.query_params.get("project_id"), admin=False)
		if not pu:
			return self._denied()
		return Response({"success": True, "data": _project_settings_dict(pu.project)})

	def patch(self, request):
		pu = self._membership(request, request.data.get("project_id"), admin=True)
		if not pu:
			return self._denied()
		serializer = ProjectSettingsSerializer(data=request.data)
		if not serializer.is_valid():
			code = "invalid_currency" if "currency" in serializer.errors else "invalid_value"
			message = "Currency must be a three-letter uppercase code" if code == "invalid_currency" else "Invalid name"
			return Response({"success": False, "error": {"code": code, "message": message}}, status=status.HTTP_400_BAD_REQUEST)
		project = pu.project
		fields = []
		for key, value in serializer.validated_data.items():
			setattr(project, key, value.strip() if key == "name" else value)
			fields.append(key)
		if fields:
			project.save(update_fields=fields)
		return Response({"success": True, "data": _project_settings_dict(project)})

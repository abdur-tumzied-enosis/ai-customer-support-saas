from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.exceptions import ValidationError
from rest_framework.throttling import SimpleRateThrottle
from django.conf import settings
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.password_validation import validate_password
from django.core import serializers
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.files.storage import default_storage
from django.core.serializers.json import DjangoJSONEncoder
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.utils.decorators import method_decorator
from .serializers import SignUpSerializer, LoginSerializer, UserSerializer
from .models import Project, ProjectUser
import logging

logger = logging.getLogger(__name__)


class AuthThrottle(SimpleRateThrottle):
	"""Per-IP cap on credential endpoints (brute force, signup spam), signed in or not."""

	scope = "auth"

	def get_rate(self):
		return settings.AUTH_THROTTLE_RATE

	def get_cache_key(self, request, view):
		return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}


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

			# Log the user in
			login(request, user)

			response_data = {
				"success": True,
				"message": "User created successfully",
				"data": {
					"user": UserSerializer(user).data,
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
			login(request, user)

			# Get user's projects
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
					"message": "Login successful",
					"data": {
						"user": UserSerializer(user).data,
						"projects": projects_data,
					},
				},
				status=status.HTTP_200_OK,
			)
		
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
	from knowledge.models import Document, ProductImage

	for kb in project.knowledge_bases.all():
		delete_collection(kb.vector_collection)
		delete_collection(kb.vector_collection + "_web")
		delete_collection(kb.image_collection)
	for doc in Document.objects.filter(kb__project=project).exclude(file__isnull=True).exclude(file=""):
		default_storage.delete(doc.file.name)
	for img in ProductImage.objects.filter(product__kb__project=project):
		default_storage.delete(img.file.name)
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

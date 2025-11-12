from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.exceptions import ValidationError
from django.contrib.auth import login
from django.views.decorators.csrf import ensure_csrf_cookie
from django.utils.decorators import method_decorator
from .serializers import SignUpSerializer, LoginSerializer, UserSerializer
from .models import Project, ProjectUser


@method_decorator(ensure_csrf_cookie, name="dispatch")
class SignUpView(APIView):
	permission_classes = [AllowAny]

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


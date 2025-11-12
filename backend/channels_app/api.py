from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import ValidationError
from accounts.models import Project, ProjectUser
from .models import ChannelCredential


class FacebookConfigView(APIView):
	permission_classes = [IsAuthenticated]

	def post(self, request):
		project_slug = request.data.get("project_slug")
		if not project_slug:
			raise ValidationError({"project_slug": "This field is required"})

		try:
			project = Project.objects.get(slug=project_slug)
		except Project.DoesNotExist:
			return Response(
				{
					"success": False,
					"error": {
						"code": "not_found",
						"message": "Project not found"
					}
				},
				status=status.HTTP_404_NOT_FOUND
			)

		# Verify user has access to this project
		try:
			ProjectUser.objects.get(project=project, user=request.user)
		except ProjectUser.DoesNotExist:
			return Response(
				{
					"success": False,
					"error": {
						"code": "access_denied",
						"message": "You do not have access to this project"
					}
				},
				status=status.HTTP_403_FORBIDDEN
			)

		cred, _ = ChannelCredential.objects.get_or_create(project=project, channel="facebook")
		cred.page_id = request.data.get("page_id", cred.page_id)
		cred.page_access_token = request.data.get("page_access_token", cred.page_access_token)
		cred.meta_app_id = request.data.get("meta_app_id", cred.meta_app_id)
		cred.meta_app_secret = request.data.get("meta_app_secret", cred.meta_app_secret)
		cred.verify_token = request.data.get("verify_token", cred.verify_token)
		cred.save()

		return Response({
			"success": True,
			"message": "Facebook channel configured successfully",
			"data": {
				"status": "saved",
				"project": project.slug
			}
		}, status=status.HTTP_200_OK)


class ChannelCredentialsListView(APIView):
	permission_classes = [IsAuthenticated]

	def get(self, request):
		project_slug = request.query_params.get("project_slug")
		if not project_slug:
			raise ValidationError({"project_slug": "This field is required"})

		try:
			project = Project.objects.get(slug=project_slug)
		except Project.DoesNotExist:
			return Response(
				{
					"success": False,
					"error": {
						"code": "not_found",
						"message": "Project not found"
					}
				},
				status=status.HTTP_404_NOT_FOUND
			)

		# Verify user has access to this project
		try:
			ProjectUser.objects.get(project=project, user=request.user)
		except ProjectUser.DoesNotExist:
			return Response(
				{
					"success": False,
					"error": {
						"code": "access_denied",
						"message": "You do not have access to this project"
					}
				},
				status=status.HTTP_403_FORBIDDEN
			)

		# Get all channel credentials for this project
		credentials = ChannelCredential.objects.filter(project=project)
		
		channels_data = []
		for cred in credentials:
			# Determine if channel is configured (has essential fields)
			is_configured = False
			if cred.channel == "facebook":
				is_configured = bool(cred.page_id and cred.page_access_token)
			
			channels_data.append({
				"id": str(cred.id),
				"channel": cred.channel,
				"is_configured": is_configured,
			})

		return Response({
			"success": True,
			"data": {
				"project": project.slug,
				"channels": channels_data
			}
		}, status=status.HTTP_200_OK)



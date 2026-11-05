from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.contrib.auth import authenticate
from .models import Project, ProjectUser
from django.utils import timezone
from django.utils.text import slugify
from uuid import uuid4
import logging

User = get_user_model()

logger = logging.getLogger(__name__)
class UserSerializer(serializers.ModelSerializer):
	class Meta:
		model = User
		fields = ["id", "first_name", "last_name", "email"]
		read_only_fields = fields


class SignUpSerializer(serializers.Serializer):
	first_name = serializers.CharField(max_length=150, required=True)
	last_name = serializers.CharField(max_length=150, required=True)
	# project_name = serializers.CharField(max_length=255, required=False, allow_blank=True)
	email = serializers.EmailField(required=True)
	password = serializers.CharField(write_only=True, min_length=8, required=True)
	# Sent only when the terms checkbox is ticked: the version of the terms the user agreed to
	terms_version = serializers.CharField(max_length=32, required=True)

	def validate_email(self, value):
		if User.objects.filter(email=value).exists():
			logger.info(f'A user with this email already exists.')
			raise serializers.ValidationError("A user with this email already exists.")
		return value

	def create(self, validated_data):
		project_name = validated_data.pop("project_name", None)
		password = validated_data.pop("password")
		first_name = validated_data.pop("first_name")
		last_name = validated_data.pop("last_name")
		email = validated_data.pop("email")

		# Create user
		username = email  # Use email as username
		user = User.objects.create_user(
			username=username,
			email=email,
			password=password,
			first_name=first_name,
			last_name=last_name,
			terms_version=validated_data.pop("terms_version"),
			terms_accepted_at=timezone.now(),
			email_verified=False,
		)

		# Always create a default project named "my_project"
		project_slug = uuid4().hex[:30]
		
		project = Project.objects.create(name="Default Project", slug=project_slug)
		# Create ProjectUser relationship
		ProjectUser.objects.create(project=project, user=user, role="admin")

		return {
			"user": user
		}


class LoginSerializer(serializers.Serializer):
	email = serializers.EmailField(required=True)
	password = serializers.CharField(write_only=True, required=True)

	def validate(self, attrs):
		email = attrs.get("email")
		password = attrs.get("password")

		if email and password:
			# Try to find user by email (since we use email as username)
			try:
				user = User.objects.get(email=email)
				user = authenticate(username=user.username, password=password)
			except User.DoesNotExist:
				user = None

			if not user:
				raise serializers.ValidationError("Invalid email or password.")

			if not user.is_active:
				raise serializers.ValidationError("User account is disabled.")

			attrs["user"] = user
		else:
			raise serializers.ValidationError("Must include 'email' and 'password'.")

		return attrs



class ProjectSettingsSerializer(serializers.Serializer):
	name = serializers.CharField(max_length=255, required=False)
	currency = serializers.RegexField(r"^[A-Z]{3}$", required=False)

import uuid
from django.db import models
from django.contrib.auth.models import AbstractUser


class User(AbstractUser):
    """
    Custom User model with UUID primary key
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        db_table = 'auth_user'


class Project(models.Model):
	id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
	name = models.CharField(max_length=255)
	slug = models.SlugField(unique=True)
	created_at = models.DateTimeField(auto_now_add=True)

	def __str__(self) -> str:
		return self.name


class ProjectUser(models.Model):
	id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
	project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="members")
	user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="project_memberships")
	role = models.CharField(max_length=64, default="admin")
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		unique_together = ("project", "user")



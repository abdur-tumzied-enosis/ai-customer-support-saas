import uuid
from django.conf import settings
from django.db import models
from django.contrib.auth.models import AbstractUser


class User(AbstractUser):
    """
    Custom User model with UUID primary key
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Consent record: which terms/privacy version (frontend lib/site.ts termsVersion) was accepted, and when
    terms_version = models.CharField(max_length=32, blank=True, default="")
    terms_accepted_at = models.DateTimeField(null=True, blank=True)
    # Signup sets False until the emailed code is entered; default True covers pre-existing and admin-made users
    email_verified = models.BooleanField(default=True)

    class Meta:
        db_table = 'auth_user'


class Project(models.Model):
	id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
	name = models.CharField(max_length=255)
	slug = models.SlugField(unique=True)
	created_at = models.DateTimeField(auto_now_add=True)
	# Product image search: the platform switch is settings.IMAGE_SEARCH_ENABLED; `allowed` is the operator's
	# per-tenant grant (admin only); `enabled` is the tenant's own switch.
	image_search_allowed = models.BooleanField(default=False)
	image_search_enabled = models.BooleanField(default=True)
	# ISO 4217 code shown with prices (symbols live in the frontend)
	currency = models.CharField(max_length=3, default="BDT")

	@property
	def image_search_available(self) -> bool:
		return bool(settings.IMAGE_SEARCH_ENABLED and self.image_search_allowed)

	@property
	def image_search_on(self) -> bool:
		return self.image_search_available and self.image_search_enabled

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



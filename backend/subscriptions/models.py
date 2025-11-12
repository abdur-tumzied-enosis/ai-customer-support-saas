import uuid
from django.db import models
from accounts.models import Project


class Plan(models.Model):
	id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
	name = models.CharField(max_length=128, unique=True)
	monthly_token_quota = models.PositiveBigIntegerField(default=100_000)
	display_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)

	def __str__(self) -> str:
		return self.name


class Subscription(models.Model):
	id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
	STATUS_CHOICES = [
		("active", "Active"),
		("past_due", "Past Due"),
		("canceled", "Canceled"),
	]
	project = models.OneToOneField(Project, on_delete=models.CASCADE, related_name="subscription")
	plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="subscriptions")
	status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="active")
	current_period_start = models.DateTimeField()
	current_period_end = models.DateTimeField()
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)



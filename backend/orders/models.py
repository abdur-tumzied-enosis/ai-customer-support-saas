import uuid
from decimal import Decimal

from django.db import models
from django.db.models import Q


class OrderSettings(models.Model):
	project = models.OneToOneField("accounts.Project", on_delete=models.CASCADE, related_name="order_settings")
	enabled = models.BooleanField(default=False)
	test_mode = models.BooleanField(default=False)
	channels = models.JSONField(default=list, blank=True)
	zones = models.JSONField(default=list, blank=True)  # [{"name", "charge"}], charge a decimal string or null
	payment_methods = models.JSONField(default=list, blank=True)  # [{"label", "default"}]
	form_fields = models.JSONField(default=dict, blank=True)  # {"optional": [...], "custom": [{"key", "label", "required"}]}
	notification_emails = models.JSONField(default=list, blank=True)
	draft_expiry_hours = models.PositiveIntegerField(default=48)
	setup_status = models.CharField(max_length=16, default="not_started")  # not_started / running / ready
	setup_report = models.JSONField(default=dict, blank=True)
	updated_at = models.DateTimeField(auto_now=True)


class OrderCounter(models.Model):
	project = models.OneToOneField("accounts.Project", on_delete=models.CASCADE, related_name="order_counter")
	last_number = models.PositiveIntegerField(default=1000)


class Order(models.Model):
	STATUS_CHOICES = [
		("draft", "Draft"), ("new", "New"), ("confirmed", "Confirmed"), ("shipped", "Shipped"),
		("delivered", "Delivered"), ("cancelled", "Cancelled"), ("abandoned", "Abandoned"),
	]

	id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
	project = models.ForeignKey("accounts.Project", on_delete=models.CASCADE, related_name="orders")
	conversation = models.ForeignKey(
		"channels_app.Conversation", on_delete=models.SET_NULL, null=True, blank=True, related_name="orders",
	)
	contact = models.ForeignKey(
		"channels_app.CustomerContact", on_delete=models.SET_NULL, null=True, blank=True, related_name="orders",
	)
	kb = models.ForeignKey("knowledge.KnowledgeBase", on_delete=models.SET_NULL, null=True, blank=True, related_name="orders")
	channel = models.CharField(max_length=16)
	number = models.PositiveIntegerField(null=True, blank=True)
	status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="draft")
	customer_name = models.CharField(max_length=255, blank=True, default="")
	customer_phone = models.CharField(max_length=32, blank=True, default="")
	customer_address = models.TextField(blank=True, default="")
	customer_note = models.TextField(blank=True, default="")
	zone_name = models.CharField(max_length=255, blank=True, default="")
	custom = models.JSONField(default=dict, blank=True)
	currency = models.CharField(max_length=3)  # snapshot of the project currency at draft time
	zone_charge = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
	subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
	total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
	advance_paid = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
	collect_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
	is_test = models.BooleanField(default=False)
	tracking_number = models.CharField(max_length=255, blank=True, default="")
	internal_note = models.TextField(blank=True, default="")
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)
	submitted_at = models.DateTimeField(null=True, blank=True)

	class Meta:
		constraints = [
			models.UniqueConstraint(
				fields=["project", "number"], condition=Q(number__isnull=False), name="order_number_per_project",
			),
			models.UniqueConstraint(
				fields=["conversation"], condition=Q(status="draft"), name="one_draft_per_conversation",
			),
		]


class OrderItem(models.Model):
	order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
	product = models.ForeignKey("knowledge.Product", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
	variant = models.ForeignKey("knowledge.ProductVariant", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
	product_external_id = models.CharField(max_length=255, blank=True, default="")
	sku = models.CharField(max_length=128, blank=True, default="")
	name = models.CharField(max_length=255)
	variant_label = models.CharField(max_length=255, blank=True, default="")
	unit_price = models.DecimalField(max_digits=12, decimal_places=2)
	quantity = models.PositiveSmallIntegerField()
	product_url = models.TextField(blank=True, default="")
	line_total = models.DecimalField(max_digits=12, decimal_places=2)


class OrderStatusHistory(models.Model):
	order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="history")
	actor_type = models.CharField(max_length=16)  # user / agent / system
	actor_id = models.CharField(max_length=64, blank=True, default="")
	from_status = models.CharField(max_length=16)
	to_status = models.CharField(max_length=16)
	created_at = models.DateTimeField(auto_now_add=True)

from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q

from accounts.models import Project

# {action: (customer label, default credits)}
ACTIONS = {
	"reply": ("AI reply", Decimal("1")),
	"photo_search": ("Photo search", Decimal("1")),
	"product_import": ("Catalog product", Decimal("2")),
	"photo_index": ("Product photo indexed", Decimal("0")),
	"order_setup": ("Order setup from your documents", Decimal("5")),
	"instruction_improve": ("Improve instructions", Decimal("0")),
	"product_suggest": ("Column mapping suggestion", Decimal("0")),
}


def _credits(**kw):
	return models.DecimalField(max_digits=12, decimal_places=2, **kw)


class ActionPrice(models.Model):
	action = models.CharField(max_length=40, unique=True, choices=[(k, v[0]) for k, v in ACTIONS.items()])
	credits = _credits()
	notes = models.TextField(blank=True)
	updated_at = models.DateTimeField(auto_now=True)

	def __str__(self):
		return f"{self.action}: {self.credits}"


class PricingSettings(models.Model):
	"""Singleton, same pattern as agents.ModelConfig."""
	usd_per_credit = models.DecimalField(max_digits=10, decimal_places=5, default=Decimal("0.005"))
	target_coverage_ratio = _credits(default=Decimal("5"))
	overdraft_credits = _credits(default=Decimal("50"))
	low_balance_credits = _credits(default=Decimal("100"))
	signup_credits = _credits(default=Decimal("100"))

	class Meta:
		verbose_name = verbose_name_plural = "Pricing settings"

	def __str__(self):
		return "Pricing settings"

	def save(self, *args, **kwargs):
		self.pk = 1
		kwargs.pop("force_insert", None)
		super().save(*args, **kwargs)

	@classmethod
	def get(cls):
		return cls.objects.get_or_create(pk=1)[0]


class CreditWallet(models.Model):
	project = models.OneToOneField(Project, null=True, on_delete=models.SET_NULL, related_name="wallet")
	project_name = models.CharField(max_length=255)
	balance = _credits(default=Decimal("0"))
	depleted_notified = models.BooleanField(default=False)
	updated_at = models.DateTimeField(auto_now=True)

	def __str__(self):
		return f"{self.project_name}: {self.balance}"


class CreditTransaction(models.Model):
	"""Append-only ledger: charges are negative, grants positive."""
	KIND_CHOICES = [
		("grant", "Grant"),
		("adjust", "Adjustment"),
		("charge", "Charge"),
		("initial_allocation", "Initial allocation"),
		("signup_bonus", "Signup bonus"),
	]
	wallet = models.ForeignKey(CreditWallet, on_delete=models.PROTECT, related_name="transactions")
	amount = _credits()
	balance_after = _credits()
	kind = models.CharField(max_length=20, choices=KIND_CHOICES)
	action = models.CharField(max_length=40, blank=True)
	idempotency_key = models.CharField(max_length=200, blank=True)
	conversation = models.ForeignKey("channels_app.Conversation", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
	trigger_message = models.ForeignKey("channels_app.Message", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
	result_message = models.ForeignKey("channels_app.Message", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
	created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
	note = models.TextField(blank=True)
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		constraints = [models.UniqueConstraint(fields=["idempotency_key"], condition=~Q(idempotency_key=""), name="uniq_credit_txn_key")]
		indexes = [models.Index(fields=["wallet", "created_at"]), models.Index(fields=["conversation"])]

	def save(self, *args, **kwargs):
		if not self._state.adding:
			raise ValueError("Credit transactions are immutable")
		super().save(*args, **kwargs)

	def delete(self, *args, **kwargs):
		raise ValueError("Credit transactions cannot be deleted")


class CreditPackage(models.Model):
	name = models.CharField(max_length=100)
	credits = models.PositiveIntegerField()
	bonus_credits = models.PositiveIntegerField(default=0)
	price = models.DecimalField(max_digits=10, decimal_places=2)
	currency = models.CharField(max_length=3, default="BDT")
	features = models.JSONField(default=list, blank=True)
	highlighted = models.BooleanField(default=False)
	sort_order = models.IntegerField(default=0)
	active = models.BooleanField(default=True)

	class Meta:
		ordering = ("sort_order",)

	def __str__(self):
		return self.name


class CreditRequest(models.Model):
	"""Manual bKash top-up: customer asks for a package, sends money, then submits the transaction id."""
	STATUS_CHOICES = [("requested", "Requested"), ("paid", "Payment submitted"), ("granted", "Credits granted"), ("rejected", "Rejected")]
	project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="credit_requests")
	user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
	package = models.ForeignKey(CreditPackage, on_delete=models.PROTECT, related_name="+")
	phone = models.CharField(max_length=20)  # contact number
	bkash_phone = models.CharField(max_length=20, blank=True)  # number the money was sent from
	trx_id = models.CharField(max_length=40, blank=True)
	status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="requested")
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	@property
	def reference(self):
		return f"AM{self.pk}"

	def __str__(self):
		return f"{self.reference} {self.package} {self.status}"

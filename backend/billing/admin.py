from datetime import date, timedelta

from django import forms
from django.contrib import admin
from django.template.response import TemplateResponse
from django.utils import timezone
from django.urls import path

from . import report, service
from .models import ActionPrice, CreditPackage, CreditTransaction, CreditWallet, PricingSettings


@admin.register(ActionPrice)
class ActionPriceAdmin(admin.ModelAdmin):
	list_display = ("action", "credits", "updated_at")
	list_editable = ("credits",)

	def has_add_permission(self, request):
		return False

	def has_delete_permission(self, request, obj=None):
		return False


@admin.register(PricingSettings)
class PricingSettingsAdmin(admin.ModelAdmin):
	def has_add_permission(self, request):
		return not PricingSettings.objects.exists()

	def has_delete_permission(self, request, obj=None):
		return False


@admin.register(CreditPackage)
class CreditPackageAdmin(admin.ModelAdmin):
	list_display = ("name", "credits", "bonus_credits", "price", "highlighted", "active", "sort_order")
	list_editable = ("sort_order", "active")


class CreditTransactionInline(admin.TabularInline):
	model = CreditTransaction
	fk_name = "wallet"
	ordering = ("-created_at", "-id")
	fields = ("created_at", "kind", "action", "amount", "balance_after", "created_by", "note")
	readonly_fields = fields
	extra = 0
	can_delete = False

	def has_add_permission(self, request, obj=None):
		return False

	def has_change_permission(self, request, obj=None):
		return False


@admin.register(CreditWallet)
class CreditWalletAdmin(admin.ModelAdmin):
	list_display = ("project_name", "project", "balance", "updated_at")
	search_fields = ("project_name",)
	inlines = [CreditTransactionInline]

	def get_readonly_fields(self, request, obj=None):
		return [f.name for f in self.model._meta.fields]

	def has_add_permission(self, request):
		return False

	def has_delete_permission(self, request, obj=None):
		return False

	def get_urls(self):
		return [path("coverage/", self.admin_site.admin_view(self.coverage_view), name="billing_creditwallet_coverage")] + super().get_urls()

	def coverage_view(self, request):
		def parse(key, default):
			try:
				return date.fromisoformat(request.GET.get(key, ""))
			except ValueError:
				return default
		end = parse("end", timezone.localdate())
		start = parse("start", end - timedelta(days=30))
		data = report.coverage(start, end)
		ctx = {
			**self.admin_site.each_context(request), "title": "Cost coverage", "start": start, "end": end, **data,
			"any_unpriced": any(r["unpriced_events"] for r in data["rows"]),
		}
		return TemplateResponse(request, "admin/billing/coverage.html", ctx)


class GrantForm(forms.ModelForm):
	kind = forms.ChoiceField(choices=[("grant", "Grant"), ("adjust", "Adjustment")])
	note = forms.CharField(widget=forms.Textarea)

	class Meta:
		model = CreditTransaction
		fields = ("wallet", "kind", "amount", "note")

	def clean_wallet(self):
		wallet = self.cleaned_data["wallet"]
		if wallet.project_id is None:
			raise forms.ValidationError("This wallet's project was deleted.")
		return wallet

	def clean(self):
		data = super().clean()
		amount, kind = data.get("amount"), data.get("kind")
		if amount is not None and (amount == 0 or (kind == "grant" and amount < 0)):
			raise forms.ValidationError("Amount must be non-zero, and positive for a grant.")
		return data


@admin.register(CreditTransaction)
class CreditTransactionAdmin(admin.ModelAdmin):
	list_display = ("created_at", "wallet", "kind", "action", "amount", "balance_after", "created_by")
	list_filter = ("kind", "action")
	search_fields = ("wallet__project_name", "note")
	list_select_related = ("wallet", "created_by")
	form = GrantForm

	def has_change_permission(self, request, obj=None):
		return False

	def has_delete_permission(self, request, obj=None):
		return False

	def save_model(self, request, obj, form, change):
		# The ledger is written only by the service so the balance moves with the row.
		txn = service.grant(obj.wallet.project, obj.amount, obj.kind, created_by=request.user, note=obj.note)
		# point the admin log entry and "added" link at the real ledger row
		obj.pk, obj.balance_after, obj.created_at = txn.pk, txn.balance_after, txn.created_at

from django.contrib import admin, messages
from django.http import HttpResponseNotAllowed
from django.shortcuts import redirect
from django.urls import path

from . import pricing
from .models import AIModel, ModelConfig


@admin.register(AIModel)
class AIModelAdmin(admin.ModelAdmin):
	list_display = ("name", "kind", "provider", "input_price_per_mtok", "output_price_per_mtok")
	list_filter = ("kind", "provider")
	search_fields = ("name",)
	change_list_template = "admin/agents/aimodel/change_list.html"

	def get_urls(self):
		return [path("sync-prices/", self.admin_site.admin_view(self.sync_prices_view), name="agents_aimodel_sync_prices")] + super().get_urls()

	def sync_prices_view(self, request):
		if request.method != "POST":
			return HttpResponseNotAllowed(["POST"])
		try:
			result = pricing.sync_openrouter_prices()
		except Exception as exc:  # network/auth/JSON failure: tell the admin, don't 500
			self.message_user(request, f"OpenRouter sync failed: {exc}", messages.ERROR)
		else:
			self.message_user(request, f"OpenRouter prices synced: {result['updated']} updated, {result['unchanged']} unchanged, not on OpenRouter: {', '.join(result['not_found']) or 'none'}")
		return redirect("admin:agents_aimodel_changelist")


@admin.register(ModelConfig)
class ModelConfigAdmin(admin.ModelAdmin):
	list_display = ("__str__", "reply_model", "fallback_model", "memory_model", "transcription_model", "updated_at")

	def has_add_permission(self, request):
		return not ModelConfig.objects.exists()

	def has_delete_permission(self, request, obj=None):
		return False

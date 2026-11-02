from django.contrib import admin

from .models import AIModel, ModelConfig


@admin.register(AIModel)
class AIModelAdmin(admin.ModelAdmin):
	list_display = ("name", "kind", "provider", "input_price_per_mtok", "output_price_per_mtok")
	list_filter = ("kind", "provider")
	search_fields = ("name",)


@admin.register(ModelConfig)
class ModelConfigAdmin(admin.ModelAdmin):
	list_display = ("__str__", "reply_model", "fallback_model", "memory_model", "transcription_model", "updated_at")

	def has_add_permission(self, request):
		return not ModelConfig.objects.exists()

	def has_delete_permission(self, request, obj=None):
		return False

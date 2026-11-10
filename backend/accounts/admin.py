from django.contrib import admin
from billing.service import balance_of
from .models import Project, ProjectUser


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
	list_display = ("id", "name", "slug", "image_search_allowed", "image_search_enabled", "created_at")
	list_filter = ("image_search_allowed", "image_search_enabled")
	list_editable = ("image_search_allowed",)
	readonly_fields = ("image_search_enabled", "created_by", "credit_balance")
	search_fields = ("name", "slug")

	@admin.display(description="Credit balance")
	def credit_balance(self, obj):
		return balance_of(obj)


@admin.register(ProjectUser)
class ProjectUserAdmin(admin.ModelAdmin):
	list_display = ("id", "project", "user", "role", "created_at")
	search_fields = ("project__name", "user__username", "role")
	list_filter = ("role",)

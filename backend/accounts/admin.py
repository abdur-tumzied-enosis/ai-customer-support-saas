from django.contrib import admin
from .models import Project, ProjectUser


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
	list_display = ("id", "name", "slug", "image_search_allowed", "image_search_enabled", "created_at")
	list_filter = ("image_search_allowed", "image_search_enabled")
	list_editable = ("image_search_allowed",)
	readonly_fields = ("image_search_enabled",)
	search_fields = ("name", "slug")


@admin.register(ProjectUser)
class ProjectUserAdmin(admin.ModelAdmin):
	list_display = ("id", "project", "user", "role", "created_at")
	search_fields = ("project__name", "user__username", "role")
	list_filter = ("role",)



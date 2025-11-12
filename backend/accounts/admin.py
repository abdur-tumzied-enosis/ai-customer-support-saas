from django.contrib import admin
from .models import Project, ProjectUser


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
	list_display = ("id", "name", "slug", "created_at")
	search_fields = ("name", "slug")


@admin.register(ProjectUser)
class ProjectUserAdmin(admin.ModelAdmin):
	list_display = ("id", "project", "user", "role", "created_at")
	search_fields = ("project__name", "user__username", "role")
	list_filter = ("role",)



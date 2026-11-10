from datetime import date, timedelta

from django.contrib import admin
from django.http import HttpResponseForbidden
from django.template.response import TemplateResponse
from django.urls import path
from django.utils import timezone

from . import report
from .models import UsageEvent


@admin.register(UsageEvent)
class UsageEventAdmin(admin.ModelAdmin):
	list_display = ("created_at", "project", "kind", "provider", "model_name", "tokens", "cost_usd", "cost_source")
	list_filter = ("kind", "cost_source", "provider")
	change_list_template = "admin/usage/usageevent/change_list.html"

	# Cost and token data is internal and superuser-only; staff without superuser see nothing here.
	def has_module_permission(self, request):
		return request.user.is_superuser

	def has_view_permission(self, request, obj=None):
		return request.user.is_superuser

	def has_add_permission(self, request):
		return False

	def has_change_permission(self, request, obj=None):
		return False

	def has_delete_permission(self, request, obj=None):
		return False

	def changelist_view(self, request, extra_context=None):
		if not request.user.is_superuser:  # Django would raise PermissionDenied, which core's middleware turns into a 500
			return HttpResponseForbidden()
		return super().changelist_view(request, extra_context)

	def get_urls(self):
		return [path("report/", self.admin_site.admin_view(self.report_view), name="usage_usageevent_report")] + super().get_urls()

	def report_view(self, request):
		if not request.user.is_superuser:  # admin_view only checks is_staff
			return HttpResponseForbidden()  # not PermissionDenied: core's middleware turns that into a 500

		def parse(key, default):
			try:
				return date.fromisoformat(request.GET.get(key, ""))
			except ValueError:
				return default

		end = parse("end", timezone.localdate())
		start = parse("start", end - timedelta(days=30))
		project = request.GET.get("project") or None
		data = report.ai_cost_report(start, end, project_id=project)
		ctx = {**self.admin_site.each_context(request), "title": "AI cost report", "start": start, "end": end, "project": project, **data}
		return TemplateResponse(request, "admin/usage/ai_cost_report.html", ctx)

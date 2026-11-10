from django.contrib import admin

from billing.report import conversation_totals

from .models import Conversation


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
	list_display = ("id", "project", "name", "credits", "known_cost")
	list_select_related = ("project",)

	# shortcut: two aggregate queries per row, fine for a 100-row admin page
	@admin.display(description="Credits charged")
	def credits(self, obj):
		return conversation_totals(obj)[0]

	@admin.display(description="Known cost (USD)")
	def known_cost(self, obj):
		return conversation_totals(obj)[1]

	def has_add_permission(self, request):
		return False

	def has_change_permission(self, request, obj=None):
		return False

	def has_delete_permission(self, request, obj=None):
		return False

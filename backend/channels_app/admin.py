from django.contrib import admin

from billing.report import conversation_totals

from .models import Conversation, Message
from .reply import resolve_unknown


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


@admin.register(Message)
class UnknownDeliveryAdmin(admin.ModelAdmin):
	"""Replies held because a send may or may not have reached the customer (a timeout, or a worker that died mid-send).
	Check the customer's chat, then mark the reply delivered or resend it. WhatsApp ones usually settle themselves."""
	list_display = ("id", "project", "contact", "text", "created_at")
	list_select_related = ("project", "contact")
	actions = ["mark_delivered", "resend"]

	def get_queryset(self, request):
		return super().get_queryset(request).filter(direction="out", status="unknown")

	@admin.action(description="The customer got it: mark delivered")
	def mark_delivered(self, request, queryset):
		for msg in queryset:
			resolve_unknown(msg.id, accepted=True)

	@admin.action(description="The customer did not get it: send again")
	def resend(self, request, queryset):
		for msg in queryset:
			resolve_unknown(msg.id, accepted=False)

	def has_add_permission(self, request):
		return False

	def has_change_permission(self, request, obj=None):
		return False  # the actions above still work; the message text, meta and status stay read-only

	def has_delete_permission(self, request, obj=None):
		return False

from datetime import timedelta

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from .models import Order, OrderSettings, OrderStatusHistory


@shared_task
def expire_stale_drafts() -> int:
	"""Abandon each draft not touched for its project's draft_expiry_hours. Returns how many were abandoned."""
	abandoned = 0
	for settings in OrderSettings.objects.all():
		cutoff = timezone.now() - timedelta(hours=settings.draft_expiry_hours)
		stale_ids = list(
			Order.objects.filter(project_id=settings.project_id, status="draft", updated_at__lt=cutoff)
			.values_list("id", flat=True)
		)
		for order_id in stale_ids:
			with transaction.atomic():
				# re-read under a lock: the customer may have changed the draft since the query above
				order = Order.objects.select_for_update().get(pk=order_id)
				if order.status != "draft" or order.updated_at >= cutoff:
					continue
				order.status = "abandoned"
				order.save(update_fields=["status", "updated_at"])
				OrderStatusHistory.objects.create(
					order=order, actor_type="system", actor_id="", from_status="draft", to_status="abandoned",
				)
				abandoned += 1
	return abandoned

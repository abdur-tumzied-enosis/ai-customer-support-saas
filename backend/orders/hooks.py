from celery import shared_task

from channels_app.webhooks import emit_event

from .models import Order, OrderAction
from .notify import notify_new_order, order_payload


@shared_task
def order_submitted(order_id: str) -> None:
	from .actions import run_action_for_orders  # actions imports service, which imports this module

	order = Order.objects.select_related("project").filter(pk=order_id).first()
	if order is None or order.is_test:  # a test order triggers no email, webhook or automatic action
		return
	notify_new_order(order)
	emit_event(order.project, "order.created", order_payload(order))
	for action in OrderAction.objects.filter(project=order.project, is_active=True, auto_run=True):
		run_action_for_orders(action, [order], ("system", "auto"))


@shared_task
def order_status_changed(order_id: str, from_status: str, to_status: str) -> None:
	order = Order.objects.select_related("project").filter(pk=order_id).first()
	if order is None or order.is_test:
		return
	emit_event(order.project, "order.status_changed", {"order": order_payload(order), "from": from_status, "to": to_status})

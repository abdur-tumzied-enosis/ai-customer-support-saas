from accounts.tasks import queue_email

from .models import OrderSettings


def order_payload(order) -> dict:
	"""The fixed body of the order events posted to the project's webhook."""
	return {
		"order": {
			"id": str(order.id), "number": order.number, "status": order.status, "currency": order.currency,
			"channel": order.channel, "subtotal": str(order.subtotal), "zone_charge": str(order.zone_charge),
			"total": str(order.total), "advance_paid": str(order.advance_paid),
			"collect_amount": str(order.collect_amount), "tracking_number": order.tracking_number,
			"created_at": order.created_at.isoformat(),
			"submitted_at": order.submitted_at.isoformat() if order.submitted_at else None,
		},
		"customer": {
			"name": order.customer_name, "phone": order.customer_phone, "address": order.customer_address,
			"zone": order.zone_name, "note": order.customer_note, "custom": order.custom,
		},
		"items": [
			{
				"product_id": str(i.product_id) if i.product_id else None, "external_id": i.product_external_id,
				"sku": i.sku, "name": i.name, "variant": i.variant_label, "quantity": i.quantity,
				"unit_price": str(i.unit_price), "product_url": i.product_url,
			}
			for i in order.items.all()
		],
	}


def notify_new_order(order) -> None:
	settings = OrderSettings.objects.filter(project_id=order.project_id).first()
	if settings is None:
		return
	lines = [
		f"{i.quantity} x {i.name}" + (f" ({i.variant_label})" if i.variant_label else "")
		+ f" ({i.product_external_id or i.sku}) @ {i.unit_price}"
		for i in order.items.all()
	]
	body = "\n".join([
		f"Customer: {order.customer_name}",
		f"Phone: {order.customer_phone}",
		f"Address: {order.customer_address}",
		f"Zone: {order.zone_name}",
		*lines,
		f"Total: {order.total} {order.currency}",
		f"Collect: {order.collect_amount} {order.currency}",
	])
	subject = f"New order #{order.number} · {order.project.name}"
	for address in settings.notification_emails:
		queue_email(address, subject, body)

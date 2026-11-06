from celery import shared_task


# Empty until Plan C; the call sites in service.py need these names to exist now.
@shared_task
def order_submitted(order_id: str) -> None:
	return None


@shared_task
def order_status_changed(order_id: str, from_status: str, to_status: str) -> None:
	return None

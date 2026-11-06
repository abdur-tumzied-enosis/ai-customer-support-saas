from django.db.models import Q

from channels_app.models import ChannelCredential, Widget
from knowledge.models import Document, Product, ProductSource

from .models import OrderSettings


def _item(key: str, label: str, ok: bool, hard: bool, setup: str) -> dict:
	status = "ok" if ok else ("fail" if hard else "warn")
	return {"key": key, "label": label, "status": status, "hard": hard, "setup": setup}


def check(project) -> list[dict]:
	"""What must be in place before ordering can go live. A hard 'fail' blocks going live; a 'warn' does not."""
	settings = OrderSettings.objects.filter(project=project).first()
	zones = settings.zones if settings else []
	has_channel = (
		ChannelCredential.objects.filter(project=project, is_active=True).exists()
		or Widget.objects.filter(project=project, is_active=True).exists()
	)
	has_source = (
		Document.objects.filter(kb__project=project).exclude(markdown_content="").exists()
		or ProductSource.objects.filter(kb__project=project).exists()
	)
	has_keys = Product.objects.filter(kb__project=project).filter(
		(Q(external_id__isnull=False) & ~Q(external_id="")) | ~Q(sku="")
	).exists()
	delivery_ok = bool(zones) and all(z.get("charge") not in (None, "") for z in zones)
	return [
		_item("channel", "A messaging channel or the website widget is connected", has_channel, True, "channels"),
		_item("product_source", "Product information is uploaded or a product sheet is connected", has_source, True, "knowledge"),
		_item("currency", "Currency is set", bool(project.currency), False, "settings"),
		_item("delivery", "Delivery zones and charges are set", delivery_ok, False, "settings"),
		_item("product_keys", "Products have a code or SKU", has_keys, False, "products"),
	]

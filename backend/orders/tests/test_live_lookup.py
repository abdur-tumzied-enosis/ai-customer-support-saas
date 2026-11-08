import json
from decimal import Decimal
from unittest.mock import patch

from agents.rag import Context
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Project, ProjectUser
from channels_app.models import Conversation, CustomerContact
from knowledge.models import KnowledgeBase, Product
from orders import service
from orders.http import HttpResult, OutboundError
from orders.models import Order, OrderItem, OrderSettings
from orders.pricing import quotes_for
from orders.tools import build_order_tools

User = get_user_model()
SECRET = "LIVE-KEY-999"
URL = "https://api.example.com/p/{{product.external_id}}"
LIVE = {
	"enabled": True, "url_template": URL, "headers": [{"name": "X-Key", "value": SECRET}],
	"price_path": "data.price", "available_path": "data.in_stock",
}


class LiveLookupTest(TestCase):
	def setUp(self):
		cache.clear()
		self.project = Project.objects.create(name="P", slug="0123456789abcdef0123456789abd2")
		self.kb = KnowledgeBase.objects.create(project=self.project, name="default", vector_collection="vc-live1")
		self.product = Product.objects.create(
			kb=self.kb, name="Shirt", external_id="S1", sku="SKU-1", price=Decimal("100"), status="active",
		)
		self.settings = OrderSettings.objects.create(project=self.project, enabled=True, live_lookup=LIVE)

	def live_response(self, text='{"data": {"price": "250", "in_stock": true}}', status=200):
		return HttpResult(status=status, text=text, ok=200 <= status < 300)

	def test_live_price_used_when_enabled(self):
		with patch("orders.live.safe_request", return_value=self.live_response()):
			quote = quotes_for([self.product])[str(self.product.id)]
		self.assertEqual(quote.price, Decimal("250"))
		self.assertTrue(quote.available)

	def test_live_lookup_internal_url_blocked(self):
		self.settings.live_lookup = {**LIVE, "url_template": "http://10.0.0.5/p/{{product.external_id}}"}
		self.settings.save()
		with self.assertLogs("orders.live", level="WARNING"):
			quote = quotes_for([self.product])[str(self.product.id)]
		self.assertEqual(quote.price, Decimal("100"))  # the catalog price, no request made

	def test_failure_falls_back_to_catalog_and_logs(self):
		with patch("orders.live.safe_request", side_effect=OutboundError("timed out")), \
				self.assertLogs("orders.live", level="WARNING") as logs:
			quote = quotes_for([self.product])[str(self.product.id)]
		self.assertEqual(quote.price, Decimal("100"))
		self.assertNotIn("LIVE-KEY-999", "".join(logs.output))

	def test_no_price_from_live_never_sellable(self):
		with patch("orders.live.safe_request", return_value=self.live_response('{"data": {}}')):
			quote = quotes_for([self.product])[str(self.product.id)]
		self.assertIsNone(quote.price)
		self.assertFalse(quote.available)

	def test_cache_used_unless_fresh(self):
		with patch("orders.live.safe_request", return_value=self.live_response()) as request:
			quotes_for([self.product])
			quotes_for([self.product])
			self.assertEqual(request.call_count, 1)
			quotes_for([self.product], fresh=True)
			self.assertEqual(request.call_count, 2)

	def test_submit_is_always_fresh(self):
		OrderSettings.objects.filter(pk=self.settings.pk).update(zones=[{"name": "Dhaka", "charge": "60"}], form_fields={"optional": [], "custom": []})
		self.settings.refresh_from_db()
		with patch("orders.live.safe_request", return_value=self.live_response()) as request:
			quotes_for([self.product])  # primes the cache
			contact = CustomerContact.objects.create(project=self.project, platform="self", external_id="c9")
			conv = Conversation.objects.create(project=self.project, contact=contact, knowledge_base=self.kb)
			order = Order.objects.create(
				project=self.project, conversation=conv, channel="self", currency="BDT", status="draft",
				customer_name="Rahim", customer_phone="+8801712345678", customer_address="Road 1", zone_name="Dhaka",
			)
			OrderItem.objects.create(
				order=order, product=self.product, product_external_id="S1", sku="SKU-1", name="Shirt", quantity=1,
				unit_price=Decimal("100"), line_total=Decimal("100"),
			)
			before = request.call_count
			service.submit_order(order, ("user", "1"))
			self.assertGreater(request.call_count, before)

	def test_url_template_encodes_external_id(self):
		self.product.external_id = "A/B 1&2"
		self.product.save()
		with patch("orders.live.safe_request", return_value=self.live_response()) as request:
			quotes_for([self.product])
		self.assertEqual(request.call_args.args[1], "https://api.example.com/p/A%2FB%201%262")

	def test_find_products_hides_unpriced_live_product(self):
		context = Context()
		conv = Conversation.objects.create(
			project=self.project, contact=CustomerContact.objects.create(project=self.project, platform="self", external_id="c8"),
			knowledge_base=self.kb,
		)
		tools = {t.__name__: t for t in build_order_tools(conv, self.settings, context)}
		with patch("orders.live.safe_request", return_value=self.live_response('{"data": {}}')):
			out = json.loads(tools["find_products"]("Shirt"))["products"]
		self.assertIsNone(out[0]["price"])
		self.assertFalse(out[0]["available"])

	def test_live_settings_secret_never_returned(self):
		admin = User.objects.create_user(username="a@x.test", email="a@x.test", password="x")
		ProjectUser.objects.create(project=self.project, user=admin, role="admin")
		client = APIClient()
		client.force_authenticate(user=admin)
		url = f"/api/orders/settings/?project_id={self.project.id}"
		r = client.get(url)
		self.assertEqual(r.status_code, 200, r.content)
		self.assertNotContains(r, SECRET)
		self.assertEqual(r.json()["data"]["live_lookup"]["headers"], [{"name": "X-Key", "has_value": True}])
		r = client.patch(url, {"live_lookup": {**LIVE, "headers": [{"name": "X-Key", "value": ""}]}}, format="json")
		self.assertEqual(r.status_code, 200, r.content)
		self.settings.refresh_from_db()
		self.assertEqual(self.settings.live_lookup["headers"], [{"name": "X-Key", "value": SECRET}])

	def test_failed_lookup_not_retried_within_a_minute(self):
		with patch("orders.live.safe_request", side_effect=OutboundError("timed out")) as request:
			quotes_for([self.product])
			quotes_for([self.product])
			self.assertEqual(request.call_count, 1)
			quotes_for([self.product], fresh=True)  # submit always asks the client
			self.assertEqual(request.call_count, 2)

	def test_lookup_budget_leaves_later_products_on_catalog(self):
		other = Product.objects.create(kb=self.kb, name="Cap", external_id="S2", price=Decimal("40"), status="active")
		seq = iter([0, 0, 9, 9])  # start, first product (inside budget), second product (budget spent)
		with patch("orders.pricing.monotonic", side_effect=lambda: next(seq)), \
				patch("orders.live.safe_request", return_value=self.live_response()) as request:
			quotes = quotes_for([self.product, other])
		self.assertEqual(request.call_count, 1)
		self.assertEqual(quotes[str(other.id)].price, Decimal("40"))

from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Project, ProjectUser
from channels_app.models import Conversation, CustomerContact, Message
from knowledge.models import KnowledgeBase, Product
from orders.from_chat import Prefill, PrefillCustomer, PrefillItem, prefill_from_conversation
from orders.models import Order, OrderSettings
from orders.service import get_or_create_draft

User = get_user_model()
CUSTOMER = {"customer_name": "Rahim", "customer_phone": "+8801712345678", "customer_address": "Road 1", "zone_name": "Dhaka"}


class FakeLLM:
	def __init__(self, parsed):
		self.parsed = parsed

	def with_structured_output(self, schema, **kwargs):
		return self

	def invoke(self, messages):
		raw = MagicMock()
		raw.usage_metadata = {"input_tokens": 50, "output_tokens": 10}
		return {"raw": raw, "parsed": self.parsed}


class FromChatTest(TestCase):
	def setUp(self):
		self.user = User.objects.create_user(username="c@x.test", email="c@x.test", password="x")
		self.project = Project.objects.create(name="P", slug="p-from-chat")
		ProjectUser.objects.create(project=self.project, user=self.user, role="member")
		self.kb = KnowledgeBase.objects.create(project=self.project, name="Products", vector_collection="vc-chat")
		self.shirt = Product.objects.create(kb=self.kb, name="Shirt", external_id="S1", price=Decimal("250"), status="active")
		self.settings = OrderSettings.objects.create(
			project=self.project, enabled=True, channels=["facebook"],
			zones=[{"name": "Dhaka", "charge": "60"}], form_fields={"optional": [], "custom": []},
		)
		contact = CustomerContact.objects.create(project=self.project, platform="facebook", external_id="ch1")
		self.conv = Conversation.objects.create(project=self.project, contact=contact, knowledge_base=self.kb)
		Message.objects.create(project=self.project, contact=contact, conversation=self.conv, direction="in", text="I want 2 shirts")
		self.client = APIClient()
		self.client.force_authenticate(user=self.user)

	def prefill(self, parsed, track=None):
		track = track or MagicMock()
		with patch("orders.from_chat.get_llm", return_value=FakeLLM(parsed)), \
				patch("orders.from_chat.llm_pricing", return_value={"chat_in": {"cost_usd": None}, "chat_out": {"cost_usd": None}}), \
				patch("orders.from_chat.track_token_usage", track):
			return prefill_from_conversation(self.conv)

	def test_prefill_drops_unknown_product(self):
		result = self.prefill(Prefill(items=[PrefillItem(product_id="00000000-0000-0000-0000-000000000000", quantity=1)]))
		self.assertEqual(result["items"], [])

	def test_prefill_sends_unpriced_to_needs_price(self):
		cap = Product.objects.create(kb=self.kb, name="Cap", external_id="C1", price=None, status="needs_info")
		result = self.prefill(Prefill(items=[PrefillItem(product_id=str(cap.id), quantity=1)]))
		self.assertEqual(result["items"], [])
		self.assertEqual(result["needs_price"], [{"product_id": str(cap.id), "name": "Cap"}])

	def test_tracked_with_source(self):
		track = MagicMock()
		self.prefill(Prefill(items=[], customer=PrefillCustomer(customer_name="Rahim")), track=track)
		sources = [c.args[3]["source"] for c in track.call_args_list]
		self.assertEqual(sources, ["order_from_chat", "order_from_chat"])

	def test_create_reuses_existing_draft(self):
		get_or_create_draft(self.conv, self.settings)  # an earlier, incomplete attempt left this draft
		r = self.client.post("/api/orders/", {
			"conversation_id": str(self.conv.id),
			"items": [{"product_id": str(self.shirt.id), "quantity": 1}], "customer": CUSTOMER,
		}, format="json")
		self.assertEqual(r.status_code, 201, r.content)
		self.assertEqual(r.json()["data"]["order_number"], 1001)
		self.assertEqual(Order.objects.filter(conversation=self.conv).count(), 1)

	def test_create_submits_complete_order(self):
		r = self.client.post("/api/orders/", {
			"conversation_id": str(self.conv.id),
			"items": [{"product_id": str(self.shirt.id), "quantity": 2}], "customer": CUSTOMER,
		}, format="json")
		self.assertEqual(r.status_code, 201, r.content)
		self.assertEqual(r.json()["data"]["order_number"], 1001)
		self.assertEqual(r.json()["data"]["status"], "new")

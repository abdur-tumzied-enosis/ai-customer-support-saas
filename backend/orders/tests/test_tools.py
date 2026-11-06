import inspect
import json
from decimal import Decimal

from django.test import TestCase

from accounts.models import Project
from agents.rag import Context, ToolDeps, _build_tools_from_context
from channels_app.models import Conversation, CustomerContact
from channels_app.tasks import build_reply_context
from knowledge.models import KnowledgeBase, Product, ProductVariant
from orders.guard import trace_unconfirmed, unconfirmed_claim
from orders.models import Order, OrderSettings
from orders.tools import build_order_tools

CUSTOMER = {"customer_name": "Rahim", "customer_phone": "+8801712345678", "customer_address": "Road 1", "zone_name": "Dhaka"}


def tool_map(tools):
	return {t.__name__: t for t in tools}


class OrderToolsTest(TestCase):
	def setUp(self):
		self.project = Project.objects.create(name="P", slug="p-tools")
		self.kb = KnowledgeBase.objects.create(project=self.project, name="Products", vector_collection="vc-tools")
		self.product = Product.objects.create(kb=self.kb, name="Shirt", external_id="S1", price=Decimal("250"), status="active")
		self.settings = OrderSettings.objects.create(
			project=self.project, enabled=True, channels=["facebook"],
			zones=[{"name": "Dhaka", "charge": "60"}], form_fields={"optional": [], "custom": []},
		)
		contact = CustomerContact.objects.create(project=self.project, platform="facebook", external_id="f1")
		self.conv = Conversation.objects.create(project=self.project, contact=contact, knowledge_base=self.kb)
		self.context = Context()
		self.tools = tool_map(build_order_tools(self.conv, self.settings, self.context))

	def test_find_products_hides_needs_info_price(self):
		Product.objects.create(kb=self.kb, name="Cap", external_id="C1", price=None, status="needs_info")
		out = json.loads(self.tools["find_products"]("Cap"))
		self.assertEqual(out[0]["status"], "needs_info")
		self.assertNotIn("price", out[0])

	def test_update_draft_has_no_price_argument(self):
		params = inspect.signature(self.tools["update_draft"]).parameters
		self.assertFalse({"price", "unit_price", "discount"} & set(params))

	def test_submit_sets_flag(self):
		self.tools["update_draft"]([{"product_id": str(self.product.id), "quantity": 1}], CUSTOMER)
		out = json.loads(self.tools["submit_order"]())
		self.assertEqual(out["order_number"], 1001)
		self.assertTrue(self.context.order_submitted)

	def test_submit_logs_post_so_retry_never_resubmits(self):
		self.tools["update_draft"]([{"product_id": str(self.product.id), "quantity": 1}], CUSTOMER)
		self.tools["submit_order"]()
		self.assertTrue(any(c.get("method") == "POST" for c in self.context.api_call_log))

	def test_find_products_variant_without_price_uses_product_price(self):
		product = Product.objects.create(kb=self.kb, name="Mug", external_id="M1", price=Decimal("100"), status="active")
		ProductVariant.objects.create(product=product, label="Large", price=None, available=True)
		out = json.loads(self.tools["find_products"]("Mug"))
		self.assertEqual(out[0]["variants"][0]["price"], "100.00")

	def test_change_only_while_new(self):
		Order.objects.create(
			project=self.project, conversation=self.conv, channel="facebook", currency="BDT",
			status="shipped", number=1001,
		)
		out = json.loads(self.tools["change_order"](1001, customer={"customer_note": "x"}))
		self.assertEqual(out["error"]["code"], "not_editable")


class ToolWiringTest(TestCase):
	def setUp(self):
		self.project = Project.objects.create(name="P", slug="p-wiring")
		self.kb = KnowledgeBase.objects.create(project=self.project, name="Products", vector_collection="vc-wire")
		self.settings = OrderSettings.objects.create(
			project=self.project, enabled=True, channels=["facebook"],
			zones=[{"name": "Dhaka", "charge": "60"}], form_fields={"optional": [], "custom": []},
		)
		contact = CustomerContact.objects.create(project=self.project, platform="facebook", external_id="f2")
		self.conv = Conversation.objects.create(project=self.project, contact=contact, knowledge_base=self.kb)

	def names(self, conv):
		context, deps = build_reply_context(self.project, self.kb, conv)
		return {t.__name__ for t in _build_tools_from_context(context, deps, retrievers=[])}

	def test_tools_present_for_enabled_channel(self):
		self.assertIn("submit_order", self.names(self.conv))

	def test_tools_absent_for_channel_not_listed(self):
		OrderSettings.objects.filter(pk=self.settings.pk).update(channels=["widget"])
		self.assertNotIn("submit_order", self.names(self.conv))

	def test_tools_absent_when_feature_off(self):
		OrderSettings.objects.filter(pk=self.settings.pk).update(enabled=False)
		self.assertNotIn("submit_order", self.names(self.conv))


class ConfirmationTraceTest(TestCase):
	def test_claimed_confirmation_without_submit_traced(self):
		ctx = Context()
		trace_unconfirmed(ctx, "Your order placed! Order number 1001.")
		self.assertIn("hallucinated_confirmation", ctx.trace.get("flags", []))

	def test_no_flag_when_submitted_this_turn(self):
		ctx = Context(order_submitted=True)
		trace_unconfirmed(ctx, "Your order placed! Order number 1001.")
		self.assertNotIn("hallucinated_confirmation", ctx.trace.get("flags", []))

	def test_order_number_question_not_flagged(self):
		self.assertFalse(unconfirmed_claim("What is your order number?", False))

	def test_bengali_claim_detected(self):
		self.assertTrue(unconfirmed_claim("আপনার অর্ডার হয়েছে", False))

	def test_plain_reply_not_flagged(self):
		self.assertFalse(unconfirmed_claim("Which size would you like?", False))

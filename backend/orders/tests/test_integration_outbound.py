from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Project, ProjectUser
from channels_app.models import Conversation, CustomerContact
from knowledge.models import KnowledgeBase, Product
from orders import hooks, service
from orders.actions import execute_run, run_action_for_orders
from orders.http import HttpResult
from orders.models import Order, OrderAction, OrderItem, OrderSettings
from orders.tasks import run_action_run

User = get_user_model()
SECRET = "INTEG-SECRET-777"


class OutboundIntegrationTest(TestCase):
	def setUp(self):
		self.project = Project.objects.create(name="Shop", slug="0123456789abcdef0123456789abd3")
		self.kb = KnowledgeBase.objects.create(project=self.project, name="default", vector_collection="vc-integ")
		self.product = Product.objects.create(
			kb=self.kb, name="Shirt", external_id="S1", sku="SKU-1", price=Decimal("250"), status="active",
		)
		self.settings = OrderSettings.objects.create(
			project=self.project, enabled=True, zones=[{"name": "Dhaka", "charge": "60"}],
			form_fields={"optional": [], "custom": []},
			live_lookup={"enabled": False, "url_template": "", "headers": [{"name": "X-Key", "value": SECRET}], "price_path": ""},
		)
		self.action = OrderAction.objects.create(
			project=self.project, name="Courier", method="POST", url="https://courier.example.com/orders",
			headers=[{"name": "X-Api-Key", "value": SECRET}], body_template='{"order": {{ order.number }}}',
			response_mapping={"tracking_number": "tracking"}, success_status="shipped", auto_run=True, is_active=True,
		)
		contact = CustomerContact.objects.create(project=self.project, platform="self", external_id="c1")
		self.conv = Conversation.objects.create(project=self.project, contact=contact, knowledge_base=self.kb)

	def draft(self):
		order = Order.objects.create(
			project=self.project, conversation=self.conv, channel="self", currency="BDT", status="draft",
			customer_name="Rahim", customer_phone="+8801712345678", customer_address="Road 1", zone_name="Dhaka",
		)
		OrderItem.objects.create(
			order=order, product=self.product, product_external_id="S1", sku="SKU-1", name="Shirt", quantity=1,
			unit_price=Decimal("250"), line_total=Decimal("250"),
		)
		return order

	def test_submit_to_action_to_tracking(self):
		order = self.draft()
		with self.captureOnCommitCallbacks(execute=False):  # the submit's own after-commit hook, run below
			submitted = service.submit_order(order, ("user", "1"))
		with patch("orders.actions.safe_request", return_value=HttpResult(200, '{"tracking": "T9"}', True)) as request, \
				patch.object(run_action_run, "delay", side_effect=execute_run), \
				patch.object(hooks.order_status_changed, "delay"), \
				self.captureOnCommitCallbacks(execute=True):
			hooks.order_submitted(str(submitted.id))
		self.assertEqual(request.call_count, 1)
		submitted.refresh_from_db()
		self.assertEqual(submitted.tracking_number, "T9")
		self.assertEqual(submitted.status, "shipped")

	def test_secrets_never_in_api_responses(self):
		admin = User.objects.create_user(username="a@x.test", email="a@x.test", password="x")
		ProjectUser.objects.create(project=self.project, user=admin, role="admin")
		client = APIClient()
		client.force_authenticate(user=admin)
		pid = self.project.id
		for url in (
			f"/api/orders/actions/?project_id={pid}",
			f"/api/orders/actions/{self.action.id}/?project_id={pid}",
			f"/api/orders/settings/?project_id={pid}",
			f"/api/orders/notifications/?project_id={pid}",
		):
			with self.subTest(url=url):
				r = client.get(url)
				self.assertEqual(r.status_code, 200, r.content)
				self.assertNotContains(r, SECRET)

	def test_outbound_logs_have_no_secret(self):
		order = self.draft()
		Order.objects.filter(pk=order.pk).update(number=1001, status="new")  # as if submitted
		order.refresh_from_db()
		with patch("orders.actions.safe_request", side_effect=RuntimeError(f"upstream echoed {SECRET}")), \
				patch.object(run_action_run, "delay", side_effect=execute_run), \
				self.assertLogs("orders.actions", level="ERROR") as logs, \
				self.captureOnCommitCallbacks(execute=True):
			run_action_for_orders(self.action, [order], ("user", "1"))
		self.assertNotIn(SECRET, "".join(logs.output))

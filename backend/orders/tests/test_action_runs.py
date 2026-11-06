from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Project, ProjectUser
from channels_app.models import Conversation, CustomerContact
from knowledge.models import KnowledgeBase
from orders.actions import execute_run, run_action_for_orders
from orders.http import HttpResult
from orders.models import Order, OrderAction, OrderActionRun, OrderStatusHistory
from orders.tasks import run_action_run

User = get_user_model()
SECRET = "SECRET-XYZ"
OK = HttpResult(status=200, text='{"data": {"tracking": "T123"}}', ok=True)


class ActionRunTest(TestCase):
	def setUp(self):
		self.project = Project.objects.create(name="P", slug="0123456789abcdef0123456789abd0")
		self.kb = KnowledgeBase.objects.create(project=self.project, name="default", vector_collection="vc-run1")
		contact = CustomerContact.objects.create(project=self.project, platform="self", external_id="c1")
		self.conv = Conversation.objects.create(project=self.project, contact=contact, knowledge_base=self.kb)
		self.action = OrderAction.objects.create(
			project=self.project, name="Courier", method="POST", url="https://courier.example.com/orders",
			headers=[{"name": "X-Api-Key", "value": SECRET}], body_template='{"order": {{ order.number }}}',
			response_mapping={"tracking_number": "data.tracking"}, success_status="shipped",
		)
		self.orders = [self.new_order(1001), self.new_order(1002), self.new_order(1003)]
		self.actor = ("user", "7")

	def new_order(self, number):
		return Order.objects.create(
			project=self.project, conversation=self.conv, channel="self", currency="BDT", status="new", number=number,
			customer_name="Rahim", customer_phone="+8801712345678", customer_address="Road 1", zone_name="Dhaka",
		)

	def run_all(self, orders=None, force=False, responses=None):
		"""Queue runs and execute them right away, as the broker would after commit."""
		with patch("orders.actions.safe_request", side_effect=responses) as request, \
				patch.object(run_action_run, "delay", side_effect=execute_run), \
				self.captureOnCommitCallbacks(execute=True):
			result = run_action_for_orders(self.action, orders or self.orders, self.actor, force=force)
		return result, request

	def test_action_run_maps_tracking_and_status(self):
		self.run_all(orders=[self.orders[0]], responses=[OK])
		self.orders[0].refresh_from_db()
		self.assertEqual(self.orders[0].tracking_number, "T123")
		self.assertEqual(self.orders[0].status, "shipped")
		self.assertTrue(OrderStatusHistory.objects.filter(order=self.orders[0], from_status="new", to_status="shipped").exists())

	def test_one_failure_does_not_stop_others(self):
		fail = HttpResult(status=500, text="boom", ok=False)
		self.run_all(responses=[OK, fail, OK])
		statuses = [OrderActionRun.objects.get(order=o).status for o in self.orders]
		self.assertEqual(statuses, ["succeeded", "failed", "succeeded"])

	def test_run_error_redacts_secret(self):
		echo = HttpResult(status=401, text=f"bad key {SECRET}", ok=False)
		self.run_all(orders=[self.orders[0]], responses=[echo])
		run = OrderActionRun.objects.get(order=self.orders[0])
		self.assertEqual(run.status, "failed")
		self.assertNotIn(SECRET, run.response_excerpt)
		self.assertIn("***", run.response_excerpt)

	def test_already_sent_needs_force(self):
		self.run_all(orders=[self.orders[0]], responses=[OK])
		result, request = self.run_all(orders=[self.orders[0]], responses=[])
		self.assertEqual(result["already_sent"], [1001])
		self.assertEqual(result["queued"], [])
		self.assertEqual(OrderActionRun.objects.filter(order=self.orders[0]).count(), 1)
		request.assert_not_called()
		forced, _ = self.run_all(orders=[self.orders[0]], force=True, responses=[OK])
		self.assertEqual(len(forced["queued"]), 1)

	def test_missing_placeholder_fails_only_that_order(self):
		Order.objects.filter(pk=self.orders[1].pk).update(number=None)
		self.orders[1].refresh_from_db()
		self.run_all(orders=[self.orders[0], self.orders[1]], responses=[OK])
		self.assertEqual(OrderActionRun.objects.get(order=self.orders[0]).status, "succeeded")
		failed = OrderActionRun.objects.get(order=self.orders[1])
		self.assertEqual(failed.status, "failed")
		self.assertIn("order.number", failed.error)

	def test_retry_increments_attempt(self):
		self.run_all(orders=[self.orders[0]], responses=[HttpResult(status=500, text="boom", ok=False)])
		failed = OrderActionRun.objects.get(order=self.orders[0])
		client = APIClient()
		admin = User.objects.create_user(username="r@x.test", email="r@x.test", password="x")
		ProjectUser.objects.create(project=self.project, user=admin, role="member")
		client.force_authenticate(user=admin)
		with patch("orders.actions.safe_request", return_value=OK), \
				patch.object(run_action_run, "delay", side_effect=execute_run), \
				self.captureOnCommitCallbacks(execute=True):
			r = client.post(f"/api/orders/actions/runs/{failed.id}/retry/")
		self.assertEqual(r.status_code, 201, r.content)
		retry = OrderActionRun.objects.get(order=self.orders[0], attempt=2)
		self.assertEqual(retry.status, "succeeded")

	def test_test_endpoint_runs_once_and_returns_result(self):
		admin = User.objects.create_user(username="t@x.test", email="t@x.test", password="x")
		ProjectUser.objects.create(project=self.project, user=admin, role="admin")
		client = APIClient()
		client.force_authenticate(user=admin)
		with patch("orders.actions.safe_request", return_value=OK) as request:
			r = client.post(f"/api/orders/actions/{self.action.id}/test/", {"order_number": 1001}, format="json")
		self.assertEqual(r.status_code, 200, r.content)
		self.assertEqual(r.json()["data"]["ok"], True)
		self.assertEqual(r.json()["data"]["http_status"], 200)
		self.assertEqual(request.call_count, 1)
		self.assertEqual(OrderActionRun.objects.filter(action=self.action).count(), 1)

	def test_run_endpoint_409_when_already_sent(self):
		self.run_all(orders=[self.orders[0]], responses=[OK])
		admin = User.objects.create_user(username="x@x.test", email="x@x.test", password="x")
		ProjectUser.objects.create(project=self.project, user=admin, role="member")
		client = APIClient()
		client.force_authenticate(user=admin)
		r = client.post(
			f"/api/orders/actions/{self.action.id}/run/",
			{"order_ids": [str(o.id) for o in self.orders]}, format="json",
		)
		self.assertEqual(r.status_code, 409, r.content)
		self.assertEqual(r.json()["error"]["details"], ["1001"])  # the frontend reads details as a list
		self.assertEqual(OrderActionRun.objects.count(), 1)  # nothing queued, not even the other two orders

	def test_queued_run_counts_as_sent(self):
		OrderActionRun.objects.create(action=self.action, order=self.orders[0], status="queued")
		result, request = self.run_all(orders=[self.orders[0]], responses=[])
		self.assertEqual(result["already_sent"], [1001])
		self.assertEqual(result["queued"], [])
		request.assert_not_called()

	def test_claim_skips_run_that_is_not_queued(self):
		done = OrderActionRun.objects.create(action=self.action, order=self.orders[0], status="succeeded")
		with patch("orders.actions.safe_request") as request:
			execute_run(done.id)
		request.assert_not_called()

	def test_cancelled_during_call_keeps_cancelled(self):
		def cancel_then_ok(*args, **kwargs):
			Order.objects.filter(pk=self.orders[0].pk).update(status="cancelled")
			return OK
		self.run_all(orders=[self.orders[0]], responses=cancel_then_ok)
		self.orders[0].refresh_from_db()
		self.assertEqual(self.orders[0].status, "cancelled")
		self.assertEqual(self.orders[0].tracking_number, "T123")
		run = OrderActionRun.objects.get(order=self.orders[0])
		self.assertIn("cancelled", run.error)

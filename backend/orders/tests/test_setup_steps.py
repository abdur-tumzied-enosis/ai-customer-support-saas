from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Project, ProjectUser
from knowledge.models import Document, KnowledgeBase, ProductCandidate
from orders.models import OrderSettings
from orders.setup import SetupBusy, _run_step, run_setup

User = get_user_model()
STATUS_URL = "/api/orders/setup/"
RUN_URL = "/api/orders/setup/run/"
REPORT = {"found": {"zones": 1, "payment_methods": 0, "product_candidates": 0}, "not_found": ["payment methods"]}


class StepsTestBase(TestCase):
	def setUp(self):
		self.project = Project.objects.create(name="P", slug="p-steps")
		self.kb = KnowledgeBase.objects.create(project=self.project, name="Products", vector_collection="vc-steps")
		Document.objects.create(kb=self.kb, source_type="url", page_url="https://shop.test/d", markdown_content="Dhaka 60 taka.")
		self.pid, self.kid = str(self.project.id), str(self.kb.id)
		self.dispatch = patch("orders.setup._dispatch").start()
		self.extract = patch("orders.setup.extract_candidates", return_value=2).start()
		self.delivery = patch("orders.setup.extract_order_settings", return_value=REPORT).start()
		self.addCleanup(patch.stopall)

	def row(self):
		return OrderSettings.objects.get(project=self.project)

	def step(self, key):
		return next(s for s in self.row().setup_steps if s["key"] == key)

	def set_steps(self, **states):
		OrderSettings.objects.update_or_create(project=self.project, defaults={"setup_steps": [
			{"key": k, "label": k, "status": states.get(k, {}).get("status", "pending"),
			 "detail": "", "error_code": "", "error": "", "started_at": None, "finished_at": None,
			 "heartbeat_at": states.get(k, {}).get("heartbeat_at")}
			for k in ("content", "products", "delivery")
		]})


class StepJobTest(StepsTestBase):
	def test_restart_creates_three_steps_and_claims_the_first(self):
		run_setup(self.pid, self.kid, mode="restart")
		self.assertEqual([s["key"] for s in self.row().setup_steps], ["content", "products", "delivery"])
		self.assertEqual([s["status"] for s in self.row().setup_steps], ["running", "pending", "pending"])
		self.dispatch.assert_called_once_with(self.pid, self.kid, "content")

	def test_second_run_refused_right_after_the_first_is_queued(self):
		run_setup(self.pid, self.kid, mode="restart")
		with self.assertRaises(SetupBusy):
			run_setup(self.pid, self.kid, mode="resume")

	def test_steps_advance_in_order_to_ready(self):
		run_setup(self.pid, self.kid, mode="restart")
		_run_step(self.pid, self.kid, "content")
		self.dispatch.assert_called_with(self.pid, self.kid, "products")
		_run_step(self.pid, self.kid, "products")
		self.dispatch.assert_called_with(self.pid, self.kid, "delivery")
		_run_step(self.pid, self.kid, "delivery")
		self.assertEqual(self.row().setup_status, "ready")
		self.assertEqual(self.dispatch.call_count, 3)  # the last step dispatches nothing

	def test_failure_stops_the_run_and_hides_the_raw_error(self):
		self.set_steps()
		self.extract.side_effect = RuntimeError("secret internal detail")
		_run_step(self.pid, self.kid, "content")
		_run_step(self.pid, self.kid, "products")
		step = self.step("products")
		self.assertEqual(step["status"], "failed")
		self.assertEqual(step["error_code"], "step_error")
		self.assertNotIn("secret internal detail", step["error"])
		self.assertEqual(self.row().setup_status, "failed")
		self.assertEqual(self.dispatch.call_count, 1)  # only content dispatched products; nothing after the failure

	def test_resume_skips_done_steps(self):
		self.set_steps()
		_run_step(self.pid, self.kid, "content")
		self.extract.side_effect = RuntimeError("boom")
		_run_step(self.pid, self.kid, "products")
		self.dispatch.reset_mock()
		run_setup(self.pid, self.kid, mode="resume")
		self.dispatch.assert_called_once_with(self.pid, self.kid, "products")
		self.assertEqual(self.step("content")["status"], "done")

	def test_restart_resets_every_step(self):
		self.set_steps(content={"status": "done"}, products={"status": "failed"})
		run_setup(self.pid, self.kid, mode="restart")
		self.assertEqual([s["status"] for s in self.row().setup_steps], ["running", "pending", "pending"])
		self.assertEqual([s["detail"] for s in self.row().setup_steps], ["", "", ""])
		self.dispatch.assert_called_with(self.pid, self.kid, "content")

	def test_retry_does_not_duplicate_candidates(self):
		self.set_steps()
		ProductCandidate.objects.create(kb=self.kb, name="Old", payload={})
		def fake_extract(kb, *args, **kwargs):
			ProductCandidate.objects.create(kb=kb, name="New", payload={})
			return 1
		self.extract.side_effect = fake_extract
		_run_step(self.pid, self.kid, "products")
		_run_step(self.pid, self.kid, "products")  # a retry
		self.assertEqual(ProductCandidate.objects.filter(kb=self.kb, status="pending").count(), 1)

	def test_restart_keeps_imported_products_and_saved_zones(self):
		ProductCandidate.objects.create(kb=self.kb, name="Imported", payload={}, status="imported")
		OrderSettings.objects.create(project=self.project, zones=[{"name": "Mine", "charge": "10"}])
		run_setup(self.pid, self.kid, mode="restart")
		self.assertTrue(ProductCandidate.objects.filter(kb=self.kb, status="imported").exists())
		self.assertEqual(self.row().zones, [{"name": "Mine", "charge": "10"}])

	def test_content_step_needs_input_without_documents(self):
		Document.objects.all().delete()
		self.set_steps()
		_run_step(self.pid, self.kid, "content")
		step = self.step("content")
		self.assertEqual(step["status"], "needs_input")
		self.assertEqual(step["error_code"], "no_content")
		self.dispatch.assert_not_called()

	def test_products_step_heartbeats_each_page(self):
		self.set_steps()
		def fake_extract(kb, document_ids=None, on_page=None):
			on_page(1)
			self.assertIsNotNone(self.step("products")["heartbeat_at"])
			return 0
		self.extract.side_effect = fake_extract
		_run_step(self.pid, self.kid, "products")
		self.assertEqual(self.step("products")["status"], "done")


class StepStatusApiTest(StepsTestBase):
	def setUp(self):
		super().setUp()
		self.admin = User.objects.create_user(username="a@x.test", email="a@x.test", password="x")
		ProjectUser.objects.create(project=self.project, user=self.admin, role="admin")
		self.client = APIClient()
		self.client.force_authenticate(user=self.admin)

	def test_stale_heartbeat_reads_as_interrupted(self):
		stale = timezone.now() - timedelta(minutes=10)
		self.set_steps(content={"status": "done"}, products={"status": "running", "heartbeat_at": stale.isoformat()})
		data = self.client.get(f"{STATUS_URL}?project_id={self.pid}").json()["data"]
		self.assertEqual(data["status"], "failed")
		products = next(s for s in data["steps"] if s["key"] == "products")
		self.assertEqual(products["status"], "failed")
		self.assertEqual(products["error_code"], "interrupted")

	def test_second_run_refused_while_a_step_is_fresh(self):
		fresh = timezone.now().isoformat()
		self.set_steps(content={"status": "running", "heartbeat_at": fresh})
		with self.assertRaises(SetupBusy):
			run_setup(self.pid, self.kid, mode="resume")
		r = self.client.post(RUN_URL, {"project_id": self.pid, "kb_id": self.kid}, format="json")
		self.assertEqual(r.status_code, 409, r.content)
		self.assertEqual(r.json()["error"]["code"], "setup_running")

	def test_run_rejects_unknown_mode(self):
		r = self.client.post(RUN_URL, {"project_id": self.pid, "kb_id": self.kid, "mode": "bogus"}, format="json")
		self.assertEqual(r.status_code, 400, r.content)

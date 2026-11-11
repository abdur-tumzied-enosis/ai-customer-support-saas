from decimal import Decimal
from agents.embeddings_api import Embedded
from io import BytesIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import TestCase
from PIL import Image
from rest_framework.test import APIClient

from accounts.models import Project, ProjectUser
from billing import service
from billing.models import ActionPrice, CreditTransaction
from core.tests.utils import fresh_storage
from knowledge.models import KnowledgeBase, Product, ProductImage, product_image_path
from knowledge.tasks import embed_product_image
from orders.improve import NoSafeSuggestion
from orders.setup import _delivery, _mutate_steps, _fresh_steps

User = get_user_model()
FOUND = {"found": {"zones": 1, "payment_methods": 1}}


@fresh_storage
class OwnerActionBillingTest(TestCase):
	def setUp(self):
		self.user = User.objects.create_user(username="o@example.com", email="o@example.com", password="x")
		self.project = Project.objects.create(name="P", slug="p-owner-act")
		ProjectUser.objects.create(project=self.project, user=self.user, role="admin")
		self.kb = KnowledgeBase.objects.create(project=self.project, name="default", is_default=True, vector_collection="vc-oa")
		self.pid, self.kid = str(self.project.id), str(self.kb.id)
		self.client = APIClient()
		self.client.force_authenticate(user=self.user)
		self.addCleanup(mock.patch.stopall)

	def price(self, action, credits):
		ActionPrice.objects.update_or_create(action=action, defaults={"credits": Decimal(credits)})

	def fund(self, amount, key="seed"):
		service.grant(self.project, Decimal(amount), "adjust", key=key)

	def count(self, action):
		return CreditTransaction.objects.filter(action=action).count()

	def start_step(self, started_at):
		def change(steps):
			for s in steps:
				if s["key"] == "delivery":
					s["started_at"] = started_at
			return steps
		_mutate_steps(self.pid, lambda steps: change(steps if steps else _fresh_steps()))

	def test_setup_run_402_when_broke(self):
		self.price("order_setup", 5)
		self.fund(4)
		with mock.patch("orders.api.run_setup") as run:
			r = self.client.post("/api/orders/setup/run/", {"project_id": self.pid, "kb_id": self.kid}, format="json")
		self.assertEqual(r.status_code, 402)
		self.assertEqual(r.json()["error"]["code"], "insufficient_credits")
		run.assert_not_called()

	def test_delivery_step_charges_once(self):
		self.price("order_setup", 5)
		self.fund(100)
		with mock.patch("orders.setup.extract_order_settings", return_value=FOUND):
			self.start_step("2026-01-01T00:00:00+00:00")
			_delivery(self.pid, self.kid, lambda t: None)
			_delivery(self.pid, self.kid, lambda t: None)
			self.assertEqual(self.count("order_setup"), 1)
			self.start_step("2026-01-02T00:00:00+00:00")
			_delivery(self.pid, self.kid, lambda t: None)
		self.assertEqual(self.count("order_setup"), 2)
		self.assertEqual(service.balance_of(self.project), Decimal("90"))

	def test_delivery_step_needs_input_when_broke(self):
		self.price("order_setup", 5)
		with mock.patch("orders.setup.extract_order_settings") as extract:
			result = _delivery(self.pid, self.kid, lambda t: None)
		self.assertEqual(result[0], "needs_input")
		self.assertEqual(result[2], "insufficient_credits")
		extract.assert_not_called()

	def test_improve_charged_on_success_only(self):
		self.price("instruction_improve", 1)
		body = {"project_id": self.pid, "text": "Confirm.", "scope": "order"}
		r = self.client.post("/api/kb/instructions/improve/", body, format="json")
		self.assertEqual(r.status_code, 402)
		self.fund(10)
		with mock.patch("orders.api.improve", side_effect=NoSafeSuggestion()):
			r = self.client.post("/api/kb/instructions/improve/", body, format="json")
		self.assertEqual(r.status_code, 422)
		self.assertEqual(self.count("instruction_improve"), 0)
		with mock.patch("orders.api.improve", return_value={"suggestion": "x", "why": "y"}):
			r = self.client.post("/api/kb/instructions/improve/", body, format="json")
		self.assertEqual(r.status_code, 200)
		self.assertEqual(self.count("instruction_improve"), 1)

	def test_suggest_charged_on_success_only(self):
		self.price("product_suggest", 1)
		url = "/api/kb/products/sources/suggest-mapping/"
		body = {"project_id": self.pid, "kb_id": self.kid, "headers": ["Name"], "sample": [{"Name": "Shoe"}]}
		r = self.client.post(url, body, format="json")
		self.assertEqual(r.status_code, 402)
		self.fund(10)
		with mock.patch("knowledge.catalog_views.suggest_mapping", side_effect=RuntimeError("no model")):
			r = self.client.post(url, body, format="json")
		self.assertEqual(r.status_code, 503)
		self.assertEqual(self.count("product_suggest"), 0)
		with mock.patch("knowledge.catalog_views.suggest_mapping", return_value=[]):
			r = self.client.post(url, body, format="json")
		self.assertEqual(r.status_code, 200)
		self.assertEqual(self.count("product_suggest"), 1)

	def test_owner_action_after_check_can_go_negative(self):
		self.price("order_setup", 5)
		self.fund(5)

		def drop(*a, **k):
			service.grant(self.project, Decimal("-3"), "adjust", key="drop")
			return FOUND

		self.start_step("2026-01-01T00:00:00+00:00")
		with mock.patch("orders.setup.extract_order_settings", side_effect=drop):
			result = _delivery(self.pid, self.kid, lambda t: None)
		self.assertEqual(result[0], "done")
		self.assertEqual(service.balance_of(self.project), Decimal("-3"))

	def test_photo_index_charged_even_below_zero(self):
		self.price("photo_index", 1)
		product = Product.objects.create(kb=self.kb, name="Shoe")
		image = ProductImage(product=product)
		buf = BytesIO()
		Image.new("RGB", (20, 10), "red").save(buf, "PNG")
		image.file.name = default_storage.save("tmp/" + product_image_path(self.kb, str(image.id), "png"), ContentFile(buf.getvalue()))
		image.save()
		mock.patch("knowledge.tasks.embed_image", return_value=Embedded([[1, 0, 0, 0]], 1200, Decimal("0.00048"), "cohere/embed-5-fast")).start()
		mock.patch("knowledge.tasks.upsert_image").start()
		mock.patch("knowledge.models.delete_images").start()
		embed_product_image(str(image.id))
		image.refresh_from_db()
		self.assertEqual(image.status, "ready")
		self.assertEqual(service.balance_of(self.project), Decimal("-1"))

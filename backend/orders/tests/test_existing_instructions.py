from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Project, ProjectUser
from knowledge.models import CustomInstruction
from orders.guard import Verdict
from orders.instructions import find_order_instructions

User = get_user_model()


class ExistingInstructionsTest(TestCase):
	def setUp(self):
		self.user = User.objects.create_user(username="i@x.test", email="i@x.test", password="x")
		self.project = Project.objects.create(name="P", slug="p-existing")
		ProjectUser.objects.create(project=self.project, user=self.user, role="admin")
		self.order_text = CustomInstruction.objects.create(
			project=self.project, label="Take orders", message="When a customer orders, confirm the address and phone.",
		)
		self.polite = CustomInstruction.objects.create(project=self.project, label="Polite", message="Be polite.")
		self.client = APIClient()
		self.client.force_authenticate(user=self.user)

	def test_review_finds_order_taking_text(self):
		found = [i.label for i in find_order_instructions(self.project)]
		self.assertEqual(found, ["Take orders"])

	def test_convert_sets_order_scope(self):
		with patch("orders.api.check_instruction", return_value=Verdict(blocked=False)):  # no model call in this test
			r = self.client.post("/api/orders/instructions/resolve/", {
				"project_id": str(self.project.id), "ids": [str(self.order_text.id)], "action": "convert",
			}, format="json")
		self.assertEqual(r.status_code, 200, r.content)
		self.order_text.refresh_from_db()
		self.assertEqual(self.order_text.scope, "order")

	def test_turn_off_sets_off_scope_and_keeps_row(self):
		r = self.client.post("/api/orders/instructions/resolve/", {
			"project_id": str(self.project.id), "ids": [str(self.order_text.id)], "action": "turn_off",
		}, format="json")
		self.assertEqual(r.status_code, 200, r.content)
		self.order_text.refresh_from_db()
		self.assertEqual(self.order_text.scope, "off")

	def test_no_delete(self):
		self.client.post("/api/orders/instructions/resolve/", {
			"project_id": str(self.project.id), "ids": [str(self.order_text.id)], "action": "turn_off",
		}, format="json")
		self.assertEqual(CustomInstruction.objects.filter(project=self.project).count(), 2)

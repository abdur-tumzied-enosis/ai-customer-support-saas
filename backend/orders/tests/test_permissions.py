from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Project, ProjectUser
from channels_app.models import Conversation, CustomerContact
from knowledge.models import KnowledgeBase
from orders.models import Order, OrderSettings

User = get_user_model()


class RolePermissionsTest(TestCase):
	"""Members may view, edit orders and run actions. Admins change settings, instructions, and ordering on or off."""

	def setUp(self):
		self.admin = User.objects.create_user(username="a@x.test", email="a@x.test", password="x")
		self.member = User.objects.create_user(username="m@x.test", email="m@x.test", password="x")
		self.project = Project.objects.create(name="P", slug="p-perms")
		ProjectUser.objects.create(project=self.project, user=self.admin, role="admin")
		ProjectUser.objects.create(project=self.project, user=self.member, role="member")
		self.kb = KnowledgeBase.objects.create(project=self.project, name="Products", vector_collection="vc-perms")
		OrderSettings.objects.create(project=self.project)
		self.pid = str(self.project.id)
		self.client = APIClient()
		self.client.force_authenticate(user=self.member)

	def test_member_cannot_change_settings(self):
		r = self.client.patch(f"/api/orders/settings/?project_id={self.pid}", {"draft_expiry_hours": 24}, format="json")
		self.assertEqual(r.status_code, 403)

	def test_member_cannot_enable_or_disable(self):
		self.assertEqual(self.client.post(f"/api/orders/enable/?project_id={self.pid}").status_code, 403)
		self.assertEqual(self.client.post(f"/api/orders/disable/?project_id={self.pid}").status_code, 403)

	def test_member_cannot_set_test_mode(self):
		r = self.client.post(f"/api/orders/test-mode/?project_id={self.pid}", {"on": True}, format="json")
		self.assertEqual(r.status_code, 403)

	def test_member_cannot_run_setup(self):
		r = self.client.post("/api/orders/setup/run/", {"project_id": self.pid, "kb_id": str(self.kb.id)}, format="json")
		self.assertEqual(r.status_code, 403)

	def test_member_cannot_resolve_instructions(self):
		r = self.client.post("/api/orders/instructions/resolve/", {
			"project_id": self.pid, "ids": [], "action": "turn_off",
		}, format="json")
		self.assertEqual(r.status_code, 403)

	def test_member_cannot_improve_instructions(self):
		r = self.client.post("/api/kb/instructions/improve/", {"project_id": self.pid, "text": "Confirm.", "scope": "order"}, format="json")
		self.assertEqual(r.status_code, 403)

	def test_member_cannot_export(self):
		r = self.client.get(f"/api/orders/export.csv?project_id={self.pid}")
		self.assertEqual(r.status_code, 403)

	def test_member_can_edit_order_and_bulk_status(self):
		contact = CustomerContact.objects.create(project=self.project, platform="facebook", external_id="p1")
		conv = Conversation.objects.create(project=self.project, contact=contact, knowledge_base=self.kb)
		order = Order.objects.create(
			project=self.project, conversation=conv, channel="facebook", currency="BDT", status="new", number=1001,
			zone_charge=Decimal("0"), total=Decimal("0"),
		)
		r = self.client.patch(f"/api/orders/{order.id}/", {"internal_note": "call before delivery"}, format="json")
		self.assertEqual(r.status_code, 200, r.content)
		r = self.client.post("/api/orders/bulk-status/", {"ids": [str(order.id)], "status": "confirmed"}, format="json")
		self.assertEqual(r.status_code, 200, r.content)

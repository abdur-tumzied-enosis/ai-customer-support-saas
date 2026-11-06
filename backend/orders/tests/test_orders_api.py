from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Project, ProjectUser
from channels_app.models import Conversation, CustomerContact
from knowledge.models import KnowledgeBase, Product
from orders.models import Order, OrderSettings, OrderStatusHistory
from orders.service import apply_lines, get_or_create_draft, submit_order, update_customer

User = get_user_model()
CUSTOMER = {"customer_name": "Rahim", "customer_phone": "+8801712345678", "customer_address": "Road 1", "zone_name": "Dhaka"}


class OrdersApiTest(TestCase):
	def setUp(self):
		self.admin = User.objects.create_user(username="a@x.test", email="a@x.test", password="x")
		self.member = User.objects.create_user(username="m@x.test", email="m@x.test", password="x")
		self.project = Project.objects.create(name="P", slug="p-orders-api")
		ProjectUser.objects.create(project=self.project, user=self.admin, role="admin")
		ProjectUser.objects.create(project=self.project, user=self.member, role="member")
		self.kb = KnowledgeBase.objects.create(project=self.project, name="Products", vector_collection="vc-api")
		self.product = Product.objects.create(kb=self.kb, name="Shirt", external_id="S1", price=Decimal("250"), status="active")
		self.settings = OrderSettings.objects.create(
			project=self.project, enabled=True, channels=["facebook"],
			zones=[{"name": "Dhaka", "charge": "60"}], form_fields={"optional": [], "custom": []},
		)
		self.client = APIClient()

	def conversation(self, project=None, platform="facebook"):
		project = project or self.project
		contact = CustomerContact.objects.create(project=project, platform=platform, external_id=f"c-{Conversation.objects.count()}-{platform}")
		kb = self.kb if project == self.project else KnowledgeBase.objects.create(project=project, name="K", vector_collection=f"vc-{project.slug}")
		return Conversation.objects.create(project=project, contact=contact, knowledge_base=kb)

	def submitted(self, qty=2):
		conv = self.conversation()
		order = get_or_create_draft(conv, self.settings)
		apply_lines(order, [{"product_id": str(self.product.id), "quantity": qty}], self.settings)
		update_customer(order, CUSTOMER, self.settings)
		return submit_order(order, ("user", "setup"))

	def test_list_excludes_drafts_and_abandoned_by_default(self):
		self.submitted()
		get_or_create_draft(self.conversation(), self.settings)  # a draft
		Order.objects.create(project=self.project, conversation=self.conversation(), channel="facebook", currency="BDT", status="abandoned")
		self.client.force_authenticate(user=self.admin)
		r = self.client.get("/api/orders/", {"project_id": str(self.project.id)})
		self.assertEqual(r.json()["data"]["count"], 1, r.content)
		r = self.client.get("/api/orders/", {"project_id": str(self.project.id), "status": "draft"})
		self.assertEqual(r.json()["data"]["count"], 1, r.content)

	def test_filter_by_channel_and_date(self):
		self.submitted()
		self.client.force_authenticate(user=self.admin)
		q = {"project_id": str(self.project.id)}
		self.assertEqual(self.client.get("/api/orders/", {**q, "channel": "facebook"}).json()["data"]["count"], 1)
		self.assertEqual(self.client.get("/api/orders/", {**q, "channel": "whatsapp"}).json()["data"]["count"], 0)
		tomorrow = (date.today() + timedelta(days=1)).isoformat()
		self.assertEqual(self.client.get("/api/orders/", {**q, "date_from": tomorrow}).json()["data"]["count"], 0)

	def test_patch_items_recomputes_totals(self):
		order = self.submitted(qty=2)  # 500 + 60 zone
		self.client.force_authenticate(user=self.admin)
		r = self.client.patch(f"/api/orders/{order.id}/", {"items": [{"product_id": str(self.product.id), "quantity": 3}]}, format="json")
		self.assertEqual(r.status_code, 200, r.content)
		self.assertEqual(r.json()["data"]["total"], "810.00")

	def test_patch_needs_price_line_rejected(self):
		order = self.submitted(qty=2)
		Product.objects.filter(pk=self.product.pk).update(price=None, status="needs_info")
		self.client.force_authenticate(user=self.admin)
		r = self.client.patch(f"/api/orders/{order.id}/", {"items": [{"product_id": str(self.product.id), "quantity": 5}]}, format="json")
		self.assertEqual(r.status_code, 422, r.content)
		self.assertIn("needs_price", [p["code"] for p in r.json()["error"]["problems"]])
		self.assertEqual(order.items.get().quantity, 2)

	def test_advance_paid_over_total_rejected(self):
		order = self.submitted(qty=2)
		self.client.force_authenticate(user=self.admin)
		r = self.client.patch(f"/api/orders/{order.id}/", {"advance_paid": "99999"}, format="json")
		self.assertEqual(r.status_code, 422, r.content)

	def test_status_change_writes_history_user(self):
		order = self.submitted()
		self.client.force_authenticate(user=self.admin)
		r = self.client.patch(f"/api/orders/{order.id}/", {"status": "confirmed"}, format="json")
		self.assertEqual(r.status_code, 200, r.content)
		row = OrderStatusHistory.objects.filter(order=order, to_status="confirmed").get()
		self.assertEqual(row.actor_type, "user")
		self.assertEqual(row.actor_id, str(self.admin.id))

	def test_bulk_status_per_order_results(self):
		mine_a, mine_b = self.submitted(), self.submitted()
		other = Project.objects.create(name="Other", slug="p-other-orders")
		other_order = Order.objects.create(project=other, conversation=self.conversation(other), channel="facebook", currency="BDT", status="new", number=9001)
		self.client.force_authenticate(user=self.member)
		r = self.client.post("/api/orders/bulk-status/", {
			"ids": [str(mine_a.id), str(mine_b.id), str(other_order.id)], "status": "shipped",
		}, format="json")
		self.assertEqual(r.status_code, 200, r.content)
		results = {row["id"]: row for row in r.json()["data"]["results"]}
		self.assertTrue(results[str(mine_a.id)]["ok"])
		self.assertTrue(results[str(mine_b.id)]["ok"])
		self.assertFalse(results[str(other_order.id)]["ok"])

	def test_staff_cannot_set_draft_status(self):
		order = self.submitted()
		self.client.force_authenticate(user=self.admin)
		r = self.client.patch(f"/api/orders/{order.id}/", {"status": "draft"}, format="json")
		self.assertEqual(r.status_code, 422, r.content)
		self.assertEqual(r.json()["error"]["code"], "invalid_status_change")

	def test_draft_cannot_jump_to_new_without_submit(self):
		draft = get_or_create_draft(self.conversation(), self.settings)  # no number yet: it was never submitted
		self.client.force_authenticate(user=self.admin)
		r = self.client.patch(f"/api/orders/{draft.id}/", {"status": "new"}, format="json")
		self.assertEqual(r.status_code, 422, r.content)
		self.assertEqual(r.json()["error"]["code"], "invalid_status_change")
		bulk = self.client.post("/api/orders/bulk-status/", {"ids": [str(draft.id)], "status": "new"}, format="json")
		self.assertFalse(bulk.json()["data"]["results"][0]["ok"])
		draft.refresh_from_db()
		self.assertEqual(draft.status, "draft")

	def test_filter_by_conversation(self):
		order = self.submitted()
		self.submitted()
		self.client.force_authenticate(user=self.admin)
		r = self.client.get("/api/orders/", {"project_id": str(self.project.id), "conversation_id": str(order.conversation_id)})
		self.assertEqual(r.json()["data"]["count"], 1, r.content)

	def test_two_staff_edits_both_in_history(self):
		order = self.submitted()
		self.client.force_authenticate(user=self.admin)
		self.client.patch(f"/api/orders/{order.id}/", {"status": "confirmed"}, format="json")
		self.client.force_authenticate(user=self.member)
		self.client.patch(f"/api/orders/{order.id}/", {"status": "shipped"}, format="json")
		order.refresh_from_db()
		self.assertEqual(order.status, "shipped")  # last write wins
		self.assertEqual(
			OrderStatusHistory.objects.filter(order=order, actor_id__in=[str(self.admin.id), str(self.member.id)]).count(), 2,
		)

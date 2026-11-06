import csv
import io
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Project, ProjectUser
from channels_app.models import Conversation, CustomerContact
from knowledge.models import KnowledgeBase, Product
from orders.export import HEADER
from orders.models import OrderSettings
from orders.service import apply_lines, get_or_create_draft, submit_order, update_customer

User = get_user_model()
CUSTOMER = {"customer_name": "Rahim", "customer_phone": "+8801712345678", "customer_address": "Road 1", "zone_name": "Dhaka"}


class ExportTest(TestCase):
	def setUp(self):
		self.user = User.objects.create_user(username="e@x.test", email="e@x.test", password="x")
		self.project = Project.objects.create(name="P", slug="p-export")
		ProjectUser.objects.create(project=self.project, user=self.user, role="admin")
		self.kb = KnowledgeBase.objects.create(project=self.project, name="Products", vector_collection="vc-export")
		self.product = Product.objects.create(kb=self.kb, name="Shirt", external_id="S1", price=Decimal("250"), status="active")
		self.settings = OrderSettings.objects.create(
			project=self.project, enabled=True, channels=["facebook", "whatsapp"],
			zones=[{"name": "Dhaka", "charge": "60"}], form_fields={"optional": [], "custom": []},
		)
		self.client = APIClient()
		self.client.force_authenticate(user=self.user)

	def submit(self, channel="facebook", customer=CUSTOMER):
		contact = CustomerContact.objects.create(project=self.project, platform=channel, external_id=f"e-{channel}-{CustomerContact.objects.count()}")
		conv = Conversation.objects.create(project=self.project, contact=contact, knowledge_base=self.kb)
		order = get_or_create_draft(conv, self.settings)
		apply_lines(order, [{"product_id": str(self.product.id), "quantity": 2}], self.settings)
		update_customer(order, customer, self.settings)
		return submit_order(order, ("user", "setup"))

	def export(self, **params):
		r = self.client.get("/api/orders/export.csv", {"project_id": str(self.project.id), **params})
		self.assertEqual(r.status_code, 200, r.content)
		return r, list(csv.reader(io.StringIO(r.content.decode())))

	def test_csv_headers_and_row(self):
		self.submit()
		r, rows = self.export()
		self.assertTrue(r["Content-Type"].startswith("text/csv"))
		self.assertEqual(rows[0], HEADER)
		row = dict(zip(HEADER, rows[1]))
		self.assertEqual(row["number"], "1001")
		self.assertEqual(row["items"], "2 x Shirt (S1)")
		self.assertEqual(row["total"], "560.00")

	def test_formula_cell_prefixed(self):
		self.submit(customer={**CUSTOMER, "customer_name": "=HYPERLINK(\"http://x\")"})
		_, rows = self.export()
		self.assertEqual(dict(zip(HEADER, rows[1]))["customer name"], "'=HYPERLINK(\"http://x\")")

	def test_export_respects_filters(self):
		self.submit(channel="facebook")
		self.submit(channel="whatsapp")
		_, rows = self.export(channel="whatsapp")
		self.assertEqual(len(rows), 2)  # header plus one order

import io
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import Project, ProjectUser
from knowledge.models import KnowledgeBase, Product, ProductSource
from knowledge.product_sources import guess_mapping

User = get_user_model()
BASE = "/api/kb/products/sources/"


def csv_file(text, name="p.csv"):
	return SimpleUploadedFile(name, text.encode("utf-8"), content_type="text/csv")


@override_settings(IMAGE_SEARCH_ENABLED=False)
class ProductSourceTests(TestCase):
	def setUp(self):
		self.user = User.objects.create_user(username="a@example.com", email="a@example.com", password="x")
		self.project = Project.objects.create(name="P", slug="p-src")
		ProjectUser.objects.create(project=self.project, user=self.user, role="admin")
		self.kb = KnowledgeBase.objects.create(project=self.project, name="default", is_default=True)
		self.client = APIClient()
		self.client.force_authenticate(user=self.user)

	def body(self, file, mapping=None):
		data = {"project_id": str(self.project.id), "kb_id": str(self.kb.id), "file": file}
		if mapping is not None:
			data["mapping"] = json.dumps(mapping)
		return data

	def upload(self, text, mapping=None, name="p.csv"):
		return self.client.post(BASE + "csv/", self.body(csv_file(text, name), mapping), format="multipart")

	def test_guess_mapping_bengali_headers(self):
		m = guess_mapping(["পণ্য কোড", "নাম", "দাম"])
		self.assertEqual((m["external_id"], m["name"], m["price"]), ("পণ্য কোড", "নাম", "দাম"))

	def test_preview_returns_headers_mapping_sample(self):
		rows = "\n".join(f"{i},Shoe{i},{i}00" for i in range(8))
		r = self.client.post(BASE + "csv/preview/", self.body(csv_file("id,name,price\n" + rows)), format="multipart")
		self.assertEqual(r.status_code, 200, r.content)
		d = r.json()["data"]
		self.assertEqual(d["headers"], ["id", "name", "price"])
		self.assertEqual(d["mapping"]["price"], "price")
		self.assertEqual(len(d["sample"]), 5)
		self.assertFalse(d["key_warning"])

	def test_csv_creates_active_with_price(self):
		r = self.upload("id,name,price\nA1,Shoe,৳ 1,200\n".replace("৳ 1,200", '"৳ 1,200"'))
		self.assertEqual(r.status_code, 201, r.content)
		p = Product.objects.get(kb=self.kb, external_id="A1")
		self.assertEqual((p.status, str(p.price), p.source), ("active", "1200.00", "csv"))
		self.assertEqual(p.source_ref.kind, "csv")
		self.assertEqual(r.json()["data"]["result"]["created"], 1)

	def test_csv_row_without_price_is_needs_info(self):
		self.upload("id,name,price\nA1,Shoe,\nA2,Bag,abc\n")
		self.assertEqual(set(Product.objects.filter(kb=self.kb).values_list("status", flat=True)), {"needs_info"})

	def test_csv_clear_price_sends_to_needs_info_and_notifies(self):
		self.upload("id,name,price\nA1,Shoe,100\n")
		with patch("knowledge.catalog.queue_email") as q:
			self.upload("id,name,price\nA1,Shoe,\n")
		p = Product.objects.get(external_id="A1")
		self.assertEqual((p.price, p.status), (None, "needs_info"))
		q.assert_called_once()
		self.assertEqual(q.call_args[0][0], "a@example.com")
		self.assertIn("Shoe", q.call_args[0][2])

	def test_owner_locked_price_kept_on_sync(self):
		self.upload("id,name,price\nA1,Shoe,100\n")
		Product.objects.filter(external_id="A1").update(price="250", locked_fields=["price"])
		with patch("knowledge.catalog.queue_email") as q:
			self.upload("id,name,price\nA1,Shoe,\n")
			self.upload("id,name,price\nA1,Shoe,999\n")
		p = Product.objects.get(external_id="A1")
		self.assertEqual((str(p.price), p.status, p.locked_fields), ("250.00", "active", ["price"]))
		q.assert_not_called()

	def test_removed_row_hides_product(self):
		from django.utils import timezone

		from knowledge.product_sources import apply_rows

		self.upload("id,name,price\nA1,Shoe,100\nA2,Bag,50\n")
		source = ProductSource.objects.get(kb=self.kb)
		mapping = source.mapping
		res = apply_rows(self.kb, source, [{"id": "A1", "name": "Shoe", "price": "100"}], mapping, timezone.now())
		self.assertEqual(res["hidden"], 1)
		self.assertEqual(Product.objects.get(external_id="A2").status, "hidden")
		self.assertEqual(Product.objects.get(external_id="A1").status, "active")
		# a locked status is not hidden by removal
		Product.objects.filter(external_id="A1").update(locked_fields=["status"])
		res = apply_rows(self.kb, source, [], mapping, timezone.now())
		self.assertEqual(Product.objects.get(external_id="A1").status, "active")

	def test_duplicate_external_id_row_rejected(self):
		r = self.upload("id,name,price\nA1,Shoe,100\nA1,Other,200\n")
		self.assertEqual(r.status_code, 201, r.content)
		res = r.json()["data"]["result"]
		self.assertEqual(res["created"], 1)
		self.assertEqual(len(res["rejected"]), 1)
		self.assertEqual(res["rejected"][0]["row"], 3)
		self.assertTrue(res["rejected"][0]["message"])
		self.assertEqual(Product.objects.get(external_id="A1").name, "Shoe")

	def test_key_warning_when_no_key_column(self):
		r = self.client.post(BASE + "csv/preview/", self.body(csv_file("name,price\nShoe,100\n")), format="multipart")
		self.assertTrue(r.json()["data"]["key_warning"])

	def test_name_match_without_keys_updates_in_place(self):
		self.upload("name,price\nShoe,100\n")
		self.upload("name,price\nShoe,150\n")
		self.assertEqual(Product.objects.filter(kb=self.kb).count(), 1)
		self.assertEqual(str(Product.objects.get(kb=self.kb).price), "150.00")

	def test_corrupt_xlsx_is_400(self):
		f = SimpleUploadedFile("p.xlsx", b"not a zip", content_type="application/octet-stream")
		r = self.client.post(BASE + "csv/preview/", self.body(f), format="multipart")
		self.assertEqual(r.status_code, 400)
		self.assertEqual(r.json()["error"]["code"], "invalid_file")

	def test_xlsx_first_sheet(self):
		import openpyxl

		wb = openpyxl.Workbook()
		ws = wb.active
		ws.append(["sku", "name", "price"])
		ws.append(["S1", "Shoe", 100.0])
		buf = io.BytesIO()
		wb.save(buf)
		f = SimpleUploadedFile("p.xlsx", buf.getvalue())
		r = self.client.post(BASE + "csv/", self.body(f), format="multipart")
		self.assertEqual(r.status_code, 201, r.content)
		self.assertEqual(str(Product.objects.get(sku="S1").price), "100.00")

	def test_unsupported_file_type_is_400(self):
		r = self.upload("x", name="p.pdf")
		self.assertEqual(r.status_code, 400)
		self.assertEqual(r.json()["error"]["code"], "unsupported_file")

	def test_file_too_large_is_400(self):
		r = self.upload("id,name\n" + "a" * (5 * 1024 * 1024 + 1))
		self.assertEqual(r.status_code, 400)
		self.assertEqual(r.json()["error"]["code"], "file_too_large")

	def test_member_cannot_use_sources(self):
		ProjectUser.objects.filter(user=self.user).update(role="member")
		self.assertEqual(self.upload("id,name\nA,B\n").status_code, 403)
		r = self.client.get(BASE, {"project_id": str(self.project.id), "kb_id": str(self.kb.id)})
		self.assertEqual(r.status_code, 403)

	def test_list_sources(self):
		self.upload("id,name,price\nA1,Shoe,100\n")
		r = self.client.get(BASE, {"project_id": str(self.project.id), "kb_id": str(self.kb.id)})
		self.assertEqual(r.status_code, 200)
		self.assertEqual(len(r.json()["data"]), 1)
		self.assertEqual(r.json()["data"][0]["kind"], "csv")

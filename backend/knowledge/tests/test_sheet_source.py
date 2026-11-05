import json
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import Project, ProjectUser
from knowledge.models import KnowledgeBase, Product, ProductSource
from knowledge.product_sources import SheetNotPublic, fetch_sheet_csv, sheet_export_url
from knowledge.tasks import sync_product_source, sync_product_sources

User = get_user_model()
BASE = "/api/kb/products/sources/"
SHEET = "https://docs.google.com/spreadsheets/d/abc123/edit?usp=sharing#gid=77"
CSV = b"id,name,price\nA1,Shoe,100\nA2,Bag,50\n"


def resp(body=CSV, status=200, ctype="text/csv"):
	return MagicMock(status_code=status, content=body, headers={"Content-Type": ctype})


class SheetUrlTests(TestCase):
	def test_sheet_export_url_parses_id_and_gid(self):
		self.assertEqual(sheet_export_url(SHEET), "https://docs.google.com/spreadsheets/d/abc123/export?format=csv&gid=77")
		self.assertTrue(sheet_export_url("https://docs.google.com/spreadsheets/d/abc123/edit").endswith("&gid=0"))

	def test_other_host_rejected(self):
		for url in ("https://evil.example.com/spreadsheets/d/abc/edit", "http://docs.google.com/spreadsheets/d/abc",
				"https://docs.google.com.evil.com/spreadsheets/d/abc", "https://127.0.0.1/spreadsheets/d/abc"):
			with self.assertRaises(ValueError):
				sheet_export_url(url)

	def test_beat_schedule_has_sync(self):
		entry = settings.CELERY_BEAT_SCHEDULE["sync-product-sources"]
		self.assertEqual((entry["task"], entry["schedule"]), ("knowledge.tasks.sync_product_sources", 3600))

	def test_fetch_refuses_redirect_and_html(self):
		for r in (resp(b"", 302), resp(b"<!DOCTYPE html><html>", 200, "text/html; charset=utf-8")):
			with patch("knowledge.product_sources.requests.get", return_value=r) as g:
				with self.assertRaises(SheetNotPublic) as cm:
					fetch_sheet_csv("https://docs.google.com/x")
			self.assertEqual(str(cm.exception), "Share the sheet as 'Anyone with the link can view'.")
			self.assertEqual((g.call_args.kwargs["timeout"], g.call_args.kwargs["allow_redirects"]), (15, False))


@override_settings(IMAGE_SEARCH_ENABLED=False)
class SheetSourceTests(TestCase):
	def setUp(self):
		self.user = User.objects.create_user(username="a@example.com", email="a@example.com", password="x")
		self.member = User.objects.create_user(username="m@example.com", email="m@example.com", password="x")
		self.project = Project.objects.create(name="P", slug="p-sheet")
		ProjectUser.objects.create(project=self.project, user=self.user, role="admin")
		ProjectUser.objects.create(project=self.project, user=self.member, role="member")
		self.kb = KnowledgeBase.objects.create(project=self.project, name="default", is_default=True)
		self.client = APIClient()
		self.client.force_authenticate(user=self.user)

	def ids(self):
		return {"project_id": str(self.project.id), "kb_id": str(self.kb.id)}

	def create(self, **extra):
		return self.client.post(BASE + "sheet/", {**self.ids(), "sheet_url": SHEET, **extra}, format="json")

	def test_sheet_not_public_error(self):
		html = resp(b"<html>Sign in</html>", 200, "text/html")
		r = self.create_ok()
		src = ProductSource.objects.get()
		with patch("knowledge.product_sources.requests.get", return_value=html):
			out = self.client.post(f"{BASE}{src.id}/sync/", self.ids(), format="json")
		self.assertEqual(out.status_code, 400)
		self.assertEqual(out.json()["error"]["code"], "sheet_not_public")
		src.refresh_from_db()
		self.assertIn("sheet_not_public", src.last_error)
		self.assertIn("Anyone with the link can view", src.last_error)
		with patch("knowledge.product_sources.requests.get", return_value=html):
			r = self.create()
		self.assertEqual((r.status_code, r.json()["error"]["code"]), (400, "sheet_not_public"))

	def create_ok(self):
		with patch("knowledge.product_sources.requests.get", return_value=resp()):
			r = self.create()
		self.assertEqual(r.status_code, 201, r.content)
		return r

	def test_sync_uses_saved_mapping(self):
		self.create_ok()
		src = ProductSource.objects.get()
		self.assertEqual((src.kind, src.sheet_url), ("sheet", SHEET))
		self.assertEqual(src.mapping["price"], "price")
		with patch("knowledge.product_sources.requests.get", return_value=resp(b"id,name,price\nA1,Shoe,120\nA3,Hat,9\n")):
			result = sync_product_source(str(src.id))
		self.assertEqual((result["updated"], result["created"], result["hidden"]), (1, 1, 1))
		self.assertEqual(str(Product.objects.get(external_id="A1").price), "120.00")
		src.refresh_from_db()
		self.assertEqual(src.last_error, "")

	def test_sync_now_endpoint(self):
		self.create_ok()
		src = ProductSource.objects.get()
		with patch("knowledge.product_sources.requests.get", return_value=resp()):
			r = self.client.post(f"{BASE}{src.id}/sync/", self.ids(), format="json")
		self.assertEqual(r.status_code, 200, r.content)
		self.assertEqual(r.json()["data"]["result"]["unchanged"], 2)

	def test_sync_all_continues_after_failure(self):
		self.create_ok()
		bad = ProductSource.objects.create(kb=self.kb, kind="sheet", sheet_url="https://evil.example.com/x", mapping={"name": "name"})
		good = ProductSource.objects.exclude(id=bad.id).get()
		with patch("knowledge.product_sources.requests.get", return_value=resp()):
			sync_product_sources()
		bad.refresh_from_db()
		good.refresh_from_db()
		self.assertTrue(bad.last_error)
		self.assertIsNotNone(good.last_synced_at)

	def test_create_rejects_non_google_host(self):
		r = self.client.post(BASE + "sheet/", {**self.ids(), "sheet_url": "https://evil.example.com/spreadsheets/d/a"}, format="json")
		self.assertEqual((r.status_code, r.json()["error"]["code"]), (400, "invalid_url"))

	def test_delete_keeps_products(self):
		self.create_ok()
		src = ProductSource.objects.get()
		r = self.client.delete(f"{BASE}{src.id}/?project_id={self.project.id}&kb_id={self.kb.id}")
		self.assertEqual(r.status_code, 204)
		self.assertEqual(Product.objects.filter(kb=self.kb, source_ref__isnull=True).exclude(status="hidden").count(), 2)

	def test_member_gets_403(self):
		self.create_ok()
		src = ProductSource.objects.get()
		self.client.force_authenticate(user=self.member)
		with patch("knowledge.product_sources.requests.get", return_value=resp()):
			codes = [self.create().status_code,
				self.client.post(f"{BASE}{src.id}/sync/", self.ids(), format="json").status_code,
				self.client.delete(f"{BASE}{src.id}/?project_id={self.project.id}").status_code]
		self.assertEqual(codes, [403, 403, 403])

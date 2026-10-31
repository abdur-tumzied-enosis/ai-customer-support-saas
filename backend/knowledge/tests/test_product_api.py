import os
import shutil
import tempfile
from io import BytesIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image
from rest_framework.test import APIClient

from accounts.models import Project, ProjectUser
from knowledge.models import KnowledgeBase, Product, ProductImage

User = get_user_model()
MEDIA = tempfile.mkdtemp()


def png():
	buf = BytesIO()
	Image.new("RGB", (8, 8), "red").save(buf, "PNG")
	return buf.getvalue()


def upload(name="a.png", data=None):
	return SimpleUploadedFile(name, data if data is not None else png(), content_type="image/png")


def files_under_media():
	return [f for _, _, fs in os.walk(MEDIA) for f in fs]


@override_settings(IMAGE_SEARCH_ENABLED=True, MEDIA_ROOT=MEDIA)
class ProductApiTests(TestCase):
	@classmethod
	def tearDownClass(cls):
		super().tearDownClass()
		shutil.rmtree(MEDIA, ignore_errors=True)

	def setUp(self):
		self.user = User.objects.create_user(username="a@example.com", email="a@example.com", password="x")
		self.project = Project.objects.create(name="P", slug="p-img", image_search_allowed=True)
		self.pu = ProjectUser.objects.create(project=self.project, user=self.user, role="admin")
		self.kb = KnowledgeBase.objects.create(project=self.project, name="default", is_default=True)
		self.client = APIClient()
		self.client.force_authenticate(user=self.user)
		p = patch("knowledge.products.embed_product_image")
		self.embed = p.start()
		self.addCleanup(p.stop)

	def create(self, images=None, **extra):
		data = {"project_id": str(self.project.id), "kb_id": str(self.kb.id), "name": "Shoe", "description": "d",
			"images": images if images is not None else [upload("a.png"), upload("b.png")]}
		data.update(extra)
		return self.client.post("/api/kb/products/", data, format="multipart")

	def make_product(self):
		return self.create().json()["data"]

	def list_products(self):
		return self.client.get("/api/kb/products/", {"project_id": str(self.project.id), "kb_id": str(self.kb.id)})

	def test_create_product_with_images(self):
		with self.captureOnCommitCallbacks(execute=True):
			r = self.create()
		self.assertEqual(r.status_code, 201, r.content)
		self.assertEqual(ProductImage.objects.filter(status="pending").count(), 2)
		self.assertEqual(self.embed.delay.call_count, 2)
		self.assertEqual(len(r.json()["data"]["images"]), 2)

	def test_invalid_image_rejects_whole_upload(self):
		before = len(files_under_media())
		r = self.create(images=[upload("ok.png"), upload("bad.png", b"not an image")])
		self.assertEqual(r.status_code, 400)
		self.assertEqual(r.json()["error"]["code"], "invalid_image")
		self.assertIn("bad.png", r.json()["error"]["message"])
		self.assertEqual(Product.objects.count(), 0)
		self.assertEqual(len(files_under_media()), before)

	def test_too_many_images(self):
		r = self.create(images=[upload(f"{i}.png") for i in range(11)])
		self.assertEqual(r.status_code, 400)
		self.assertEqual(r.json()["error"]["code"], "too_many_images")
		self.assertEqual(Product.objects.count(), 0)

	def test_no_images_and_empty_name(self):
		self.assertEqual(self.create(images=[]).json()["error"]["code"], "no_images")
		self.assertEqual(self.create(name=" ").json()["error"]["code"], "invalid_name")

	def test_add_images_respects_total_limit(self):
		pid = self.make_product()["id"]
		r = self.client.post(f"/api/kb/products/{pid}/images/", {"images": [upload(f"{i}.png") for i in range(9)]}, format="multipart")
		self.assertEqual(r.json()["error"]["code"], "too_many_images")
		r = self.client.post(f"/api/kb/products/{pid}/images/", {"images": [upload("c.png")]}, format="multipart")
		self.assertEqual(r.status_code, 201)
		self.assertEqual(len(r.json()["data"]["images"]), 3)

	def test_list_shows_images_with_url(self):
		self.make_product()
		r = self.list_products()
		self.assertEqual(r.status_code, 200)
		prod = r.json()["data"][0]
		img = prod["images"][0]
		self.assertEqual(img["url"], f"/api/kb/products/images/{img['id']}/file/")
		self.assertEqual(img["status"], "pending")
		self.assertEqual(prod["name"], "Shoe")

	def test_patch_and_delete(self):
		pid = self.make_product()["id"]
		r = self.client.patch(f"/api/kb/products/{pid}/", {"name": "Boot"}, format="json")
		self.assertEqual(r.status_code, 200)
		self.assertEqual(Product.objects.get(id=pid).name, "Boot")
		img_id = ProductImage.objects.filter(product_id=pid).first().id
		r = self.client.delete(f"/api/kb/products/images/{img_id}/")
		self.assertEqual(r.status_code, 200)
		self.assertFalse(ProductImage.objects.filter(id=img_id).exists())
		r = self.client.delete(f"/api/kb/products/{pid}/")
		self.assertEqual(r.status_code, 200)
		self.assertEqual(Product.objects.count(), 0)

	def test_image_file_served_to_member(self):
		self.make_product()
		img = ProductImage.objects.first()
		r = self.client.get(f"/api/kb/products/images/{img.id}/file/")
		self.assertEqual(r.status_code, 200)
		self.assertEqual(b"".join(r.streaming_content), png())
		self.assertEqual(r["Content-Type"], "image/png")

	def test_other_project_product_is_404(self):
		pid = self.make_product()["id"]
		img = ProductImage.objects.first()
		other = User.objects.create_user(username="b@example.com", email="b@example.com", password="x")
		p2 = Project.objects.create(name="P2", slug="p2-img", image_search_allowed=True)
		ProjectUser.objects.create(project=p2, user=other, role="admin")
		c = APIClient()
		c.force_authenticate(user=other)
		self.assertEqual(c.get(f"/api/kb/products/images/{img.id}/file/").status_code, 404)
		self.assertEqual(c.patch(f"/api/kb/products/{pid}/", {"name": "x"}, format="json").status_code, 404)
		self.assertEqual(c.delete(f"/api/kb/products/{pid}/").status_code, 404)
		self.assertEqual(c.delete(f"/api/kb/products/images/{img.id}/").status_code, 404)
		self.assertEqual(Product.objects.count(), 1)

	def test_non_admin_cannot_write(self):
		pid = self.make_product()["id"]
		self.pu.role = "member"
		self.pu.save()
		self.assertEqual(self.create().status_code, 403)
		self.assertEqual(self.client.delete(f"/api/kb/products/{pid}/").status_code, 403)
		self.assertEqual(self.list_products().status_code, 200)

	def test_failed_save_leaves_no_rows_or_files(self):
		orig = ProductImage.save
		calls = []

		def flaky(img, *a, **kw):
			calls.append(1)
			if len(calls) == 2:
				raise RuntimeError("boom")
			return orig(img, *a, **kw)

		before = len(files_under_media())
		with patch.object(ProductImage, "save", autospec=True, side_effect=flaky):
			with self.captureOnCommitCallbacks(execute=True):
				r = self.create()
		self.assertEqual(r.status_code, 500)
		self.assertEqual(Product.objects.count(), 0)
		self.assertEqual(ProductImage.objects.count(), 0)
		self.assertEqual(len(files_under_media()), before)
		self.embed.delay.assert_not_called()

	def test_failed_add_images_leaves_existing_product_intact(self):
		pid = self.make_product()["id"]
		files_before = len(files_under_media())
		orig = ProductImage.save
		calls = []

		def flaky(img, *a, **kw):
			calls.append(1)
			if len(calls) == 2:
				raise RuntimeError("boom")
			return orig(img, *a, **kw)

		with patch.object(ProductImage, "save", autospec=True, side_effect=flaky):
			r = self.client.post(f"/api/kb/products/{pid}/images/", {"images": [upload("c.png"), upload("d.png")]}, format="multipart")
		self.assertEqual(r.status_code, 500)
		self.assertEqual(ProductImage.objects.filter(product_id=pid).count(), 2)
		self.assertEqual(len(files_under_media()), files_before)

	def test_tenant_off_gates_every_route(self):
		pid = self.make_product()["id"]
		img = ProductImage.objects.first()
		self.project.image_search_enabled = False
		self.project.save()
		pj = str(self.project.id)
		calls = {
			"create": lambda: self.create(),
			"patch": lambda: self.client.patch(f"/api/kb/products/{pid}/", {"name": "x"}, format="json"),
			"delete": lambda: self.client.delete(f"/api/kb/products/{pid}/"),
			"add_images": lambda: self.client.post(f"/api/kb/products/{pid}/images/", {"images": [upload("c.png")]}, format="multipart"),
			"delete_image": lambda: self.client.delete(f"/api/kb/products/images/{img.id}/"),
			"file": lambda: self.client.get(f"/api/kb/products/images/{img.id}/file/"),
		}
		for name, call in calls.items():
			with self.subTest(route=name):
				r = call()
				self.assertEqual(r.status_code, 404)
				self.assertEqual(r.json()["error"]["code"], "feature_disabled")
		self.assertEqual(Product.objects.count(), 1)

	def test_non_admin_cannot_add_or_delete_images(self):
		pid = self.make_product()["id"]
		img = ProductImage.objects.first()
		self.pu.role = "member"
		self.pu.save()
		r = self.client.post(f"/api/kb/products/{pid}/images/", {"images": [upload("c.png")]}, format="multipart")
		self.assertEqual(r.status_code, 403)
		self.assertEqual(self.client.delete(f"/api/kb/products/images/{img.id}/").status_code, 403)
		self.assertEqual(ProductImage.objects.count(), 2)

	def test_tenant_off_hides_endpoints(self):
		self.project.image_search_enabled = False
		self.project.save()
		r = self.list_products()
		self.assertEqual(r.status_code, 404)
		self.assertEqual(r.json()["error"]["code"], "feature_disabled")
		r = self.client.get("/api/kb/features/", {"project_id": str(self.project.id)})
		self.assertEqual(r.json()["data"], {"image_search_available": True, "image_search": False})

	def test_tenant_admin_toggles_feature(self):
		body = {"project_id": str(self.project.id), "image_search": False}
		r = self.client.patch("/api/kb/features/", body, format="json")
		self.assertEqual(r.status_code, 200)
		self.assertEqual(r.json()["data"], {"image_search_available": True, "image_search": False})
		self.project.refresh_from_db()
		self.assertFalse(self.project.image_search_enabled)
		r = self.client.patch("/api/kb/features/", {**body, "image_search": True}, format="json")
		self.assertEqual(r.json()["data"]["image_search"], True)
		self.project.refresh_from_db()
		self.assertTrue(self.project.image_search_enabled)

	def test_tenant_non_admin_cannot_toggle(self):
		self.pu.role = "member"
		self.pu.save()
		r = self.client.patch("/api/kb/features/", {"project_id": str(self.project.id), "image_search": False}, format="json")
		self.assertEqual(r.status_code, 403)
		self.project.refresh_from_db()
		self.assertTrue(self.project.image_search_enabled)

	def test_tenant_cannot_enable_when_not_allowed(self):
		self.project.image_search_allowed = False
		self.project.image_search_enabled = False
		self.project.save()
		r = self.client.patch("/api/kb/features/", {"project_id": str(self.project.id), "image_search": True}, format="json")
		self.assertEqual(r.status_code, 403)
		self.assertEqual(r.json()["error"]["code"], "not_allowed")
		self.project.refresh_from_db()
		self.assertFalse(self.project.image_search_enabled)

	def test_operator_revoke_overrides_tenant(self):
		self.project.image_search_allowed = False
		self.project.save()
		r = self.list_products()
		self.assertEqual(r.status_code, 404)
		self.assertEqual(r.json()["error"]["code"], "feature_disabled")
		r = self.client.get("/api/kb/features/", {"project_id": str(self.project.id)})
		self.assertEqual(r.json()["data"], {"image_search_available": False, "image_search": False})

	@override_settings(IMAGE_SEARCH_ENABLED=False)
	def test_master_off_hides_endpoints(self):
		r = self.list_products()
		self.assertEqual(r.status_code, 404)
		self.assertEqual(r.json()["error"]["code"], "feature_disabled")
		r = self.client.get("/api/kb/features/", {"project_id": str(self.project.id)})
		self.assertEqual(r.json()["data"], {"image_search_available": False, "image_search": False})

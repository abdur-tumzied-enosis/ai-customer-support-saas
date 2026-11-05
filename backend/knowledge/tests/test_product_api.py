from io import BytesIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import TestCase, override_settings
from PIL import Image
from rest_framework.test import APIClient

from accounts.models import Project, ProjectUser
from agents.image_search import MAX_IMAGE_BYTES
from core.storage import thumb_key
from core.tests.utils import fresh_storage
from knowledge.models import MAX_PRODUCT_IMAGE_BYTES, KnowledgeBase, Product, ProductImage

User = get_user_model()


def png():
	buf = BytesIO()
	Image.new("RGB", (8, 8), "red").save(buf, "PNG")
	return buf.getvalue()


def spec(name="a.jpg", size=1000, content_type="image/jpeg"):
	return {"name": name, "size": size, "content_type": content_type}


def fake_presign(key, content_type, sha256_hex=None, size=None):
	return {"url": f"https://r2.test/{key}", "headers": {"Content-Type": content_type}}


@fresh_storage
@override_settings(IMAGE_SEARCH_ENABLED=True)
class ProductApiTests(TestCase):
	def setUp(self):
		self.user = User.objects.create_user(username="a@example.com", email="a@example.com", password="x")
		self.project = Project.objects.create(name="P", slug="p-img", image_search_allowed=True)
		self.pu = ProjectUser.objects.create(project=self.project, user=self.user, role="admin")
		self.kb = KnowledgeBase.objects.create(project=self.project, name="default", is_default=True)
		self.client = APIClient()
		self.client.force_authenticate(user=self.user)
		patches = {
			"embed": patch("knowledge.products.embed_product_image"),
			"presign_put": patch("knowledge.products.presign_put", side_effect=fake_presign),
			"presign_get": patch("knowledge.products.presign_get", side_effect=lambda key: f"https://r2.test/get/{key}"),
		}
		for name, p in patches.items():
			setattr(self, name, p.start())
			self.addCleanup(p.stop)

	def create(self, files=None, **extra):
		data = {"project_id": str(self.project.id), "kb_id": str(self.kb.id), "name": "Shoe", "description": "d",
			"files": files if files is not None else [spec("a.jpg"), spec("b.png", content_type="image/png")]}
		data.update(extra)
		return self.client.post("/api/kb/products/", data, format="json")

	def add(self, pid, files):
		return self.client.post(f"/api/kb/products/{pid}/images/", {"files": files}, format="json")

	def confirm(self, ids):
		return self.client.post("/api/kb/products/images/confirm/", {"image_ids": [str(i) for i in ids]}, format="json")

	def put_all(self):
		"""What the browser does: PUT a real photo to every uploading key."""
		rows = list(ProductImage.objects.filter(status="uploading"))
		for img in rows:
			default_storage.save(img.file.name, ContentFile(png()))
		return [img.id for img in rows]

	def make_product(self):
		data = self.create().json()["data"]
		with self.captureOnCommitCallbacks(execute=True):
			self.confirm(self.put_all())
		return data["product"]

	def list_products(self):
		return self.client.get("/api/kb/products/", {"project_id": str(self.project.id), "kb_id": str(self.kb.id)})

	def test_create_returns_uploads_and_uploading_rows(self):
		r = self.create()
		self.assertEqual(r.status_code, 201, r.content)
		data = r.json()["data"]
		imgs = list(ProductImage.objects.all())
		self.assertEqual([i.status for i in imgs], ["uploading", "uploading"])
		self.assertTrue(all(i.file.name.startswith("tmp/products/p-img/") for i in imgs))
		self.assertEqual({i.file.name.rsplit(".", 1)[1] for i in imgs}, {"jpg", "png"})
		self.assertEqual({u["image_id"] for u in data["uploads"]}, {str(i.id) for i in imgs})
		upload = data["uploads"][0]
		self.assertEqual(upload["url"], f"https://r2.test/{ProductImage.objects.get(id=upload['image_id']).file.name}")
		self.assertIn("Content-Type", upload["headers"])
		self.assertEqual(data["product"]["name"], "Shoe")
		self.embed.delay.assert_not_called()

	def test_create_rejects_oversize_declared(self):
		r = self.create(files=[spec("big.jpg", size=MAX_PRODUCT_IMAGE_BYTES + 1)])
		self.assertEqual(r.status_code, 400, r.content)
		self.assertEqual(r.json()["error"]["code"], "image_too_large")
		self.assertIn("big.jpg is larger than", r.json()["error"]["message"])
		self.assertEqual(Product.objects.count(), 0)

	def test_catalog_accepts_photo_over_customer_limit(self):
		# Full-size stock and phone photos are often 5-10 MB; the 5 MB cap is for customer uploads only
		r = self.create(files=[spec("big.jpg", size=MAX_IMAGE_BYTES + 1)])
		self.assertEqual(r.status_code, 201, r.content)

	def test_create_rejects_bad_content_type(self):
		r = self.create(files=[spec("ok.jpg"), spec("bad.pdf", content_type="application/pdf")])
		self.assertEqual(r.status_code, 400)
		self.assertEqual(r.json()["error"]["code"], "invalid_image")
		self.assertIn("bad.pdf", r.json()["error"]["message"])
		self.assertEqual(Product.objects.count(), 0)

	def test_create_rejects_malformed_files(self):
		for files in ("nope", [{"name": "a.jpg"}], [spec(size="10")], [spec(size=0)], [None]):
			with self.subTest(files=files):
				r = self.create(files=files)
				self.assertEqual(r.status_code, 400, r.content)
				self.assertEqual(r.json()["error"]["code"], "invalid_files")
		self.assertEqual(Product.objects.count(), 0)

	def test_too_many_images(self):
		r = self.create(files=[spec(f"{i}.jpg") for i in range(11)])
		self.assertEqual(r.status_code, 400)
		self.assertEqual(r.json()["error"]["code"], "too_many_images")
		self.assertEqual(Product.objects.count(), 0)

	def test_no_images_and_empty_name(self):
		self.assertEqual(self.create(files=[]).json()["error"]["code"], "no_images")
		self.assertEqual(self.create(name=" ").json()["error"]["code"], "invalid_name")

	def test_add_images_respects_total_limit(self):
		pid = self.make_product()["id"]
		r = self.add(pid, [spec(f"{i}.jpg") for i in range(9)])
		self.assertEqual(r.json()["error"]["code"], "too_many_images")
		r = self.add(pid, [spec("c.jpg")])
		self.assertEqual(r.status_code, 201)
		self.assertEqual(len(r.json()["data"]["uploads"]), 1)
		self.assertEqual(len(r.json()["data"]["product"]["images"]), 3)

	def test_confirm_queues_embed(self):
		self.create()
		ids = self.put_all()
		with self.captureOnCommitCallbacks(execute=True):
			r = self.confirm(ids)
		self.assertEqual(r.status_code, 200, r.content)
		self.assertEqual([i["status"] for i in r.json()["data"]], ["pending", "pending"])
		self.assertEqual(ProductImage.objects.filter(status="pending").count(), 2)
		self.assertEqual(self.embed.delay.call_count, 2)

	def test_confirm_missing_object_marks_failed(self):
		self.create()
		img = ProductImage.objects.first()
		with self.captureOnCommitCallbacks(execute=True):
			r = self.confirm([img.id])
		self.assertEqual(r.status_code, 200)
		img.refresh_from_db()
		self.assertEqual((img.status, img.error), ("failed", "upload_missing"))
		self.embed.delay.assert_not_called()

	def test_confirm_oversize_marks_failed(self):
		self.create(files=[spec()])
		img = ProductImage.objects.get()
		default_storage.save(img.file.name, ContentFile(b"x" * 101))
		with patch("knowledge.products.MAX_PRODUCT_IMAGE_BYTES", 100), self.captureOnCommitCallbacks(execute=True):
			self.confirm([img.id])
		img.refresh_from_db()
		self.assertEqual((img.status, img.error), ("failed", "upload_too_large"))
		self.assertFalse(default_storage.exists(img.file.name))
		self.embed.delay.assert_not_called()

	def test_confirm_twice_queues_once(self):
		self.create(files=[spec()])
		ids = self.put_all()
		with self.captureOnCommitCallbacks(execute=True):
			self.confirm(ids)
		with self.captureOnCommitCallbacks(execute=True):
			r = self.confirm(ids)
		self.assertEqual(r.status_code, 200)
		self.assertEqual(self.embed.delay.call_count, 1)

	def test_confirm_rejects_malformed_ids(self):
		r = self.client.post("/api/kb/products/images/confirm/", {"image_ids": "x"}, format="json")
		self.assertEqual(r.status_code, 400)
		self.assertEqual(r.json()["error"]["code"], "invalid_ids")
		self.assertEqual(self.confirm(["not-a-uuid"]).status_code, 404)

	def test_list_shows_images_with_urls(self):
		self.make_product()
		r = self.list_products()
		self.assertEqual(r.status_code, 200)
		prod = r.json()["data"][0]
		img = prod["images"][0]
		self.assertEqual(img["url"], f"/api/kb/products/images/{img['id']}/file/")
		self.assertEqual(img["thumb_url"], f"/api/kb/products/images/{img['id']}/file/?size=thumb")
		self.assertEqual(img["status"], "pending")
		self.assertEqual(prod["name"], "Shoe")

	def test_patch_and_delete(self):
		pid = self.make_product()["id"]
		r = self.client.patch(f"/api/kb/products/{pid}/", {"name": "Boot"}, format="json")
		self.assertEqual(r.status_code, 200)
		self.assertEqual(Product.objects.get(id=pid).name, "Boot")
		img = ProductImage.objects.filter(product_id=pid).first()
		r = self.client.delete(f"/api/kb/products/images/{img.id}/")
		self.assertEqual(r.status_code, 200)
		self.assertFalse(ProductImage.objects.filter(id=img.id).exists())
		self.assertFalse(default_storage.exists(img.file.name))
		r = self.client.delete(f"/api/kb/products/{pid}/")
		self.assertEqual(r.status_code, 200)
		self.assertEqual(Product.objects.count(), 0)

	def test_file_view_redirects_to_presigned(self):
		self.make_product()
		img = ProductImage.objects.first()
		r = self.client.get(f"/api/kb/products/images/{img.id}/file/")
		self.assertEqual(r.status_code, 302)
		self.assertEqual(r["Location"], f"https://r2.test/get/{img.file.name}")

	def test_thumb_served_when_ready(self):
		self.make_product()
		img = ProductImage.objects.first()
		ProductImage.objects.filter(id=img.id).update(status="ready")
		r = self.client.get(f"/api/kb/products/images/{img.id}/file/", {"size": "thumb"})
		self.assertEqual(r["Location"], f"https://r2.test/get/{thumb_key(img.file.name)}")

	def test_thumb_falls_back_when_not_ready(self):
		self.make_product()
		img = ProductImage.objects.first()
		r = self.client.get(f"/api/kb/products/images/{img.id}/file/", {"size": "thumb"})
		self.assertEqual(r["Location"], f"https://r2.test/get/{img.file.name}")

	def test_uploading_or_failed_image_file_is_404(self):
		self.create()
		img = ProductImage.objects.first()
		self.assertEqual(self.client.get(f"/api/kb/products/images/{img.id}/file/").status_code, 404)
		ProductImage.objects.filter(id=img.id).update(status="failed")
		self.assertEqual(self.client.get(f"/api/kb/products/images/{img.id}/file/").status_code, 404)

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
		self.assertEqual(c.post(f"/api/kb/products/{pid}/images/", {"files": [spec()]}, format="json").status_code, 404)
		self.assertEqual(c.post("/api/kb/products/images/confirm/", {"image_ids": [str(img.id)]}, format="json").status_code, 404)
		self.assertEqual(Product.objects.count(), 1)

	def test_non_admin_cannot_write(self):
		pid = self.make_product()["id"]
		img = ProductImage.objects.first()
		self.pu.role = "member"
		self.pu.save()
		self.assertEqual(self.create().status_code, 201)  # members create and edit products
		self.assertEqual(self.client.delete(f"/api/kb/products/{pid}/").status_code, 403)
		self.assertEqual(self.confirm([img.id]).status_code, 403)
		self.assertEqual(self.list_products().status_code, 200)

	def test_failed_save_leaves_no_rows(self):
		orig = ProductImage.save
		calls = []

		def flaky(img, *a, **kw):
			calls.append(1)
			if len(calls) == 2:
				raise RuntimeError("boom")
			return orig(img, *a, **kw)

		with patch.object(ProductImage, "save", autospec=True, side_effect=flaky):
			r = self.create()
		self.assertEqual(r.status_code, 500)
		self.assertEqual(Product.objects.count(), 0)
		self.assertEqual(ProductImage.objects.count(), 0)

	def test_failed_add_images_leaves_existing_product_intact(self):
		pid = self.make_product()["id"]
		orig = ProductImage.save
		calls = []

		def flaky(img, *a, **kw):
			calls.append(1)
			if len(calls) == 2:
				raise RuntimeError("boom")
			return orig(img, *a, **kw)

		with patch.object(ProductImage, "save", autospec=True, side_effect=flaky):
			r = self.add(pid, [spec("c.jpg"), spec("d.jpg")])
		self.assertEqual(r.status_code, 500)
		self.assertEqual(ProductImage.objects.filter(product_id=pid).count(), 2)

	def test_tenant_off_gates_every_route(self):
		pid = self.make_product()["id"]
		img = ProductImage.objects.first()
		self.project.image_search_enabled = False
		self.project.save()
		calls = {
			"create": lambda: self.create(),
			"add_images": lambda: self.add(pid, [spec()]),
			"confirm": lambda: self.confirm([img.id]),
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
		self.assertEqual(self.add(pid, [spec()]).status_code, 403)
		self.assertEqual(self.client.delete(f"/api/kb/products/images/{img.id}/").status_code, 403)
		self.assertEqual(ProductImage.objects.count(), 2)

	def test_tenant_off_hides_endpoints(self):
		self.project.image_search_enabled = False
		self.project.save()
		self.assertEqual(self.list_products().status_code, 200)  # the catalog no longer needs image search
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
		self.assertEqual(self.list_products().status_code, 200)
		r = self.create()  # photos are gated, the catalog is not
		self.assertEqual(r.status_code, 404)
		self.assertEqual(r.json()["error"]["code"], "feature_disabled")
		r = self.client.get("/api/kb/features/", {"project_id": str(self.project.id)})
		self.assertEqual(r.json()["data"], {"image_search_available": False, "image_search": False})

	@override_settings(IMAGE_SEARCH_ENABLED=False)
	def test_master_off_hides_endpoints(self):
		self.assertEqual(self.list_products().status_code, 200)
		r = self.create()  # photos are gated, the catalog is not
		self.assertEqual(r.status_code, 404)
		self.assertEqual(r.json()["error"]["code"], "feature_disabled")
		r = self.client.get("/api/kb/features/", {"project_id": str(self.project.id)})
		self.assertEqual(r.json()["data"], {"image_search_available": False, "image_search": False})

	def test_presign_signs_declared_size(self):
		self.create(files=[spec(size=4321)])
		self.assertEqual(self.presign_put.call_args.kwargs["size"], 4321)

	def test_concurrent_confirm_queues_once(self):
		self.create(files=[spec()])
		ids = self.put_all()

		def other_confirm_wins(key):  # a second confirm finishes while this one is checking the object
			ProductImage.objects.filter(id=ids[0]).update(status="pending")
			return 1000
		with patch("knowledge.products.object_size", side_effect=other_confirm_wins), self.captureOnCommitCallbacks(execute=True):
			r = self.confirm(ids)
		self.assertEqual(r.status_code, 200)
		self.embed.delay.assert_not_called()

from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Project, ProjectUser
from knowledge.models import KnowledgeBase, Product, ProductImage, ProductSource
from knowledge.product_sources import guess_mapping, parse_table
from knowledge.tasks import SYNC_LOCK_TIMEOUT, _claim_sync, sync_product_source, sync_product_sources

User = get_user_model()
BASE = "/api/kb/products/sources/"
SHEET = "https://docs.google.com/spreadsheets/d/abc123/edit#gid=77"
CSV = b"id,name,price,image\nA1,Shoe,100,https://img.example.com/a.jpg\nA2,Bag,50,\n"


def resp(body=CSV):
	return MagicMock(status_code=200, content=body, headers={"Content-Type": "text/csv"})


@override_settings(IMAGE_SEARCH_ENABLED=False)
class SourceSyncTests(TestCase):
	def setUp(self):
		self.user = User.objects.create_user(username="a@example.com", email="a@example.com", password="x")
		self.project = Project.objects.create(name="P", slug="p-sync")
		ProjectUser.objects.create(project=self.project, user=self.user, role="admin")
		self.kb = KnowledgeBase.objects.create(project=self.project, name="default", is_default=True)
		self.client = APIClient()
		self.client.force_authenticate(user=self.user)
		self.source = ProductSource.objects.create(
			kb=self.kb, kind="sheet", sheet_url=SHEET, auto_sync_enabled=True,
			mapping={"external_id": "id", "name": "name", "price": "price", "image_url": "image"})

	def ids(self):
		return {"project_id": str(self.project.id), "kb_id": str(self.kb.id)}

	def qs(self, url):
		return f"{url}?project_id={self.project.id}&kb_id={self.kb.id}"  # PATCH and DELETE read the KB from the query string

	def test_image_column_is_guessed_and_parsed(self):
		headers, rows = parse_table("sheet.csv", CSV)
		self.assertEqual(guess_mapping(headers)["image_url"], "image")
		self.assertEqual(rows[0]["image"], "https://img.example.com/a.jpg")

	def test_sync_queues_image_download_once(self):
		with patch("knowledge.product_sources.requests.get", return_value=resp()), \
				patch("knowledge.tasks.fetch_product_images.delay") as delay, self.captureOnCommitCallbacks(execute=True):
			result = sync_product_source(str(self.source.id))
		self.assertEqual(result["images_queued"], 1)  # only A1 has an image
		delay.assert_called_once()
		product = Product.objects.get(external_id="A1")
		self.assertEqual(delay.call_args.args, (str(product.id), ["https://img.example.com/a.jpg"]))
		# The same URL already attached: the next sync must not download it again
		ProductImage.objects.create(product=product, file="x.webp", status="ready", source_url="https://img.example.com/a.jpg")
		with patch("knowledge.product_sources.requests.get", return_value=resp()), \
				patch("knowledge.tasks.fetch_product_images.delay") as delay, self.captureOnCommitCallbacks(execute=True):
			result = sync_product_source(str(self.source.id))
		self.assertEqual(result["images_queued"], 0)
		delay.assert_not_called()

	def test_lock_blocks_overlap_until_stale(self):
		self.assertTrue(_claim_sync(self.source.id))
		self.assertFalse(_claim_sync(self.source.id))
		self.assertEqual(sync_product_source(str(self.source.id)), {"error": "already_syncing", "message": "This source is already syncing"})
		ProductSource.objects.filter(id=self.source.id).update(sync_locked_at=timezone.now() - SYNC_LOCK_TIMEOUT - timedelta(minutes=1))
		self.assertTrue(_claim_sync(self.source.id))

	def test_failure_backs_off_and_success_resets(self):
		with patch("knowledge.product_sources.requests.get", return_value=MagicMock(status_code=500, content=b"", headers={})):
			sync_product_source(str(self.source.id))
		self.source.refresh_from_db()
		self.assertEqual(self.source.sync_failures, 1)
		self.assertIsNone(self.source.sync_locked_at)
		self.assertLessEqual(self.source.next_sync_at, timezone.now() + timedelta(minutes=5, seconds=5))
		with patch("knowledge.product_sources.requests.get", return_value=resp()), patch("knowledge.tasks.fetch_product_images.delay"):
			sync_product_source(str(self.source.id))
		self.source.refresh_from_db()
		self.assertEqual((self.source.sync_failures, self.source.last_error), (0, ""))
		self.assertGreater(self.source.next_sync_at, timezone.now() + timedelta(minutes=55))

	def test_beat_runs_only_due_enabled_sources(self):
		ProductSource.objects.create(kb=self.kb, kind="sheet", sheet_url=SHEET, auto_sync_enabled=True,
			mapping={"name": "name"}, next_sync_at=timezone.now() + timedelta(hours=1))
		ProductSource.objects.create(kb=self.kb, kind="sheet", sheet_url=SHEET, auto_sync_enabled=False, mapping={"name": "name"})
		with patch("knowledge.tasks.sync_product_source.delay") as queue:
			sync_product_sources()
		queue.assert_called_once_with(str(self.source.id))

	def test_schedule_endpoint_validates_and_saves(self):
		url = f"{BASE}{self.source.id}/schedule/"
		self.assertEqual(self.client.patch(self.qs(url), {"schedule": "weekly"}, format="json").status_code, 400)
		self.assertEqual(self.client.patch(self.qs(url), {"auto_sync_enabled": "yes"}, format="json").status_code, 400)
		r = self.client.patch(self.qs(url), {"auto_sync_enabled": False}, format="json")
		self.assertEqual(r.status_code, 200)
		self.source.refresh_from_db()
		self.assertEqual((self.source.auto_sync_enabled, self.source.next_sync_at), (False, None))
		r = self.client.patch(self.qs(url), {"auto_sync_enabled": True, "schedule": "daily"}, format="json")
		self.source.refresh_from_db()
		self.assertEqual((self.source.schedule, r.json()["data"]["source"]["schedule"]), ("daily", "daily"))
		self.assertIsNotNone(self.source.next_sync_at)

	def test_sync_while_running_returns_409(self):
		ProductSource.objects.filter(id=self.source.id).update(sync_locked_at=timezone.now())
		r = self.client.post(f"{BASE}{self.source.id}/sync/", self.ids(), format="json")
		self.assertEqual(r.status_code, 409)
		self.assertTrue(self.client.get(BASE, self.ids()).json()["data"][0]["sync_running"])



class MultiImageTests(TestCase):
	def setUp(self):
		self.user = User.objects.create_user(username="m@example.com", email="m@example.com", password="x")
		self.project = Project.objects.create(name="M", slug="p-multi")
		ProjectUser.objects.create(project=self.project, user=self.user, role="admin")
		self.kb = KnowledgeBase.objects.create(project=self.project, name="default", is_default=True)

	def test_split_handles_commas_spaces_lines_and_repeats(self):
		from knowledge.product_sources import split_image_urls
		text = "https://a.com/1.jpg, https://a.com/2.jpg;https://a.com/1.jpg\nhttps://a.com/3.jpg"
		self.assertEqual(split_image_urls(text), ["https://a.com/1.jpg", "https://a.com/2.jpg", "https://a.com/3.jpg"])
		self.assertEqual(split_image_urls(""), [])

	def test_one_cell_with_two_links_queues_one_task_with_both(self):
		csv = b"id,name,price,image\nA1,Shoe,100,https://img.example.com/a.jpg https://img.example.com/b.jpg\n"
		src = ProductSource.objects.create(kb=self.kb, kind="sheet", sheet_url=SHEET, auto_sync_enabled=True,
			mapping={"external_id": "id", "name": "name", "price": "price", "image_url": "image"})
		with patch("knowledge.product_sources.requests.get", return_value=resp(csv)), \
				patch("knowledge.tasks.fetch_product_images.delay") as delay, self.captureOnCommitCallbacks(execute=True):
			result = sync_product_source(str(src.id))
		self.assertEqual(result["images_queued"], 2)
		delay.assert_called_once()
		self.assertEqual(delay.call_args.args[1], ["https://img.example.com/a.jpg", "https://img.example.com/b.jpg"])

	def test_api_list_of_links_becomes_one_cell(self):
		from knowledge.product_api import _flatten
		self.assertEqual(_flatten(["https://a.com/1.jpg", "https://a.com/2.jpg"]), "https://a.com/1.jpg, https://a.com/2.jpg")
		self.assertEqual(_flatten([{"src": "x"}]), '[{"src": "x"}]')


class FailedImageRetryTests(TestCase):
	def setUp(self):
		self.user = User.objects.create_user(username="r@example.com", email="r@example.com", password="x")
		self.project = Project.objects.create(name="R", slug="p-retry")
		ProjectUser.objects.create(project=self.project, user=self.user, role="admin")
		self.kb = KnowledgeBase.objects.create(project=self.project, name="default", is_default=True)
		self.src = ProductSource.objects.create(kb=self.kb, kind="sheet", sheet_url=SHEET, auto_sync_enabled=True,
			mapping={"external_id": "id", "name": "name", "image_url": "image"})
		self.product = Product.objects.create(kb=self.kb, source_ref=self.src, external_id="A1", name="Shoe", price=100, source="sheet")
		self.url = "https://img.example.com/a.jpg"
		ProductImage.objects.create(product=self.product, file="x.webp", status="failed", source_url=self.url)

	def sync(self):
		with patch("knowledge.product_sources.requests.get", return_value=resp(CSV)), \
				patch("knowledge.tasks.fetch_product_images.delay") as delay, self.captureOnCommitCallbacks(execute=True):
			result = sync_product_source(str(self.src.id))
		return result, delay

	def test_recent_failure_is_not_downloaded_again(self):
		result, delay = self.sync()
		self.assertEqual(result["images_queued"], 0)
		delay.assert_not_called()

	def test_old_failure_is_retried(self):
		ProductImage.objects.filter(source_url=self.url).update(created_at=timezone.now() - timedelta(days=2))
		result, delay = self.sync()
		self.assertEqual(result["images_queued"], 1)
		delay.assert_called_once()

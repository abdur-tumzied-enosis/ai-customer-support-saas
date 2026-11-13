from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.conf import settings
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone

from agents.embeddings_api import Embedded
from channels_app import image_reply, tasks
from channels_app.models import Message
from channels_app.tests.test_image_reply import png_bytes
from channels_app.tests.test_reply_pipeline import PipelineBase
from knowledge.models import Product

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "vision-fallback-tests"}}


@override_settings(VISION_FALLBACK_ENABLED=True, IMAGE_SEARCH_ENABLED=True, CACHES=LOCMEM)
class VisionBase(PipelineBase):
	def setUp(self):
		super().setUp()
		cache.clear()  # the daily cap is cache-backed, so the annotate-time check needs a clean cache
		self.conv.project.vision_fallback_enabled = True
		self.conv.project.save(update_fields=["vision_fallback_enabled"])
		self.hits = []  # [(product_id, score)]
		self.fetch = mock.patch.object(image_reply, "fetch_photo", side_effect=lambda msg, att: png_bytes()).start()
		self.embed = mock.patch.object(image_reply, "embed_image", return_value=Embedded([[1, 0, 0, 0]], 1200, Decimal("0.00048"), "cohere/embed-5-fast")).start()
		self.search = mock.patch.object(image_reply, "search_products", side_effect=lambda *a, **k: self.hits).start()
		mock.patch.object(image_reply, "record_image_usage").start()
		mock.patch.object(image_reply, "charge").start()
		self.vision_capable = mock.patch.object(image_reply, "vision_route", return_value=("google", "gemini-3.8-flash")).start()
		self.addCleanup(mock.patch.stopall)

	def make_product(self, name="Red Kurta", description="Cotton kurta in red"):
		return Product.objects.create(kb=self.kb, name=name, price=100, status="active", description=description)

	def photo_message(self, text="", photos=1):
		return self.inbound(text, has_attachments=True, status="pending",
			attachment_info=[{"type": "image", "path": f"chat_media/p/{i}.webp"} for i in range(photos)],
			meta={"photo_reply": True})

	def annotate(self, *msgs):
		image_reply.annotate_turn(self.conv, list(msgs))
		for m in msgs:
			m.refresh_from_db()
		return msgs


class VisionAnnotateTest(VisionBase):
	def test_no_match_note_is_numbered_and_attached(self):
		(msg,) = self.annotate(self.photo_message())
		assert msg.meta["photo_notes"][0]["note"] == "[Customer sent photo 1. No catalog product looks similar. Photo attached.]"
		assert msg.meta["photo_vision"] == [{"index": 1, "path": "chat_media/p/0.webp"}]

	def test_confident_match_notes_not_attached(self):
		self.hits = [(str(self.make_product().id), 0.93)]
		(msg,) = self.annotate(self.photo_message())
		assert msg.meta["photo_notes"][0]["note"].endswith("Photo not attached.]")
		assert msg.meta["photo_vision"] == []

	def test_borderline_match_is_attached(self):
		self.hits = [(str(self.make_product().id), settings.IMAGE_SEARCH_MIN_SCORE + 0.05)]
		(msg,) = self.annotate(self.photo_message())
		assert msg.meta["photo_notes"][0]["note"].endswith("Photo attached: the match is not close enough to rely on.]")

	def test_damage_text_attaches_confident_match(self):
		self.hits = [(str(self.make_product().id), 0.93)]
		(msg,) = self.annotate(self.photo_message("it arrived cracked"))
		entry = msg.meta["photo_notes"][0]
		assert entry["trigger"] == "damage" and entry["attached"] is True
		assert "suggests damage" in entry["note"]

	def test_bangla_damage_text_attaches(self):
		(msg,) = self.annotate(self.photo_message("এটা ভেঙে গেছে, রিফান্ড চাই"))
		assert msg.meta["photo_notes"][0]["attached"] is True

	def test_banglish_damage_text_attaches(self):
		self.hits = [(str(self.make_product().id), 0.93)]
		(msg,) = self.annotate(self.photo_message("eta bhanga, ferot korte chai"))
		entry = msg.meta["photo_notes"][0]
		assert entry["attached"] is True and entry["trigger"] == "damage"

	def test_damage_from_previous_message_attaches(self):
		self.hits = [(str(self.make_product().id), 0.93)]
		prev = self.inbound("it is broken")
		Message.objects.filter(pk=prev.pk).update(created_at=timezone.now() - timedelta(minutes=2))
		(msg,) = self.annotate(self.photo_message(""))
		entry = msg.meta["photo_notes"][0]
		assert entry["attached"] is True and entry["trigger"] == "damage_history"

	def test_damage_text_older_than_lookback_does_not_attach(self):
		self.hits = [(str(self.make_product().id), 0.93)]
		prev = self.inbound("it is broken")
		Message.objects.filter(pk=prev.pk).update(created_at=timezone.now() - timedelta(minutes=11))
		(msg,) = self.annotate(self.photo_message(""))
		assert msg.meta["photo_notes"][0]["attached"] is False

	def test_unchecked_photo_with_damage_text_is_not_attached(self):
		self.fetch.side_effect = RuntimeError("decrypt failed")
		(msg,) = self.annotate(self.photo_message("this arrived cracked"))
		entry = msg.meta["photo_notes"][0]
		assert entry["reason"] == "unchecked" and entry["attachable"] is False
		assert msg.meta["photo_vision"] == []
		assert "Photo attached" not in entry["note"]

	def test_turn_cap_marks_third_photo_limit_reached(self):
		(msg,) = self.annotate(self.photo_message(photos=3))
		notes = msg.meta["photo_notes"]
		assert [n["attached"] for n in notes] == [True, True, False]
		assert notes[2]["note"].endswith("Photo not attached (limit reached).]")
		assert [p["index"] for p in msg.meta["photo_vision"]] == [1, 2]

	def test_daily_cap_is_applied_when_annotating(self):
		cache.set(f"vision-day:{self.conv.id}", [timezone.now().isoformat()] * 6, timeout=3600)
		(msg,) = self.annotate(self.photo_message())
		assert msg.meta["photo_notes"][0]["note"].endswith("Photo not attached (daily limit).]")
		assert msg.meta["photo_vision"] == []

	def test_retry_budget_subtracts_already_checked_photos(self):
		done = self.inbound("", has_attachments=True, status="completed",
			attachment_info=[{"type": "image", "path": f"chat_media/p/a{i}.webp"} for i in range(3)],
			meta={"photo_reply": True, image_reply.PHOTO_NOTES_KEY: [{"index": i, "note": "old"} for i in (1, 2, 3)]})
		fresh = self.photo_message(photos=3)
		self.annotate(done, fresh)
		notes = fresh.meta["photo_notes"]
		assert [n["index"] for n in notes] == [4, 5, 6]
		assert [n["reason"] for n in notes] == ["no_match", "no_match", "unchecked"]
		assert self.fetch.call_count == 2
		assert done.meta["photo_notes"][0]["note"] == "old"  # the annotated message is not touched

	def test_photo_numbering_spans_messages(self):
		first = self.photo_message(photos=1)
		second = self.photo_message(photos=2)
		first, second = self.annotate(first, second)
		assert [n["index"] for n in first.meta["photo_notes"]] == [1]
		assert [n["index"] for n in second.meta["photo_notes"]] == [2, 3]

	def test_fourth_photo_in_one_message_gets_unchecked_note(self):
		(msg,) = self.annotate(self.photo_message(photos=4))
		assert msg.meta["photo_notes"][3]["note"] == "[Customer sent photo 4, but it could not be checked against the catalog.]"
		assert self.fetch.call_count == 3

	def test_unreadable_photo_note_is_numbered(self):
		self.fetch.side_effect = RuntimeError("decrypt failed")
		(msg,) = self.annotate(self.photo_message())
		assert msg.meta["photo_notes"][0]["note"] == "[Customer sent photo 1, but it could not be checked against the catalog.]"

	def test_model_without_vision_says_not_attached(self):
		self.vision_capable.return_value = None
		(msg,) = self.annotate(self.photo_message())
		assert msg.meta["photo_notes"][0]["note"] == "[Customer sent photo 1. No catalog product looks similar. Photo not attached.]"
		assert msg.meta["photo_vision"] == []

	def test_tenant_off_says_not_attached(self):
		self.conv.project.vision_fallback_enabled = False
		self.conv.project.save(update_fields=["vision_fallback_enabled"])
		(msg,) = self.annotate(self.photo_message())
		assert msg.meta["photo_notes"][0]["note"].endswith("Photo not attached.]")
		assert msg.meta["photo_vision"] == []

	def test_legacy_photo_matches_still_renders(self):
		legacy = self.inbound("", has_attachments=True, status="completed",
			attachment_info=[{"type": "image", "path": "chat_media/p/l.webp"}], meta={"photo_reply": True, "photo_matches": "Red shirt"})
		(legacy,) = self.annotate(legacy)
		assert legacy.meta["photo_matches"] == "Red shirt"
		self.fetch.assert_not_called()
		assert "Red shirt" in tasks.format_message_with_context(legacy)

	def test_photo_notes_render_in_history(self):
		(msg,) = self.annotate(self.photo_message("hello"))
		formatted = tasks.format_message_with_context(msg)
		assert "[Customer sent photo 1." in formatted and formatted.startswith("hello ")

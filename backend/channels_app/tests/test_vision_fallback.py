import io
import os
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.conf import settings
from django.core.cache import cache
from django.test import SimpleTestCase, override_settings
from django.utils import timezone
from PIL import Image

from celery.exceptions import SoftTimeLimitExceeded

from agents.embeddings_api import Embedded
from agents.image_search import NotAnImage
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

	def test_earlier_attached_photo_is_not_shown_as_visible_in_history(self):
		self.annotate(self.photo_message(photos=1))  # attached on its own turn
		later = self.inbound("what is the total on that receipt?")
		joined = " ".join(h["content"] for h in tasks.build_history(later))
		assert "Photo attached." not in joined and "not visible now" in joined

	def test_photo_notes_render_in_history(self):
		(msg,) = self.annotate(self.photo_message("hello"))
		formatted = tasks.format_message_with_context(msg)
		assert "[Customer sent photo 1." in formatted and formatted.startswith("hello ")


def png_of_size(width, height):
	buf = io.BytesIO()
	Image.new("RGB", (width, height), "blue").save(buf, "PNG")
	return buf.getvalue()


class VisionReplyTest(VisionBase):
	def setUp(self):
		super().setUp()
		self.read = mock.patch.object(image_reply, "read_upload", side_effect=lambda path: png_bytes()).start()

	def test_generate_agent_reply_sets_context_vision_images(self):
		captured = []

		def capture(question, context, deps=None, history=None, project_id=None):
			captured.append(list(context.vision_images))
			return "ok"

		msg = self.inbound("hi")
		with mock.patch("channels_app.tasks.generate_reply", side_effect=capture):
			tasks.generate_agent_reply(msg, self.kb, images=["data:image/jpeg;base64,AAA"])
		assert captured == [["data:image/jpeg;base64,AAA"]]

	def test_reply_passes_images_and_merges_trace(self):
		msg = self.photo_message()
		with mock.patch.object(image_reply, "resolve_model", return_value=("google", "gemini-3.8-flash")):
			self.run_reply(msg)
		assert len(self.vision_images[-1]) == 1 and self.vision_images[-1][0].startswith("data:image/jpeg;base64,")
		assert "[Customer sent photo 1. No catalog product looks similar. Photo attached.]" in self.questions[-1]
		out = self.outbound()[-1]
		assert out.meta["trace"]["vision_images"] == 1 and out.meta["trace"]["vision_route"] == "reply"

	def test_vision_prep_soft_time_limit_sends_fallback(self):
		msg = self.photo_message()
		with mock.patch.object(image_reply, "vision_images", side_effect=SoftTimeLimitExceeded()):
			self.run_reply(msg)
		assert self.questions == []
		out = self.outbound()
		assert len(out) == 1 and out[0].text == tasks.FALLBACK_REPLY

	def test_vision_prep_error_does_not_block_reply(self):
		msg = self.photo_message()
		with mock.patch.object(image_reply, "vision_images", side_effect=RuntimeError("boom")):
			self.run_reply(msg)
		assert len(self.questions) == 1 and self.vision_images[-1] == []
		out = self.outbound()
		assert len(out) == 1 and out[0].meta["trace"]["vision_skipped"] == "error"
		msg.refresh_from_db()
		assert msg.meta["photo_notes"][0]["note"].endswith("Photo not attached (could not be prepared).]")
		assert msg.meta["photo_vision"] == []

	def test_photo_plus_handoff_keyword_skips_vision_prep(self):
		msg = self.photo_message("can I talk to a person")
		with mock.patch.object(image_reply, "vision_images") as prep:
			self.run_reply(msg)
		prep.assert_not_called()
		assert self.outbound()[-1].text == settings.HANDOFF_MESSAGE
		msg.refresh_from_db()
		assert msg.meta["photo_notes"][0]["note"].endswith("Photo not attached.]")
		assert msg.meta["photo_vision"] == []


class VisionEncodeTest(SimpleTestCase):
	def test_tall_image_long_side_is_1568(self):
		enc = image_reply.encode_vision_image(png_of_size(100, 4000))
		assert max(enc["sent"]) == settings.VISION_MAX_EDGE
		assert abs(enc["sent"][0] - round(100 * settings.VISION_MAX_EDGE / 4000)) <= 1

	def test_small_image_is_not_upscaled(self):
		assert image_reply.encode_vision_image(png_of_size(100, 50))["sent"] == (100, 50)

	def test_returns_jpeg_data_uri(self):
		enc = image_reply.encode_vision_image(png_of_size(20, 20))
		assert enc["uri"].startswith("data:image/jpeg;base64,") and enc["bytes"] > 0

	def test_corrupt_bytes_raise_not_an_image(self):
		with self.assertRaises(NotAnImage):
			image_reply.encode_vision_image(b"not an image")


class VisionImagesTest(VisionBase):
	def setUp(self):
		super().setUp()
		self.read = mock.patch.object(image_reply, "read_upload", side_effect=lambda path: png_bytes()).start()

	def turn_with(self, photos=2, text=""):
		msg = self.photo_message(text, photos=photos)
		self.annotate(msg)
		return msg

	def call(self, msg):
		return image_reply.vision_images([msg], self.conv)

	def test_two_chosen_images_are_prepared(self):
		msg = self.turn_with(photos=2)
		out = self.call(msg)
		assert len(out["images"]) == 2 and out["marker"] == ""
		assert out["trace"]["vision_images"] == 2
		assert len(out["trace"]["vision_dimensions"]) == 2 and len(out["trace"]["vision_original_dimensions"]) == 2
		assert out["trace"]["vision_triggers"] == ["no_match", "no_match"]
		assert out["trace"]["vision_top_scores"] == [None, None]

	def test_daily_cap_prefills_and_limits(self):
		msg = self.turn_with(photos=2)
		cache.set(f"vision-day:{self.conv.id}", [timezone.now().isoformat()] * 5, timeout=3600)
		out = self.call(msg)
		assert len(out["images"]) == 1
		assert out["marker"] == "[Photo 2 was not attached (daily limit); do not claim to have seen it.]"
		assert out["trace"]["vision_skipped"] == "cap"

	def test_daily_cap_rewrites_saved_note(self):
		msg = self.turn_with(photos=2)
		cache.set(f"vision-day:{self.conv.id}", [timezone.now().isoformat()] * 5, timeout=3600)
		self.call(msg)
		msg.refresh_from_db()
		assert msg.meta["photo_notes"][1]["attached"] is False
		assert "Photo not attached (daily limit)" in msg.meta["photo_notes"][1]["note"]
		assert [p["index"] for p in msg.meta["photo_vision"]] == [1]

	def test_daily_cap_zero_prepares_nothing(self):
		msg = self.turn_with(photos=2)
		cache.set(f"vision-day:{self.conv.id}", [timezone.now().isoformat()] * 6, timeout=3600)
		out = self.call(msg)
		assert out["images"] == []
		assert out["marker"] == "[Photos 1, 2 were not attached (daily limit); do not claim to have seen them.]"
		msg.refresh_from_db()
		assert [n["attached"] for n in msg.meta["photo_notes"]] == [False, False]

	def test_old_timestamps_are_pruned(self):
		cache.set(f"vision-day:{self.conv.id}", [(timezone.now() - timedelta(hours=25)).isoformat()] * 6, timeout=3600)
		msg = self.turn_with(photos=2)
		assert len(self.call(msg)["images"]) == 2

	def test_read_failure_skips_image_and_reports(self):
		msg = self.turn_with(photos=2)
		def read(path):
			if path.endswith("1.webp"):  # photo 2's stored file is gone
				raise OSError("gone")
			return png_bytes()

		self.read.side_effect = read
		out = self.call(msg)
		assert len(out["images"]) == 1
		assert out["marker"] == "[Photo 2 could not be prepared; do not claim to have seen it.]"
		assert out["trace"]["vision_error"] == "prepare"
		msg.refresh_from_db()
		assert msg.meta["photo_notes"][1]["note"].endswith("Photo not attached (could not be prepared).]")

	def test_tenant_gate_skips(self):
		msg = self.turn_with(photos=1)
		self.conv.project.vision_fallback_enabled = False
		self.conv.project.save(update_fields=["vision_fallback_enabled"])
		assert self.call(msg) == {"images": [], "marker": "", "trace": {"vision_skipped": "tenant"}}

	def test_model_gate_skips(self):
		msg = self.turn_with(photos=1)
		self.vision_capable.return_value = None
		assert self.call(msg)["trace"] == {"vision_skipped": "model"}

	def test_vision_model_route_is_traced(self):
		msg = self.turn_with(photos=1)
		with mock.patch.object(image_reply, "resolve_model", return_value=("openrouter", "reply/x")):
			self.vision_capable.return_value = ("openrouter", "vendor/eyes")
			assert self.call(msg)["trace"]["vision_route"] == "vision"
			self.vision_capable.return_value = ("openrouter", "reply/x")
			assert self.call(msg)["trace"]["vision_route"] == "reply"

	def test_text_turn_without_vision_adds_no_trace(self):
		self.turn_with(photos=1)  # an earlier photo message in the conversation
		text = self.inbound("ok thanks")
		assert image_reply.vision_images([text], self.conv) == {"images": [], "marker": "", "trace": {}}

	def test_tracing_skips_photos_before_they_are_counted(self):
		msg = self.turn_with(photos=1)
		with mock.patch.dict(os.environ, {"LANGSMITH_TRACING": "true"}):
			out = self.call(msg)
		assert out == {"images": [], "marker": "", "trace": {"vision_skipped": "tracing"}}
		msg.refresh_from_db()
		assert msg.meta["photo_notes"][0]["note"].endswith("Photo not attached.]")
		assert cache.get(f"vision-day:{self.conv.id}") is None

	def test_cache_unavailable_fails_closed(self):
		msg = self.turn_with(photos=1)
		with mock.patch.object(image_reply, "cache") as broken:
			broken.get.side_effect = RuntimeError("redis down")
			out = self.call(msg)
		assert out["images"] == [] and out["trace"]["vision_skipped"] == "cap"

	def test_cache_write_failure_fails_closed(self):
		msg = self.turn_with(photos=2)
		with mock.patch.object(image_reply, "cache") as flaky:
			flaky.get.return_value = []
			flaky.set.side_effect = RuntimeError("redis down")
			out = self.call(msg)
		assert out["images"] == []
		assert out["trace"]["vision_skipped"] == "cap"

import io
from unittest import mock

from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from django.test import SimpleTestCase, override_settings
from PIL import Image

from agents.llm import build_system_prompt
from channels_app.tasks import FALLBACK_REPLY
from channels_app import media, tasks
from channels_app.media import MediaError
from channels_app.models import ChannelCredential, Message
from channels_app.service.facebook_messing import handle_facebook_message
from channels_app.service.whatsapp_messaging import handle_whatsapp_message
from channels_app.tests.test_reply_pipeline import PipelineBase
from knowledge.models import KnowledgeBase, Product
from usage.models import UsageEvent

PHOTO_URL = "https://scontent.xx.fbcdn.net/p.jpg"
UNCHECKED = "[Customer sent a photo, but it could not be checked against the catalog.]"
NO_MATCH = "[Customer sent a photo. No catalog product looks similar.]"


def png_bytes():
	buf = io.BytesIO()
	Image.new("RGB", (4, 4), "red").save(buf, "PNG")
	return buf.getvalue()


def fb_payload(mid, recipient="page-1", text="", photos=1, sticker=False):
	payload = {"url": PHOTO_URL, **({"sticker_id": 369239263222822} if sticker else {})}
	message = {"mid": mid, **({"text": text} if text else {})}
	if photos:
		message["attachments"] = [{"type": "image", "payload": dict(payload)} for _ in range(photos)]
	return {"object": "page", "entry": [{"messaging": [{
		"sender": {"id": "psid"}, "recipient": {"id": recipient}, "timestamp": 1, "message": message,
	}]}]}


def wa_payload(wamid, caption=None):
	image = {"id": "media-1", "mime_type": "image/jpeg", **({"caption": caption} if caption is not None else {})}
	return {"object": "whatsapp_business_account", "entry": [{"id": "waba-1", "changes": [{"field": "messages", "value": {
		"messaging_product": "whatsapp",
		"metadata": {"display_phone_number": "15550001111", "phone_number_id": "pn-1"},
		"contacts": [{"wa_id": "8801700000000", "profile": {"name": "Ann Lee"}}],
		"messages": [{"from": "8801700000000", "id": wamid, "timestamp": "1700000000", "type": "image", "image": image}],
	}}]}]}


@override_settings(IMAGE_SEARCH_ENABLED=True)
class ImageReplyTest(PipelineBase):
	def setUp(self):
		super().setUp()
		self.project.image_search_allowed = True
		self.project.save()
		self.cred = ChannelCredential.objects.create(
			project=self.project, channel="facebook", knowledge_base=self.kb, is_active=True, image_search_enabled=True,
			credentials={"page_id": "page-1", "page_access_token": "tok"},
		)
		self.product = Product.objects.create(kb=self.kb, name="Red Kurta", price=100, status="active", description="Cotton kurta in red, sizes S to XL")
		self.scheduled = []
		mock.patch("channels_app.service.facebook_messing.schedule_reply", side_effect=self.scheduled.append).start()
		mock.patch("channels_app.service.whatsapp_messaging.schedule_reply", side_effect=self.scheduled.append).start()

		def stored_then_scheduled(message_id, reply):  # media_store's effect, minus the R2 copy (fetch_photo is mocked)
			if reply:
				self.scheduled.append(Message.objects.get(id=message_id))
		for handler in ("facebook_messing", "whatsapp_messaging"):
			mock.patch(f"channels_app.service.{handler}.store_message_media.delay", side_effect=stored_then_scheduled).start()
		mock.patch("channels_app.service.facebook_messing.get_user_name", return_value="Ann").start()
		self.fetch = mock.patch("channels_app.image_reply.fetch_photo", return_value=png_bytes()).start()
		self.embed = mock.patch("channels_app.image_reply.embed_image", return_value=[1, 0, 0, 0]).start()
		self.search = mock.patch(
			"channels_app.image_reply.search_products", side_effect=lambda collection, vector: [(str(self.product.id), 0.93)]
		).start()

	def answer_all(self):
		for msg in list(self.scheduled):
			self.run_reply(msg)

	def searches(self):
		return UsageEvent.objects.filter(project=self.project, kind="image", metadata__op="search")

	def assert_ignored(self, mid):
		msg = Message.objects.get(external_message_id=mid)
		assert self.scheduled == [] and msg.status == "completed"
		assert "photo_reply" not in msg.meta and msg.meta["recipient"]["id"]  # raw event stored as before
		self.fetch.assert_not_called()
		assert not self.searches().exists()

	def test_photo_message_gets_reply_with_match(self):
		handle_facebook_message(fb_payload("m1"))
		msg = Message.objects.get(external_message_id="m1")
		assert self.scheduled == [msg] and msg.status == "pending"
		assert msg.meta["photo_reply"] is True and msg.meta["recipient"] == {"id": "page-1"}
		self.answer_all()
		assert self.gen.call_count == 1
		question = self.questions[0]
		assert '"Red Kurta" (similarity 0.93): Cotton kurta in red, sizes S to XL' in question, question
		assert "[Message includes attachments" not in question
		self.search.assert_called_once_with(self.kb.image_collection, [1, 0, 0, 0])
		event = self.searches().get()
		assert event.metadata == {"op": "search", "matched": True}
		assert (event.message_id, event.conversation_id) == (msg.id, msg.conversation_id)
		msg.refresh_from_db()
		assert msg.status == "completed" and "Red Kurta" in msg.meta["photo_matches"]
		assert len(self.outbound()) == 1
		self.send.assert_called_once()

	def test_no_match_note(self):
		self.search.side_effect = lambda collection, vector: []
		handle_facebook_message(fb_payload("m1"))
		self.answer_all()
		assert NO_MATCH in self.questions[0]
		assert self.searches().get().metadata["matched"] is False
		assert len(self.outbound()) == 1

	def test_product_from_another_kb_is_dropped(self):
		other_kb = KnowledgeBase.objects.create(project=self.project, name="kb2", vector_collection="vc-other")
		stale = Product.objects.create(kb=other_kb, name="Blue Saree")
		self.search.side_effect = lambda collection, vector: [(str(stale.id), 0.95), (str(self.product.id), 0.90)]
		handle_facebook_message(fb_payload("m1"))
		self.answer_all()
		assert "Blue Saree" not in self.questions[0]
		assert '[Customer sent a photo. Closest catalog products: "Red Kurta" (similarity 0.90): ' in self.questions[0]

	def test_matches_keep_score_order(self):
		second = Product.objects.create(kb=self.kb, name="Green Kurta", price=100, status="active", description="x" * 400)
		self.search.side_effect = lambda collection, vector: [(str(second.id), 0.91), (str(self.product.id), 0.88)]
		handle_facebook_message(fb_payload("m1"))
		self.answer_all()
		note = (
			f'[Customer sent a photo. Closest catalog products: "Green Kurta" (similarity 0.91): {"x" * 300}; '
			'"Red Kurta" (similarity 0.88): Cotton kurta in red, sizes S to XL]'
		)
		assert note in self.questions[0], self.questions[0]

	def test_unreadable_photo_still_replies(self):
		self.fetch.side_effect = MediaError("download failed")
		handle_facebook_message(fb_payload("m1"))
		with self.assertLogs("channels_app.image_reply", "WARNING"):
			self.answer_all()
		assert UNCHECKED in self.questions[0]
		assert len(self.outbound()) == 1
		self.send.assert_called_once()
		assert not self.searches().exists()

	def test_unexpected_error_still_replies(self):
		self.fetch.side_effect = RuntimeError("decrypt failed")
		handle_facebook_message(fb_payload("m1"))
		with self.assertLogs("channels_app.image_reply", "WARNING"):
			self.answer_all()
		assert UNCHECKED in self.questions[0]
		assert len(self.outbound()) == 1
		assert not self.searches().exists()

	def test_not_an_image_still_replies(self):
		self.fetch.return_value = b"not an image"
		handle_facebook_message(fb_payload("m1"))
		self.answer_all()
		assert UNCHECKED in self.questions[0]
		self.embed.assert_not_called()
		assert not self.searches().exists()

	def test_search_error_still_replies(self):
		self.search.side_effect = ConnectionError("qdrant down")
		handle_facebook_message(fb_payload("m1"))
		self.answer_all()
		assert UNCHECKED in self.questions[0]
		assert not self.searches().exists()

	def test_soft_time_limit_sends_fallback(self):
		self.fetch.side_effect = SoftTimeLimitExceeded()
		handle_facebook_message(fb_payload("m1"))
		with self.assertLogs("channels_app.reply", "WARNING"):
			self.answer_all()
		self.gen.assert_not_called()
		out = self.outbound()
		assert len(out) == 1 and out[0].text == FALLBACK_REPLY
		self.send.assert_called_once()
		assert Message.objects.get(external_message_id="m1").status == "completed"
		assert not self.searches().exists()

	def test_soft_time_limit_in_download_sends_fallback(self):
		# The real fetch path: the soft limit raised inside requests must not become a MediaError
		with mock.patch("channels_app.image_reply.fetch_photo", media.fetch_photo), \
				mock.patch("channels_app.media.requests.get", side_effect=SoftTimeLimitExceeded()):
			handle_facebook_message(fb_payload("m1"))
			with self.assertLogs("channels_app.reply", "WARNING"):
				self.answer_all()
		self.gen.assert_not_called()
		out = self.outbound()
		assert len(out) == 1 and out[0].text == FALLBACK_REPLY
		self.send.assert_called_once()
		assert not UsageEvent.objects.exists()

	def test_soft_time_limit_still_hands_off(self):
		self.fetch.side_effect = SoftTimeLimitExceeded()
		handle_facebook_message(fb_payload("m1", text="can I talk to a person"))
		self.answer_all()
		self.gen.assert_not_called()
		assert self.outbound()[0].text == settings.HANDOFF_MESSAGE

	def test_at_most_three_photos_per_message(self):
		handle_facebook_message(fb_payload("m1", photos=4))
		self.answer_all()
		assert self.fetch.call_count == 3 and self.searches().count() == 3
		assert self.questions[0].count('"Red Kurta"') == 3

	def test_at_most_five_photos_per_turn(self):
		for i in range(7):
			handle_facebook_message(fb_payload(f"m{i}"))
		self.answer_all()
		assert self.gen.call_count == 1
		assert self.fetch.call_count == 5 and self.searches().count() == 5
		assert self.questions[0].count('"Red Kurta"') == 5 and self.questions[0].count(UNCHECKED) == 2

	def assert_answered_without_photo_check(self):
		self.fetch.assert_not_called()
		assert not UsageEvent.objects.exists()
		assert self.gen.call_count == 1 and "[Message includes attachments: image]" in self.questions[0], self.questions
		assert len(self.outbound()) == 1
		self.send.assert_called_once()

	def test_operator_revoked_before_reply_skips_photo_check(self):
		handle_facebook_message(fb_payload("m1"))
		self.project.image_search_allowed = False
		self.project.save()
		self.answer_all()
		self.assert_answered_without_photo_check()

	def test_channel_turned_off_before_reply_skips_photo_check(self):
		handle_facebook_message(fb_payload("m1"))
		tasks.get_facebook_credential.return_value.image_search_enabled = False
		self.answer_all()
		self.assert_answered_without_photo_check()

	def test_channel_toggle_error_counts_as_off(self):
		handle_facebook_message(fb_payload("m1"))
		cred = tasks.get_facebook_credential.return_value
		tasks.get_facebook_credential.side_effect = [RuntimeError("db down"), cred]  # the toggle check, then the send
		with self.assertLogs("channels_app.image_reply", "WARNING"):
			self.answer_all()
		self.assert_answered_without_photo_check()

	@mock.patch("channels_app.tasks.send_whatsapp_text", return_value={"message_id": "wamid.out"})
	def test_whatsapp_turned_off_before_reply_skips_photo_check(self, send_wa):
		wa = ChannelCredential.objects.create(
			project=self.project, channel="whatsapp", knowledge_base=self.kb, image_search_enabled=True,
			credentials={"phone_number_id": "pn-1", "access_token": "wa-tok", "verify_token": "v", "app_secret": "s"},
		)
		handle_whatsapp_message(wa_payload("wamid.1", caption="price?"))
		wa.image_search_enabled = False
		wa.save()
		self.answer_all()
		self.fetch.assert_not_called()
		assert not UsageEvent.objects.exists()
		assert self.gen.call_count == 1
		send_wa.assert_called_once()

	def test_sticker_is_ignored(self):
		handle_facebook_message(fb_payload("m1", sticker=True))
		self.assert_ignored("m1")

	def test_channel_off_ignores_photo(self):
		self.cred.image_search_enabled = False
		self.cred.save()
		handle_facebook_message(fb_payload("m1"))
		self.assert_ignored("m1")

	def test_toggle_is_per_connection(self):
		ChannelCredential.objects.create(
			project=self.project, channel="facebook", knowledge_base=self.kb, is_active=True, image_search_enabled=False,
			credentials={"page_id": "page-2", "page_access_token": "tok2"},
		)
		handle_facebook_message(fb_payload("m1", recipient="page-1"))
		handle_facebook_message(fb_payload("m2", recipient="page-2"))
		assert [m.external_message_id for m in self.scheduled] == ["m1"]
		b = Message.objects.get(external_message_id="m2")
		assert b.status == "completed" and "photo_reply" not in b.meta

	def test_tenant_off_ignores_photo(self):
		self.project.image_search_enabled = False
		self.project.save()
		handle_facebook_message(fb_payload("m1"))
		self.assert_ignored("m1")

	def test_operator_disallowed_ignores_photo(self):
		self.project.image_search_allowed = False
		self.project.save()
		handle_facebook_message(fb_payload("m1"))
		self.assert_ignored("m1")

	@override_settings(IMAGE_SEARCH_ENABLED=False)
	def test_master_off_ignores_photo(self):
		handle_facebook_message(fb_payload("m1"))
		self.assert_ignored("m1")

	def test_photo_with_text_is_answered_when_on(self):
		handle_facebook_message(fb_payload("m1", text="how much?"))
		self.answer_all()
		assert self.questions[0].startswith("how much? [Customer sent a photo.")

	def test_photo_with_text_is_not_answered_when_off(self):
		self.cred.image_search_enabled = False
		self.cred.save()
		handle_facebook_message(fb_payload("m1", text="how much?"))
		self.assert_ignored("m1")

	@mock.patch("channels_app.tasks.send_whatsapp_text", return_value={"message_id": "wamid.out"})
	def test_whatsapp_caption_is_text(self, send_wa):
		ChannelCredential.objects.create(
			project=self.project, channel="whatsapp", knowledge_base=self.kb, image_search_enabled=True,
			credentials={"phone_number_id": "pn-1", "access_token": "wa-tok", "verify_token": "v", "app_secret": "s"},
		)
		handle_whatsapp_message(wa_payload("wamid.1", caption="price?"))
		msg = Message.objects.get(external_message_id="wamid.1")
		assert msg.text == "price?" and msg.status == "pending"
		assert msg.meta["photo_reply"] is True and msg.meta["phone_number_id"] == "pn-1"
		self.answer_all()
		assert self.questions[0].startswith("price?"), self.questions[0]
		assert '"Red Kurta" (similarity 0.93)' in self.questions[0]
		send_wa.assert_called_once()

	def test_whatsapp_caption_ignored_when_off(self):
		ChannelCredential.objects.create(
			project=self.project, channel="whatsapp", knowledge_base=self.kb, image_search_enabled=False,
			credentials={"phone_number_id": "pn-1", "access_token": "wa-tok", "verify_token": "v", "app_secret": "s"},
		)
		handle_whatsapp_message(wa_payload("wamid.1", caption="price?"))
		msg = Message.objects.get(external_message_id="wamid.1")
		assert msg.text == "" and msg.status == "completed" and "photo_reply" not in msg.meta
		assert self.scheduled == []

	def test_photo_then_text_burst_answers_once(self):
		handle_facebook_message(fb_payload("m1"))
		handle_facebook_message(fb_payload("m2", text="do you have it in blue?", photos=0))
		assert len(self.scheduled) == 2
		self.answer_all()
		assert self.gen.call_count == 1
		assert '"Red Kurta"' in self.questions[0] and "do you have it in blue?" in self.questions[0]
		assert len(self.outbound()) == 1
		assert set(Message.objects.filter(direction="in").values_list("status", flat=True)) == {"completed"}

	def test_text_then_photo_burst_answers_once(self):
		handle_facebook_message(fb_payload("m1", text="hi", photos=0))
		handle_facebook_message(fb_payload("m2"))
		self.answer_all()
		assert self.gen.call_count == 1
		assert "hi" in self.questions[0] and '"Red Kurta"' in self.questions[0]
		assert len(self.outbound()) == 1

	def test_retry_does_not_refetch(self):
		note = '[Customer sent a photo. Closest catalog products: "Red Kurta" (similarity 0.93): earlier]'
		msg = self.inbound(
			"", has_attachments=True, status="pending",
			attachment_info=[{"type": "image", "url": PHOTO_URL, "payload": {"url": PHOTO_URL}}],
			meta={"photo_reply": True, "photo_matches": note},
		)
		self.run_reply(msg)
		self.fetch.assert_not_called()
		assert not self.searches().exists()
		assert note in self.questions[0]

	def test_note_stays_in_history(self):
		handle_facebook_message(fb_payload("m1"))
		self.answer_all()
		handle_facebook_message(fb_payload("m2", text="thanks", photos=0))
		self.run_reply(self.scheduled[-1])
		assert any('"Red Kurta" (similarity 0.93)' in h for h in self.histories[-1])
		assert self.fetch.call_count == 1


class PhotoPromptTest(SimpleTestCase):
	def test_system_prompt_explains_photo_notes(self):
		assert (
			"- A message may note that the customer sent a photo and list the closest catalog products with a similarity "
			"score; answer about the best match, mention others only if their scores are close, and if no product matched "
			"or the photo could not be checked, say you couldn't identify it and ask for the product name or a clearer photo\n"
		) in build_system_prompt()

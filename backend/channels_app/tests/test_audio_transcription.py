from decimal import Decimal
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import TestCase, override_settings

import agents.tests
from accounts.models import Project
from agents.transcription import Transcript
from billing import service
from billing.models import ActionPrice, CreditTransaction
from channels_app import media, media_store
from channels_app.models import Conversation, CustomerContact, Message
from core.tests.utils import fresh_storage
from knowledge.models import KnowledgeBase

FIXTURES = Path(agents.tests.__file__).parent / "fixtures"


class AudioGateTest(TestCase):
	def setUp(self):
		self.project = Project.objects.create(name="P", slug="p-gate")

	def test_off_by_default(self):
		assert media.wants_audio_reply(self.project, [{"type": "audio"}]) is False

	@override_settings(TRANSCRIPTION_ENABLED=True)
	def test_on_for_every_project_when_the_allowlist_is_empty(self):
		assert media.wants_audio_reply(self.project, [{"type": "audio"}]) is True

	@override_settings(TRANSCRIPTION_ENABLED=True, TRANSCRIPTION_PROJECTS=["other-slug"])
	def test_allowlist_limits_the_pilot(self):
		assert media.wants_audio_reply(self.project, [{"type": "audio"}]) is False

	def test_photos_are_not_audio(self):
		with override_settings(TRANSCRIPTION_ENABLED=True):
			assert media.wants_audio_reply(self.project, [{"type": "image"}]) is False


def tr(text="hi", seconds=3):
	return Transcript(text, seconds, None, "m", {})


@fresh_storage
@override_settings(TRANSCRIPTION_ENABLED=True)
class TranscribeSuccessTest(TestCase):
	def setUp(self):
		self.project = Project.objects.create(name="P", slug="p-tr")
		kb = KnowledgeBase.objects.create(project=self.project, name="kb", vector_collection="vc-tr")
		contact = CustomerContact.objects.create(project=self.project, platform="whatsapp", external_id="1", display_name="Ann")
		conv = Conversation.objects.create(project=self.project, contact=contact, knowledge_base=kb)
		key = default_storage.save("chat_media/p-tr/x.ogg", ContentFile((FIXTURES / "voice.ogg").read_bytes()))
		self.msg = Message.objects.create(
			project=self.project, contact=contact, conversation=conv, direction="in", text="", status="pending",
			has_attachments=True, attachment_info=[{"type": "audio", "path": key}],
		)

	def test_success_appends_text_and_charges_per_seconds(self):
		with mock.patch.object(media_store, "transcribe", return_value=Transcript("hello there", 7, Decimal("0.001"), "elevenlabs/scribe-v2", {})), \
		     mock.patch.object(media_store, "schedule_reply") as scheduled:
			media_store.transcribe_attachment(str(self.msg.id), 0)
		self.msg.refresh_from_db()
		seconds = self.msg.meta["transcription"]["0"]["seconds"]
		assert self.msg.text == "hello there"
		assert self.msg.meta["transcript_texts"]["0"] == "hello there"
		assert self.msg.meta["transcription"]["0"]["started_at"]
		assert self.msg.meta["transcription"]["0"]["status"] == "transcribed"
		txn = CreditTransaction.objects.get(idempotency_key=f"transcription:{self.msg.id}:0")
		assert txn.amount == Decimal(-((seconds + 4) // 5))  # ceil(seconds / 5), 1 credit each
		assert service.balance_of(self.project) == txn.amount
		assert scheduled.called

	def test_price_is_applied_once(self):
		ActionPrice.objects.filter(action="transcription").update(credits=Decimal("2"))
		with mock.patch.object(media_store, "transcribe", return_value=tr()):
			media_store.transcribe_attachment(str(self.msg.id), 0)
		self.msg.refresh_from_db()
		units = self.msg.meta["transcription"]["0"]["units"]
		txn = CreditTransaction.objects.get(idempotency_key=f"transcription:{self.msg.id}:0")
		assert txn.amount == Decimal(-2 * units)  # not -4 * units

	def test_free_price_transcribes_without_any_rows(self):
		ActionPrice.objects.filter(action="transcription").update(credits=Decimal("0"))
		with mock.patch.object(media_store, "transcribe", return_value=tr()):
			media_store.transcribe_attachment(str(self.msg.id), 0)
		self.msg.refresh_from_db()
		assert self.msg.meta["transcription"]["0"]["status"] == "transcribed"
		assert not CreditTransaction.objects.filter(action="transcription").exists()

	def test_bin_extension_is_transcribed_from_its_bytes(self):
		key = default_storage.save("chat_media/p/x.bin", ContentFile((FIXTURES / "voice_messenger.mp3").read_bytes()))
		Message.objects.filter(id=self.msg.id).update(attachment_info=[{"type": "audio", "path": key}])
		with mock.patch.object(media_store, "transcribe", return_value=tr()) as call:
			media_store.transcribe_attachment(str(self.msg.id), 0)
		assert call.call_args.args[1] == "mp3"

	def test_unstored_audio_fails_without_a_charge(self):
		Message.objects.filter(id=self.msg.id).update(attachment_info=[{"type": "audio", "url": "https://x/a.mp4"}])
		with mock.patch.object(media_store, "transcribe") as call, \
		     mock.patch.object(media_store, "schedule_reply") as scheduled:
			media_store.transcribe_attachment(str(self.msg.id), 0)
		call.assert_not_called()
		self.msg.refresh_from_db()
		assert self.msg.meta["transcription"]["0"]["status"] == "failed"
		assert not CreditTransaction.objects.exists()
		assert scheduled.called

	def test_unaffordable_ends_without_a_call(self):
		with mock.patch.object(media_store, "charge_if_affordable", side_effect=media_store.InsufficientCredits(Decimal("3"), Decimal("-50"))), \
		     mock.patch.object(media_store, "schedule_reply") as scheduled, \
		     mock.patch.object(media_store, "transcribe") as call:
			media_store.transcribe_attachment(str(self.msg.id), 0)
		call.assert_not_called()
		self.msg.refresh_from_db()
		assert self.msg.meta["transcription"]["0"]["status"] == "unaffordable"
		assert not CreditTransaction.objects.filter(idempotency_key=f"transcription:{self.msg.id}:0").exists()
		assert scheduled.called

	def test_unexpected_error_ends_failed_refunded_and_replied(self):
		with mock.patch.object(media_store, "start_attempt", side_effect=RuntimeError("db down")), \
		     mock.patch.object(media_store, "transcribe") as call, \
		     mock.patch.object(media_store, "schedule_reply") as scheduled:
			media_store.transcribe_attachment(str(self.msg.id), 0)
		call.assert_not_called()
		self.msg.refresh_from_db()
		assert self.msg.meta["transcription"]["0"]["status"] == "failed"
		assert service.balance_of(self.project) == Decimal("0.00")
		assert scheduled.called

	def test_task_is_routed_to_the_replies_queue(self):
		assert settings.CELERY_TASK_ROUTES["channels_app.media_store.transcribe_attachment"] == {"queue": "replies"}

	def test_typed_text_is_preserved(self):
		Message.objects.filter(id=self.msg.id).update(text="see this")
		with mock.patch.object(media_store, "transcribe", return_value=tr("and this")):
			media_store.transcribe_attachment(str(self.msg.id), 0)
		self.msg.refresh_from_db()
		assert self.msg.text == "see this and this"

	def test_charge_happens_before_the_provider_call(self):
		order = []
		with mock.patch.object(media_store, "charge_if_affordable", side_effect=lambda *a, **k: order.append("charge")), \
		     mock.patch.object(media_store, "transcribe", side_effect=lambda *a, **k: (order.append("call"), tr())[1]):
			media_store.transcribe_attachment(str(self.msg.id), 0)
		assert order == ["charge", "call"]
		self.msg.refresh_from_db()
		assert self.msg.meta["transcription"]["0"]["status"] == "transcribed"

	def test_second_run_does_not_recharge_or_recall(self):
		with mock.patch.object(media_store, "transcribe", return_value=tr()) as call:
			media_store.transcribe_attachment(str(self.msg.id), 0)
			cache.delete(f"transcribe:{self.msg.id}:0")  # prove the state check stops it, not the claim
			media_store.transcribe_attachment(str(self.msg.id), 0)
		assert call.call_count == 1
		assert CreditTransaction.objects.filter(idempotency_key=f"transcription:{self.msg.id}:0").count() == 1

	def test_processing_from_a_dead_run_is_not_called_again(self):
		service.charge_if_affordable(self.project, "transcription", key=f"transcription:{self.msg.id}:0", units=3)
		media_store.update_message(str(self.msg.id), lambda m: m.meta.update(transcription={"0": {"status": "processing", "units": 3}}))
		with mock.patch.object(media_store, "transcribe") as call:
			media_store.transcribe_attachment(str(self.msg.id), 0)
		call.assert_not_called()
		self.msg.refresh_from_db()
		assert self.msg.meta["transcription"]["0"]["status"] == "failed_uncertain"
		assert service.balance_of(self.project) == Decimal("0.00")

	def test_two_attachments_charge_separately_and_join_in_order(self):
		att = list(self.msg.attachment_info) + [dict(self.msg.attachment_info[0])]
		Message.objects.filter(id=self.msg.id).update(attachment_info=att, meta={"audio_reply": True})
		texts = iter(["first", "second"])
		with mock.patch.object(media_store, "transcribe", side_effect=lambda *a, **k: tr(next(texts), 5)), \
		     mock.patch.object(media_store, "schedule_reply") as scheduled:
			media_store.transcribe_attachment(str(self.msg.id), 0)
			media_store.transcribe_attachment(str(self.msg.id), 1)
		self.msg.refresh_from_db()
		assert self.msg.text == "first second"
		assert CreditTransaction.objects.filter(action="transcription").count() == 2
		assert scheduled.call_count == 1

	def _two_voice_notes(self):
		att = [{"type": "audio", "path": self.msg.attachment_info[0]["path"]}, {"type": "audio", "url": "https://x/a.mp4"}]
		Message.objects.filter(id=self.msg.id).update(attachment_info=att, meta={"audio_reply": True})

	@override_settings(TRANSCRIPTION_ENABLED=True)
	def test_store_message_media_queues_one_task_per_voice_note(self):
		self._two_voice_notes()
		with mock.patch.object(media_store.transcribe_attachment, "delay") as delay, 		     mock.patch.object(media_store, "schedule_reply") as scheduled:
			media_store.store_message_media(str(self.msg.id), True)
		assert [c.args for c in delay.call_args_list] == [(str(self.msg.id), 0), (str(self.msg.id), 1)]
		scheduled.assert_not_called()

	@override_settings(TRANSCRIPTION_ENABLED=True)
	def test_store_message_media_ignores_audio_without_audio_reply(self):
		self._two_voice_notes()
		Message.objects.filter(id=self.msg.id).update(meta={})
		with mock.patch.object(media_store.transcribe_attachment, "delay") as delay, mock.patch.object(media_store, "schedule_reply") as scheduled:
			media_store.store_message_media(str(self.msg.id), True)
		delay.assert_not_called()
		assert scheduled.called

	@override_settings(TRANSCRIPTION_ENABLED=False)
	def test_store_message_media_replies_directly_when_the_flag_is_off(self):
		self._two_voice_notes()
		with mock.patch.object(media_store.transcribe_attachment, "delay") as delay, 		     mock.patch.object(media_store, "schedule_reply") as scheduled:
			media_store.store_message_media(str(self.msg.id), True)
		delay.assert_not_called()
		assert scheduled.called

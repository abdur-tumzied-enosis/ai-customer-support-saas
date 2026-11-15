from datetime import timedelta
from decimal import Decimal
from unittest import mock

from celery.exceptions import SoftTimeLimitExceeded
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import Project
from agents.transcription import Transcript, TranscriptionError
from billing import service
from billing.models import CreditTransaction
from channels_app import media_store
from channels_app.models import Conversation, CustomerContact, Message
from channels_app.tests.test_audio_transcription import FIXTURES
from core.tests.utils import fresh_storage
from knowledge.models import KnowledgeBase
from usage.models import UsageEvent


def age(msg, **delta):
	Message.objects.filter(id=msg.id).update(created_at=timezone.now() - timedelta(**(delta or {"minutes": 20})))


@fresh_storage
@override_settings(TRANSCRIPTION_ENABLED=True)
class TranscriptionFailureTest(TestCase):
	def setUp(self):
		self.project = Project.objects.create(name="P", slug="p-fail")
		kb = KnowledgeBase.objects.create(project=self.project, name="kb", vector_collection="vc-fail")
		self.contact = CustomerContact.objects.create(project=self.project, platform="whatsapp", external_id="1", display_name="Ann")
		self.conv = Conversation.objects.create(project=self.project, contact=self.contact, knowledge_base=kb)
		self.key = default_storage.save("chat_media/p-fail/x.ogg", ContentFile((FIXTURES / "voice.ogg").read_bytes()))
		self.msg = self.make()
		patcher = mock.patch.object(media_store, "schedule_reply")
		patcher.start()
		self.addCleanup(patcher.stop)

	def make(self):
		return Message.objects.create(
			project=self.project, contact=self.contact, conversation=self.conv, direction="in", text="", status="pending",
			has_attachments=True, attachment_info=[{"type": "audio", "path": self.key}],
		)

	def charge(self, msg):
		service.charge_if_affordable(self.project, "transcription", key=f"transcription:{msg.id}:0", units=3)

	def set_state(self, msg, status):
		msg.meta = {"transcription": {"0": {"status": status, "units": 3}}}
		msg.save(update_fields=["meta"])

	def state(self, msg=None):
		msg = msg or self.msg
		msg.refresh_from_db()
		return msg.meta["transcription"]["0"]["status"]

	def test_empty_transcript_is_not_billed(self):
		with mock.patch.object(media_store, "transcribe", return_value=Transcript("   ", 0, Decimal("0.001"), "m", {})):
			media_store.transcribe_attachment(str(self.msg.id), 0)
		assert self.state() == "empty"
		assert service.balance_of(self.project) == Decimal("0.00")
		assert CreditTransaction.objects.filter(kind="adjust").count() == 1

	def test_provider_error_is_refunded_and_attempt_recorded(self):
		with mock.patch.object(media_store, "transcribe", side_effect=TranscriptionError("500")):
			media_store.transcribe_attachment(str(self.msg.id), 0)
		assert self.state() == "failed"
		assert service.balance_of(self.project) == Decimal("0.00")
		attempt = UsageEvent.objects.get(kind="transcription")
		assert (attempt.cost_usd, attempt.metadata["status"]) == (None, "failed")

	def test_soft_time_limit_mid_call_is_failed_and_refunded(self):
		with mock.patch.object(media_store, "transcribe", side_effect=SoftTimeLimitExceeded()):
			media_store.transcribe_attachment(str(self.msg.id), 0)
		assert self.state() == "failed"
		assert service.balance_of(self.project) == Decimal("0.00")
		assert CreditTransaction.objects.filter(kind="adjust").count() == 1

	def test_refund_without_a_charge_row_grants_nothing(self):
		media_store.refund_transcription(self.project, str(self.msg.id), 0, "test")
		assert not CreditTransaction.objects.exists()

	def test_a_refunded_failure_is_never_called_again(self):
		with mock.patch.object(media_store, "transcribe", side_effect=TranscriptionError("500")) as call:
			media_store.transcribe_attachment(str(self.msg.id), 0)
			cache.delete(f"transcribe:{self.msg.id}:0")
			media_store.transcribe_attachment(str(self.msg.id), 0)
		assert call.call_count == 1
		assert CreditTransaction.objects.filter(kind="adjust").count() == 1
		assert service.balance_of(self.project) == Decimal("0.00")

	def test_a_lost_cache_claim_cannot_double_call(self):
		# the first run already set `processing`; the second one has a stale view of the row and a lost cache claim
		real = media_store._claim_processing
		first = {}

		def stale_then_claim(message_id, index, **fields):
			if not first:
				first["done"] = real(message_id, index)  # a concurrent run wins the row lock first
			return real(message_id, index, **fields)

		with mock.patch.object(media_store, "_claim_processing", side_effect=stale_then_claim), \
		     mock.patch.object(media_store, "transcribe", return_value=Transcript("hi", 3, None, "m", {})) as call:
			media_store.transcribe_attachment(str(self.msg.id), 0)
		assert first["done"] is True
		call.assert_not_called()
		assert not CreditTransaction.objects.filter(kind="charge").exists()

	def test_claim_is_refused_when_a_state_exists(self):
		assert media_store._claim_processing(str(self.msg.id), 0) is True
		assert media_store._claim_processing(str(self.msg.id), 0) is False

	def test_sweep_refunds_a_charge_whose_call_never_returned(self):
		self.charge(self.msg)
		self.set_state(self.msg, "processing")
		age(self.msg)
		media_store.reconcile_transcriptions()
		assert self.state() == "failed_uncertain"
		assert service.balance_of(self.project) == Decimal("0.00")

	def test_sweep_ignores_messages_older_than_two_hours(self):
		self.charge(self.msg)
		self.set_state(self.msg, "processing")
		age(self.msg, hours=3)
		media_store.reconcile_transcriptions()
		assert self.state() == "processing"

	def test_sweep_leaves_a_transcribed_attachment_alone(self):
		self.set_state(self.msg, "transcribed")
		age(self.msg)
		with mock.patch.object(media_store, "refund_transcription") as refund:
			media_store.reconcile_transcriptions()
		refund.assert_not_called()
		assert self.state() == "transcribed"

	def test_sweep_refunds_a_failure_with_a_missing_refund_row(self):
		self.charge(self.msg)
		self.set_state(self.msg, "failed")
		age(self.msg)
		media_store.reconcile_transcriptions()
		media_store.reconcile_transcriptions()  # idempotent
		assert service.balance_of(self.project) == Decimal("0.00")
		assert CreditTransaction.objects.filter(kind="adjust").count() == 1

	def test_sweep_leaves_an_already_refunded_failure_alone(self):
		self.charge(self.msg)
		media_store.refund_transcription(self.project, str(self.msg.id), 0, "test")
		self.set_state(self.msg, "failed_uncertain")
		age(self.msg)
		with mock.patch.object(media_store, "refund_transcription") as refund:
			media_store.reconcile_transcriptions()
		refund.assert_not_called()

	def test_a_failing_refund_does_not_stop_the_sweep(self):
		other = self.make()
		for m in (self.msg, other):
			self.charge(m)
			self.set_state(m, "processing")
			age(m)
		real, calls = media_store.grant, []

		def flaky(*a, **kw):
			calls.append(1)
			if len(calls) == 1:
				raise RuntimeError("db")
			return real(*a, **kw)

		with mock.patch.object(media_store, "grant", side_effect=flaky):
			media_store.reconcile_transcriptions()
		assert self.state(self.msg) == "failed_uncertain" and self.state(other) == "failed_uncertain"
		assert CreditTransaction.objects.filter(kind="adjust").count() == 1
		media_store.reconcile_transcriptions()  # the failed refund is retried by the failure branch
		assert CreditTransaction.objects.filter(kind="adjust").count() == 2

	def test_sweep_skips_a_processing_entry_with_a_fresh_started_at(self):
		self.charge(self.msg)
		self.msg.meta = {"transcription": {"0": {"status": "processing", "units": 3, "started_at": timezone.now().isoformat()}}}
		self.msg.save(update_fields=["meta"])
		age(self.msg)
		with mock.patch.object(media_store, "refund_transcription") as refund:
			media_store.reconcile_transcriptions()
		refund.assert_not_called()
		assert self.state() == "processing"

	def test_catch_all_does_not_overwrite_a_transcribed_entry(self):
		def other_run_wins(*a, **k):
			media_store._move_if_processing(str(self.msg.id), 0, "transcribed")
			raise RuntimeError("boom")

		with mock.patch.object(media_store, "transcribe", side_effect=other_run_wins):
			media_store.transcribe_attachment(str(self.msg.id), 0)
		assert self.state() == "transcribed"
		assert not CreditTransaction.objects.filter(kind="adjust").exists()

	def swept_during(self, effect):
		"""Run the task; inside the provider call the sweep marks the note failed_uncertain and refunds it."""
		def call(*a, **k):
			media_store._move_if_processing(str(self.msg.id), 0, "failed_uncertain")
			media_store.refund_transcription(self.project, str(self.msg.id), 0, "interrupted")
			return effect()

		with mock.patch.object(media_store, "transcribe", side_effect=call):
			media_store.transcribe_attachment(str(self.msg.id), 0)
		assert self.state() == "failed_uncertain"
		assert CreditTransaction.objects.filter(kind="adjust").count() == 1
		assert service.balance_of(self.project) == Decimal("0.00")

	def test_late_success_after_a_sweep_is_dropped(self):
		self.swept_during(lambda: Transcript("late words", 3, None, "m", {}))
		assert "late words" not in self.msg.text
		assert "0" not in self.msg.meta.get("transcript_texts", {})

	def test_late_provider_error_after_a_sweep_does_not_refund_again(self):
		def boom():
			raise TranscriptionError("500")
		self.swept_during(boom)

	def test_a_run_that_lost_the_claim_does_not_move_anothers_processing_entry(self):
		def other_run_claims_then_this_one_fails(*a, **k):
			media_store._claim_processing(str(self.msg.id), 0)
			self.charge(self.msg)
			raise RuntimeError("boom")

		with mock.patch.object(media_store, "read_stored", side_effect=other_run_claims_then_this_one_fails):
			media_store.transcribe_attachment(str(self.msg.id), 0)
		assert self.state() == "processing"
		assert not CreditTransaction.objects.filter(kind="adjust").exists()

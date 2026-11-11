from decimal import Decimal
from agents.embeddings_api import Embedded
from unittest import mock

from celery.exceptions import SoftTimeLimitExceeded
from django.test import override_settings

from billing import service
from billing.models import CreditTransaction
from channels_app.models import Message
from channels_app.reply import redeliver as redeliver_task
from channels_app.tests.test_reply_pipeline import PipelineBase

PHOTO = {"has_attachments": True, "attachment_info": [{"type": "image", "payload": {}}], "meta": {"photo_reply": True}}


class ReplyBillingTest(PipelineBase):
	def setUp(self):
		super().setUp()
		service.grant(self.project, Decimal("100"), "adjust", key="seed")

	def rows(self, action=None):
		qs = CreditTransaction.objects.filter(wallet__project=self.project, kind="charge")
		return qs.filter(action=action) if action else qs

	def redeliver(self, out):
		redeliver_task.apply(args=[str(out.id)])

	def test_delivered_reply_charged_once(self):
		m = self.inbound("hello")
		self.run_reply(m)
		self.run_reply(m)
		out = self.outbound()[0]
		row = self.rows("reply").get()
		self.assertEqual((row.result_message_id, row.trigger_message_id), (out.id, m.id))
		self.assertEqual(out.meta["billable"], "reply")
		self.assertEqual(service.balance_of(self.project), Decimal("99"))

	def test_failed_then_redelivered_charged_once(self):
		self.send.return_value = {}
		with mock.patch("channels_app.reply.redeliver.apply_async"):
			self.run_reply(self.inbound("hello"))
		out = self.outbound()[0]
		self.assertEqual(self.rows().count(), 0)
		self.send.return_value = {"message_id": "mid.2"}
		self.redeliver(out)
		self.redeliver(out)
		self.assertEqual(self.rows("reply").count(), 1)

	def test_never_delivered_not_charged(self):
		self.send.return_value = {}
		with mock.patch("channels_app.reply.redeliver.apply_async"):
			self.run_reply(self.inbound("hello"))
		self.redeliver(self.outbound()[0])
		self.assertEqual(self.rows().count(), 0)

	def test_fallback_reply_not_charged_even_after_redeliver(self):
		self.gen.side_effect = SoftTimeLimitExceeded()
		self.send.return_value = {}
		with mock.patch("channels_app.reply.redeliver.apply_async"):
			self.run_reply(self.inbound("hello"))
		out = self.outbound()[0]
		self.assertNotIn("billable", out.meta)
		self.assertEqual(self.rows().count(), 0)
		self.send.return_value = {"message_id": "mid.2"}
		self.redeliver(out)
		out.refresh_from_db()
		self.assertEqual(out.status, "completed")
		self.assertNotIn("billable", out.meta)
		self.assertEqual(self.rows().count(), 0)

	def test_handoff_reply_not_charged(self):
		self.run_reply(self.inbound("can I talk to a person"))
		self.assertNotIn("billable", self.outbound()[0].meta)
		self.assertEqual(self.rows().count(), 0)

	def test_test_chat_free_and_never_blocked(self):
		self.contact.platform = "self"
		self.contact.save()
		service.grant(self.project, Decimal("-1100"), "adjust", key="drain")
		self.run_reply(self.inbound("hello"))
		self.assertEqual(self.gen.call_count, 1)
		self.assertEqual(self.rows().count(), 0)

	def test_out_of_credits_hands_off(self):
		service.grant(self.project, Decimal("-150"), "adjust", key="drain")
		self.run_reply(self.inbound("hello"))
		self.assertEqual(self.gen.call_count, 0)
		self.conv.refresh_from_db()
		self.assertEqual(self.conv.handoff_reason, "out of credits")
		self.assertEqual(self.rows().count(), 0)
		self.assertNotIn("billable", self.outbound()[0].meta)

	def test_precheck_error_fails_open(self):
		with mock.patch("channels_app.reply.shopper_blocked", side_effect=RuntimeError("db down")):
			self.run_reply(self.inbound("hello"))
		self.assertEqual(self.gen.call_count, 1)
		self.assertEqual(self.outbound()[0].status, "completed")

	@override_settings(IMAGE_SEARCH_ENABLED=True)
	def test_overdraft_counts_photo(self):
		self.project.image_search_allowed = True
		self.project.save()
		service.grant(self.project, Decimal("-149"), "adjust", key="drain")  # balance -49
		self.run_reply(self.inbound("", **PHOTO))
		self.assertEqual(self.gen.call_count, 0)
		self.conv.refresh_from_db()
		self.assertEqual(self.conv.handoff_reason, "out of credits")
		self.conv.handoff_at = None
		self.conv.handoff_reason = ""
		self.conv.save()
		self.run_reply(self.inbound("hello"))
		self.assertEqual(self.gen.call_count, 1)

	@override_settings(IMAGE_SEARCH_ENABLED=True)
	def test_photo_search_charged_even_if_reply_times_out(self):
		self.project.image_search_allowed = True
		self.project.save()
		patches = {"fetch_photo": b"x", "load_image": object(), "embed_image": Embedded([[1, 0]], 5, None, "cohere/embed-5-fast"), "search_products": []}
		for name, value in patches.items():
			mock.patch(f"channels_app.image_reply.{name}", return_value=value).start()
		self.gen.side_effect = SoftTimeLimitExceeded()
		m = self.inbound("", **PHOTO)
		self.run_reply(m)
		row = self.rows().get()
		self.assertEqual((row.action, row.trigger_message_id), ("photo_search", m.id))
		self.assertEqual(self.rows("reply").count(), 0)

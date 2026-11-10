from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from billing import service
from billing.models import CreditTransaction, CreditWallet
from billing.tasks import check_balances, reconcile
from channels_app.models import CustomerContact, Message
from channels_app.tests.test_reply_pipeline import PipelineBase


class ReconcileTest(PipelineBase):
	def setUp(self):
		super().setUp()
		service.grant(self.project, Decimal("100"), "adjust", key="seed")

	def reply(self, minutes=20, contact=None, external_id="mid", billable="reply"):
		msg = Message.objects.create(
			project=self.project, contact=contact or self.contact, conversation=self.conv, direction="out",
			sender="bot", external_message_id=external_id, meta={"billable": billable} if billable else {},
		)
		Message.objects.filter(pk=msg.pk).update(created_at=timezone.now() - timedelta(minutes=minutes))
		return msg

	def rows(self):
		return CreditTransaction.objects.filter(kind="charge", action="reply").count()

	def test_reconcile_charges_delivered_uncharged(self):
		self.reply()
		reconcile()
		self.assertEqual(self.rows(), 1)
		reconcile()
		self.assertEqual(self.rows(), 1)

	def test_reconcile_skips_undelivered(self):
		self.reply(external_id="")
		reconcile()
		self.assertEqual(self.rows(), 0)

	def test_reconcile_charges_widget_reply(self):
		widget = CustomerContact.objects.create(project=self.project, platform="widget", external_id="w1")
		self.reply(contact=widget, external_id="")
		reconcile()
		self.assertEqual(self.rows(), 1)

	def test_reconcile_skips_recent(self):
		self.reply(minutes=2)
		reconcile()
		self.assertEqual(self.rows(), 0)

	def test_reconcile_skips_old_and_unmarked(self):
		self.reply(minutes=8 * 24 * 60)
		self.reply(billable="")
		reconcile()
		self.assertEqual(self.rows(), 0)

	def test_check_balances_reports_mismatch(self):
		wallet = CreditWallet.objects.get(project=self.project)
		CreditWallet.objects.filter(pk=wallet.pk).update(balance=999)
		with self.assertLogs(level="ERROR"):
			bad = check_balances()
		self.assertEqual([w.pk for w in bad], [wallet.pk])
		wallet.refresh_from_db()
		self.assertEqual(wallet.balance, 999)

	def test_check_balances_clean(self):
		self.assertEqual(check_balances(), [])

import threading
from decimal import Decimal
from unittest import mock

from django.db import connection
from django.db.models import Sum
from django.test import TestCase, TransactionTestCase

from accounts.models import Project, ProjectUser, User
from billing import service
from billing.models import ACTIONS, ActionPrice, CreditPackage, CreditTransaction, CreditWallet, PricingSettings


class ModelTests(TestCase):
	def test_seed_has_every_action(self):
		self.assertEqual(set(ActionPrice.objects.values_list("action", flat=True)), set(ACTIONS))
		self.assertEqual(ActionPrice.objects.get(action="product_import").credits, Decimal("2"))

	def test_pricing_settings_singleton(self):
		PricingSettings(overdraft_credits=10).save()
		PricingSettings(overdraft_credits=10).save()
		self.assertEqual(PricingSettings.objects.count(), 1)
		self.assertEqual(PricingSettings.get().overdraft_credits, 10)

	def test_transaction_immutable(self):
		wallet = CreditWallet.objects.create(project_name="p")
		txn = CreditTransaction.objects.create(wallet=wallet, amount=Decimal("5"), balance_after=Decimal("5"), kind="grant")
		with self.assertRaises(ValueError):
			txn.save()
		with self.assertRaises(ValueError):
			txn.delete()

	def test_seed_packages(self):
		packages = CreditPackage.objects.filter(active=True)
		self.assertEqual(packages.count(), 3)
		self.assertEqual(list(packages.filter(highlighted=True).values_list("name", flat=True)), ["Growth"])
		self.assertEqual(CreditPackage.objects.get(name="Pro").bonus_credits, 6000)


class ServiceBase:
	def setUp(self):
		ActionPrice.objects.get_or_create(action="reply", defaults={"credits": Decimal("1")})
		self.project = Project.objects.create(name="Shop", slug="shop")
		self.admin = User.objects.create_user(username="a@x.com", email="a@x.com", password="pw-123456")
		ProjectUser.objects.create(project=self.project, user=self.admin, role="admin")
		patcher = mock.patch("billing.service.queue_email")
		self.mail = patcher.start()
		self.addCleanup(patcher.stop)


class ServiceTests(ServiceBase, TestCase):
	def test_charge_debits_price(self):
		txn = service.charge(self.project, "reply", key="reply:1")
		self.assertEqual(service.balance_of(self.project), Decimal("-1.00"))
		self.assertEqual((txn.amount, txn.balance_after, txn.kind, txn.action), (Decimal("-1"), Decimal("-1"), "charge", "reply"))

	def test_charge_same_key_once(self):
		first = service.charge(self.project, "reply", key="reply:1")
		again = service.charge(self.project, "reply", key="reply:1")
		self.assertEqual(first.pk, again.pk)
		self.assertEqual(CreditTransaction.objects.count(), 1)
		self.assertEqual(service.balance_of(self.project), Decimal("-1"))

	def test_free_action_writes_nothing(self):
		self.assertIsNone(service.charge(self.project, "photo_index", key="photo_index:x"))
		self.assertEqual(CreditTransaction.objects.count(), 0)

	def test_price_snapshot(self):
		txn = service.charge(self.project, "reply", key="reply:1")
		ActionPrice.objects.filter(action="reply").update(credits=3)
		txn.refresh_from_db()
		self.assertEqual(txn.amount, Decimal("-1"))

	def test_missing_price_row_uses_default(self):
		ActionPrice.objects.filter(action="reply").delete()
		with self.assertLogs("billing.service", "ERROR"):
			service.charge(self.project, "reply", key="reply:1")
		self.assertEqual(service.balance_of(self.project), Decimal("-1"))

	def test_grant_and_balance_matches_ledger(self):
		service.grant(self.project, Decimal("100"), "grant", key="g1")
		for i in range(3):
			service.charge(self.project, "reply", key=f"reply:{i}")
		wallet = service.wallet_for(self.project)
		self.assertEqual(wallet.balance, Decimal("97"))
		self.assertEqual(wallet.balance, wallet.transactions.aggregate(t=Sum("amount"))["t"])
		self.assertIsNone(service.grant(self.project, Decimal("100"), "grant", key="g1"))

	def test_charge_affordable_partial(self):
		service.grant(self.project, Decimal("5"), "grant")
		rows = [mock.Mock(pk=i) for i in range(1, 5)]
		create = mock.Mock()
		self.assertEqual(service.charge_affordable(self.project, "product_import", rows, create), 2)
		create.assert_called_once_with(rows[:2])
		self.assertEqual(service.balance_of(self.project), Decimal("1"))
		self.assertEqual(sorted(CreditTransaction.objects.filter(kind="charge").values_list("idempotency_key", flat=True)), ["product_import:1", "product_import:2"])

	def test_charge_affordable_never_negative(self):
		service.grant(self.project, Decimal("-3"), "adjust")
		create = mock.Mock()
		self.assertEqual(service.charge_affordable(self.project, "product_import", [mock.Mock(pk=1)], create), 0)
		create.assert_called_once_with([])

	def test_depletion_email_once_and_rearm(self):
		service.grant(self.project, Decimal("2"), "grant")
		service.charge(self.project, "reply", key="r1")
		self.assertEqual(self.mail.call_count, 0)
		service.charge(self.project, "reply", key="r2")
		self.assertEqual(self.mail.call_count, 1)
		self.assertEqual(self.mail.call_args.args[0], "a@x.com")
		service.charge(self.project, "reply", key="r3")
		self.assertEqual(self.mail.call_count, 1)
		service.grant(self.project, Decimal("5"), "grant")
		self.assertFalse(service.wallet_for(self.project).depleted_notified)
		service.charge(self.project, "reply", key="r4")
		service.charge(self.project, "reply", key="r5")
		service.charge(self.project, "reply", key="r6")
		self.assertEqual(self.mail.call_count, 1)
		service.charge(self.project, "reply", key="r7")
		self.assertEqual(self.mail.call_count, 2)

	def test_no_admins_logs_warning(self):
		ProjectUser.objects.all().update(role="member")
		service.grant(self.project, Decimal("1"), "grant")
		with self.assertLogs("billing.service", "WARNING"):
			service.charge(self.project, "reply", key="r1")
		self.assertEqual(self.mail.call_count, 0)

	def test_shopper_blocked(self):
		PricingSettings(overdraft_credits=50).save()
		service.grant(self.project, Decimal("-49"), "adjust")
		self.assertTrue(service.shopper_blocked(self.project, Decimal("2")))
		self.assertFalse(service.shopper_blocked(self.project, Decimal("1")))

	def test_insufficient_credits_response(self):
		resp = service.insufficient_credits_response(Decimal("2"))
		self.assertEqual(resp.status_code, 402)
		self.assertEqual(resp.data["error"]["code"], "insufficient_credits")
		self.assertIn("(2 needed)", resp.data["error"]["message"])


class ConcurrencyTests(ServiceBase, TransactionTestCase):
	def _run(self, n):
		def work(i):
			try:
				service.charge(self.project, "reply", key=f"reply:{i}")
			finally:
				connection.close()
		threads = [threading.Thread(target=work, args=(i,)) for i in range(n)]
		for t in threads:
			t.start()
		for t in threads:
			t.join()

	def test_concurrent_charges_consistent(self):
		service.wallet_for(self.project)
		self._run(10)
		wallet = service.wallet_for(self.project)
		self.assertEqual(wallet.balance, Decimal("-10"))
		self.assertEqual(wallet.balance, wallet.transactions.aggregate(t=Sum("amount"))["t"])

	def test_one_depletion_email_under_concurrency(self):
		service.grant(self.project, Decimal("1"), "grant")
		self._run(5)
		self.assertEqual(self.mail.call_count, 1)

from decimal import Decimal

from django.test import TestCase

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

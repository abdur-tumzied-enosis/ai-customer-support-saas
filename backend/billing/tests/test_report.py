from datetime import date, datetime, timezone as dt_tz
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.admin.sites import site
from django.test import TestCase
from django.urls import reverse

from accounts.models import Project
from billing import report, service
from billing.models import CreditTransaction, CreditWallet
from channels_app.models import Conversation
from knowledge.models import KnowledgeBase
from usage.models import UsageEvent


def at(txn_or_event, day):
	type(txn_or_event).objects.filter(pk=txn_or_event.pk).update(created_at=datetime(2026, 5, day, 12, tzinfo=dt_tz.utc))


class ReportTest(TestCase):
	def setUp(self):
		self.a = Project.objects.create(name="A", slug="a-rep")
		self.b = Project.objects.create(name="B", slug="b-rep")
		kb = KnowledgeBase.objects.create(project=self.a, name="kb", vector_collection="vc-rep")
		self.conv = Conversation.objects.create(project=self.a, knowledge_base=kb)
		self.other = Conversation.objects.create(project=self.a, knowledge_base=kb)

	def charge(self, project, n, day, key, conv=None):
		service.grant(project, Decimal("1000"), "grant", key=f"g{key}")
		t = service.charge(project, "reply", key=key, units=n, conversation=conv)
		at(t, day)

	def event(self, project, cost, day, conv=None):
		e = UsageEvent.objects.create(project=project, kind="chat_out", cost_usd=cost, conversation=conv)
		at(e, day)

	def test_coverage_sums_and_flags(self):
		self.charge(self.a, 1000, 10, "a1")  # 1000 credits * 0.005 = 5 USD
		self.event(self.a, Decimal("0.5"), 10)
		self.event(self.a, None, 11)
		self.charge(self.b, 1000, 10, "b1")
		self.event(self.b, Decimal("2"), 10)
		self.charge(self.b, 5, 20, "b-out")  # outside range
		data = report.coverage(date(2026, 5, 1), date(2026, 5, 15))
		rows = {r["project_name"]: r for r in data["rows"]}
		self.assertEqual(rows["A"]["credits_charged"], Decimal("1000"))
		self.assertEqual(rows["A"]["known_cost_usd"], Decimal("0.5"))
		self.assertEqual(rows["A"]["unpriced_events"], 1)
		self.assertEqual(rows["A"]["ratio"], Decimal("10"))
		self.assertFalse(rows["A"]["below_target"])
		self.assertEqual(rows["A"]["by_action"], {"reply": Decimal("1000")})
		self.assertEqual(rows["B"]["credits_charged"], Decimal("1000"))
		self.assertEqual(rows["B"]["ratio"], Decimal("2.5"))
		self.assertTrue(rows["B"]["below_target"])
		self.assertEqual(data["target"], Decimal("5"))

	def test_ratio_none_when_no_known_cost(self):
		self.charge(self.a, 10, 10, "a1")
		self.event(self.a, None, 10)
		row = report.coverage(date(2026, 5, 1), date(2026, 5, 31))["rows"][0]
		self.assertIsNone(row["ratio"])
		self.assertFalse(row["below_target"])

	def test_date_filter_inclusive_end(self):
		self.charge(self.a, 10, 15, "a1")
		self.assertEqual(len(report.coverage(date(2026, 5, 15), date(2026, 5, 15))["rows"]), 1)
		self.assertEqual(report.coverage(date(2026, 5, 16), date(2026, 5, 20))["rows"], [])

	def test_mismatched_reuses_check_balances(self):
		w = service.wallet_for(self.a)
		CreditWallet.objects.filter(pk=w.pk).update(balance=Decimal("7"))
		self.assertEqual([x.pk for x in report.coverage(date(2026, 5, 1), date(2026, 5, 2))["mismatched"]], [w.pk])

	def test_no_false_mismatch_with_many_transactions(self):
		self.charge(self.a, 3, 10, "m1")
		self.charge(self.a, 4, 10, "m2")
		self.assertEqual(report.coverage(date(2026, 5, 1), date(2026, 5, 31))["mismatched"], [])

	def test_conversation_totals(self):
		self.charge(self.a, 3, 10, "c1", conv=self.conv)
		self.charge(self.a, 4, 10, "c2", conv=self.other)
		self.event(self.a, Decimal("0.25"), 10, conv=self.conv)
		self.event(self.a, Decimal("9"), 10, conv=self.other)
		self.event(self.a, None, 10, conv=self.conv)
		self.assertEqual(report.conversation_totals(self.conv), (Decimal("3"), Decimal("0.25")))


class AdminTest(TestCase):
	def setUp(self):
		self.user = get_user_model().objects.create_superuser("root", "r@x.com", "pw")
		self.client.force_login(self.user)
		self.project = Project.objects.create(name="P", slug="p-adm")

	def test_admin_pages_load(self):
		from billing.models import PricingSettings
		PricingSettings.get()
		for model in [m for m in site._registry if m._meta.app_label in ("billing", "channels_app")]:
			url = reverse(f"admin:{model._meta.app_label}_{model._meta.model_name}_changelist")
			self.assertEqual(self.client.get(url).status_code, 200, url)
		self.assertEqual(self.client.get(reverse("admin:billing_creditwallet_coverage")).status_code, 200)
		self.assertEqual(self.client.get(reverse("admin:billing_creditwallet_coverage") + "?start=2026-01-01&end=2026-01-31").status_code, 200)

	def test_add_transaction_goes_through_grant(self):
		wallet = service.wallet_for(self.project)
		r = self.client.post(reverse("admin:billing_credittransaction_add"), {"wallet": wallet.pk, "kind": "grant", "amount": "25", "note": "promo"})
		self.assertEqual(r.status_code, 302, getattr(r, "context", None) and r.context["adminform"].form.errors)
		wallet.refresh_from_db()
		self.assertEqual(wallet.balance, Decimal("25"))
		txn = CreditTransaction.objects.get(wallet=wallet)
		self.assertEqual((txn.created_by, txn.balance_after, txn.note), (self.user, Decimal("25"), "promo"))

	def test_grant_amount_validation(self):
		wallet = service.wallet_for(self.project)
		url = reverse("admin:billing_credittransaction_add")
		for kind, amount in [("grant", "0"), ("grant", "-5"), ("adjust", "0")]:
			r = self.client.post(url, {"wallet": wallet.pk, "kind": kind, "amount": amount, "note": "n"})
			self.assertEqual(r.status_code, 200, (kind, amount))
		self.assertFalse(CreditTransaction.objects.exists())
		r = self.client.post(url, {"wallet": wallet.pk, "kind": "adjust", "amount": "-5", "note": "n"})
		self.assertEqual(r.status_code, 302)
		self.assertEqual(CreditTransaction.objects.get().amount, Decimal("-5"))

	def test_transaction_not_editable(self):
		wallet = service.wallet_for(self.project)
		txn = service.grant(self.project, Decimal("5"), "grant")
		# the app's exception middleware turns PermissionDenied into a 500, so assert denial rather than 403
		for name, data in [("change", {"note": "x"}), ("delete", {"post": "yes"})]:
			r = self.client.post(reverse(f"admin:billing_credittransaction_{name}", args=[txn.pk]), data)
			self.assertNotIn(r.status_code, (200, 302))
		txn.refresh_from_db()
		self.assertEqual(txn.note, "")

	def test_orphan_wallet_rejected(self):
		wallet = CreditWallet.objects.create(project=None, project_name="gone")
		r = self.client.post(reverse("admin:billing_credittransaction_add"), {"wallet": wallet.pk, "kind": "grant", "amount": "5", "note": "n"})
		self.assertEqual(r.status_code, 200)
		self.assertFalse(CreditTransaction.objects.exists())

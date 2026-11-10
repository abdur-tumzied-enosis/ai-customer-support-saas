from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from accounts.models import Project
from billing.models import CreditTransaction, CreditWallet
from usage.models import UsageEvent
from usage.report import ai_cost_report

TODAY = timezone.localdate()


class AiCostReportTests(TestCase):
	def setUp(self):
		self.project = Project.objects.create(name="P", slug="p-report")
		self.other = Project.objects.create(name="Q", slug="q-report")

	def event(self, **kw):
		kw.setdefault("project", self.project)
		kw.setdefault("kind", "chat_out")
		kw.setdefault("tokens", 0)
		return UsageEvent.objects.create(**kw)

	def report(self, **kw):
		return ai_cost_report(TODAY, TODAY, **kw)

	def test_known_by_source_split(self):
		self.event(cost_usd=Decimal("0.005"), cost_source="token_calculated", provider="google", model_name="g")
		self.event(cost_usd=Decimal("0.001"), cost_source="provider_reported", provider="openrouter", model_name="o/x")
		self.event(cost_usd=Decimal(0), cost_source="system_default", kind="embed")
		r = self.report()
		self.assertEqual(r["known_by_source"]["token_calculated"], Decimal("0.005"))
		self.assertEqual(r["known_by_source"]["provider_reported"], Decimal("0.001"))
		self.assertEqual(r["known_by_source"]["system_default"], Decimal(0))
		self.assertEqual(r["unknown_events"], 0)
		self.assertFalse(r["has_unknown"])

	def test_unknown_counted_not_zeroed(self):
		self.event(cost_usd=None, cost_source="unknown", provider="google", model_name="gemini-x", tokens=500)
		r = self.report()
		self.assertEqual((r["unknown_events"], r["unknown_tokens"]), (1, 500))
		self.assertTrue(r["has_unknown"])
		self.assertEqual(r["unmatched_models"], ["gemini-x"])

	def test_legacy_rows_separate(self):
		self.event(cost_usd=Decimal("0.2"))  # blank cost_source: written before snapshots existed
		r = self.report()
		self.assertEqual(r["legacy_events"], 1)
		self.assertEqual(r["legacy_usd"], Decimal("0.2"))
		self.assertEqual(r["known_by_source"]["provider_reported"], Decimal(0))
		self.assertFalse(any(m["provider"] == "" for m in r["models"]))  # legacy is not a model row with $0 known cost

	def test_models_table_tokens(self):
		self.event(kind="chat_in", tokens=1000, cost_usd=Decimal("0.003"), cost_source="token_calculated", provider="google", model_name="g")
		self.event(kind="chat_out", tokens=300, cost_usd=Decimal("0.0015"), cost_source="token_calculated", provider="google", model_name="g")
		m = next(x for x in self.report()["models"] if x["model_name"] == "g")
		self.assertEqual((m["input_tokens"], m["output_tokens"]), (1000, 300))
		self.assertEqual(m["known_usd"], Decimal("0.0045"))

	def test_date_and_project_filter(self):
		old = self.event(cost_usd=Decimal("1"), cost_source="token_calculated", model_name="g")
		UsageEvent.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=40))
		self.event(cost_usd=Decimal("2"), cost_source="token_calculated", model_name="g", project=self.other)
		self.event(cost_usd=Decimal("3"), cost_source="token_calculated", model_name="g")
		self.assertEqual(self.report()["known_by_source"]["token_calculated"], Decimal("5"))
		self.assertEqual(self.report(project_id=str(self.project.id))["known_by_source"]["token_calculated"], Decimal("3"))

	def test_credits_charged_debits_only(self):
		wallet = CreditWallet.objects.create(project=self.project, project_name="P", balance=Decimal("0"))
		CreditTransaction.objects.create(wallet=wallet, amount=Decimal("-3"), balance_after=Decimal("-3"), kind="charge", action="reply")
		CreditTransaction.objects.create(wallet=wallet, amount=Decimal("100"), balance_after=Decimal("97"), kind="grant")
		self.assertEqual(self.report()["credits_charged"], Decimal("3"))

	def test_unknown_or_invalid_project_id_is_empty_not_error(self):
		for bad in ("00000000-0000-0000-0000-000000000000", "not-a-uuid"):
			r = self.report(project_id=bad)
			self.assertEqual((r["models"], r["credits_charged"]), ([], Decimal(0)))

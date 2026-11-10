from decimal import Decimal

from django.test import TestCase

from accounts.models import Project
from usage.models import UsageEvent


class PricingSnapshotFieldsTest(TestCase):
	def setUp(self):
		self.project = Project.objects.create(name="P", slug="p-snap-fields")

	def test_snapshot_fields_round_trip(self):
		e = UsageEvent.objects.create(
			project=self.project, kind="chat_in", tokens=10, cost_usd=Decimal("0.001234"),
			provider="google", model_name="gemini-3.8-flash",
			price_in_per_mtok=Decimal("0.300000"), price_out_per_mtok=Decimal("2.500000"),
			cost_source="token_calculated",
		)
		e.refresh_from_db()
		self.assertEqual(e.price_in_per_mtok, Decimal("0.300000"))
		self.assertEqual(e.cost_source, "token_calculated")

	def test_legacy_event_defaults(self):
		e = UsageEvent.objects.create(project=self.project, kind="chat_in", tokens=1)
		self.assertEqual((e.cost_source, e.provider), ("", ""))
		self.assertIsNone(e.price_in_per_mtok)

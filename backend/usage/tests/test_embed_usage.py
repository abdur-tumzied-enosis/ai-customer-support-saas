from decimal import Decimal

from django.test import TestCase

from accounts.models import Project
from agents.models import AIModel
from usage.models import UsageEvent
from usage.utils import record_image_usage, record_model_usage


class RecordModelUsageTest(TestCase):
	def setUp(self):
		self.pid = str(Project.objects.create(name="P", slug="p-embed-usage").id)

	def _event(self):
		return UsageEvent.objects.get(project_id=self.pid)

	def test_reported_cost_is_stored(self):
		record_model_usage(self.pid, "embed", tokens=100, cost_usd=Decimal("0.00001"), model="cohere/embed-5-fast")
		e = self._event()
		self.assertEqual((e.kind, e.cost_usd, e.cost_source), ("embed", Decimal("0.00001"), "provider_reported"))
		self.assertEqual(e.model_name, "cohere/embed-5-fast")

	def test_calculated_from_ai_model_row(self):
		AIModel.objects.update_or_create(
			provider="openrouter", name="cohere/embed-5-fast",
			defaults={"input_price_per_mtok": Decimal("0.08"), "image_price_per_mtok": Decimal("0.40")},
		)
		record_model_usage(self.pid, "image", tokens=1000, cost_usd=None, model="cohere/embed-5-fast", image_tokens=500, op="index")
		e = self._event()
		self.assertEqual((e.cost_usd, e.cost_source), (Decimal("0.0002"), "token_calculated"))
		self.assertEqual(e.metadata, {"op": "index"})

	def test_unknown_without_row(self):
		record_model_usage(self.pid, "rerank", tokens=10, cost_usd=None, model="nope/none")
		e = self._event()
		self.assertIsNone(e.cost_usd)
		self.assertEqual((e.kind, e.cost_source), ("rerank", "unknown"))

	def test_never_raises(self):
		record_model_usage("not-a-uuid", "embed", tokens=1, cost_usd=None, model="x")

	def _seed(self):
		AIModel.objects.update_or_create(
			provider="openrouter", name="cohere/embed-5-fast",
			defaults={"input_price_per_mtok": Decimal("0.08"), "image_price_per_mtok": Decimal("0.40")},
		)

	def test_image_usage_prices_image_tokens_only(self):
		self._seed()
		record_image_usage(self.pid, "index", tokens=1200, cost_usd=None, model="cohere/embed-5-fast")
		e = self._event()
		self.assertEqual((e.tokens, e.cost_usd, e.cost_source), (1200, Decimal("0.00048"), "token_calculated"))  # not + 0.000096

	def test_no_usage_and_no_cost_is_unknown(self):
		self._seed()
		record_image_usage(self.pid, "search", tokens=0, cost_usd=None, model="cohere/embed-5-fast")
		record_model_usage(self.pid, "embed", tokens=0, cost_usd=None, model="cohere/embed-5-fast")
		for e in UsageEvent.objects.filter(project_id=self.pid):
			self.assertEqual((e.cost_usd, e.cost_source), (None, "unknown"))

	def test_image_usage_stores_tokens_and_cost(self):
		record_image_usage(self.pid, "search", tokens=1200, cost_usd=Decimal("0.00048"), model="cohere/embed-5-fast")
		e = self._event()
		self.assertEqual((e.kind, e.tokens, e.cost_usd), ("image", 1200, Decimal("0.00048")))
		self.assertEqual(e.metadata["op"], "search")

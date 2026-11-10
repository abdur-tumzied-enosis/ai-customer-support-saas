from decimal import Decimal

from django.test import TestCase
from langchain_core.messages import AIMessage

from accounts.models import Project
from agents.models import AIModel
from usage.models import UsageEvent
from usage.utils import llm_costs, llm_pricing, track_token_usage


def _google(name, tokens_in, tokens_out):
	return AIMessage(content="ok", response_metadata={"model_name": name},
		usage_metadata={"input_tokens": tokens_in, "output_tokens": tokens_out, "total_tokens": tokens_in + tokens_out})


class LlmPricingTest(TestCase):
	def setUp(self):
		self.project = Project.objects.create(name="P", slug="p-snap-pricing")
		AIModel.objects.create(provider="google", name="gemini-3.8-flash", input_price_per_mtok=Decimal("0.30"), output_price_per_mtok=Decimal("2.50"))

	def test_google_priced_snapshot(self):
		p = llm_pricing([_google("gemini-3.8-flash", 10000, 2000)])
		self.assertEqual(p["chat_out"]["cost_usd"], Decimal("0.005"))
		self.assertEqual(p["chat_in"]["cost_usd"], Decimal("0.003"))
		self.assertEqual(p["chat_out"]["cost_source"], "token_calculated")
		self.assertEqual(p["chat_out"]["provider"], "google")
		self.assertEqual(p["chat_out"]["price_out_per_mtok"], Decimal("2.50"))

	def test_google_unpriced_is_unknown_not_zero(self):
		p = llm_pricing([_google("gemini-unlisted", 10000, 2000)])
		self.assertIsNone(p["chat_out"]["cost_usd"])
		self.assertEqual(p["chat_out"]["cost_source"], "unknown")
		self.assertEqual(p["chat_out"]["provider"], "google")

	def test_openrouter_booked_on_output(self):
		msg = AIMessage(content="ok", response_metadata={"model_name": "deepseek/x", "cost": 0.00042}, usage_metadata={"input_tokens": 5, "output_tokens": 3, "total_tokens": 8})
		p = llm_pricing([msg])
		self.assertEqual(p["chat_out"]["cost_usd"], Decimal("0.00042"))
		self.assertEqual(p["chat_in"]["cost_usd"], Decimal(0))
		self.assertEqual(p["chat_in"]["cost_source"], "provider_reported")
		self.assertTrue(p["chat_in"]["cost_note"])
		self.assertEqual(p["chat_out"]["provider"], "openrouter")

	def test_mixed_messages_unknown(self):
		p = llm_pricing([_google("gemini-3.8-flash", 10000, 2000), _google("gemini-unlisted", 1, 1)])
		self.assertIsNone(p["chat_out"]["cost_usd"])
		self.assertIsNone(p["chat_in"]["cost_usd"])

	def test_match_requires_provider(self):
		AIModel.objects.create(provider="openrouter", name="gemini-only-or", input_price_per_mtok=Decimal("1"), output_price_per_mtok=Decimal("1"))
		p = llm_pricing([_google("gemini-only-or", 10, 10)])
		self.assertIsNone(p["chat_out"]["cost_usd"])

	def test_llm_costs_still_returns_pair(self):
		cost_in, cost_out = llm_costs([_google("gemini-3.8-flash", 10000, 2000)])
		self.assertEqual((cost_in, cost_out), (Decimal("0.003"), Decimal("0.005")))

	def test_track_token_usage_writes_snapshot(self):
		track_token_usage(str(self.project.id), "chat_out", 5, {"source": "t"}, pricing={
			"cost_usd": Decimal("0.001"), "cost_source": "token_calculated", "provider": "google",
			"model_name": "gemini-3.8-flash", "price_in_per_mtok": Decimal("0.3"), "price_out_per_mtok": Decimal("2.5"),
			"cost_note": "n",
		})
		e = UsageEvent.objects.get()
		self.assertEqual((e.provider, e.model_name, e.cost_source), ("google", "gemini-3.8-flash", "token_calculated"))
		self.assertEqual(e.price_out_per_mtok, Decimal("2.5"))
		self.assertEqual(e.metadata["cost_note"], "n")

	def test_price_change_keeps_old_event(self):
		p = llm_pricing([_google("gemini-3.8-flash", 10000, 2000)])
		track_token_usage(str(self.project.id), "chat_out", 2000, pricing=p["chat_out"])
		AIModel.objects.filter(name="gemini-3.8-flash").update(output_price_per_mtok=Decimal("9.99"))
		e = UsageEvent.objects.get()
		self.assertEqual((e.cost_usd, e.price_out_per_mtok), (Decimal("0.005"), Decimal("2.5")))


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

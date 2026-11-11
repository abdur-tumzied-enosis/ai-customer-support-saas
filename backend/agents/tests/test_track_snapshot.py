from decimal import Decimal

from django.test import TestCase
from langchain_core.messages import AIMessage

from accounts.models import Project
from agents.models import AIModel
from agents.rag import _track_token_usage
from usage.models import UsageEvent
from usage.utils import record_image_usage


class TrackSnapshotTests(TestCase):
	def setUp(self):
		self.project = Project.objects.create(name="P", slug="p-track-snap")
		AIModel.objects.create(provider="google", name="gemini-3.8-flash", input_price_per_mtok=Decimal("0.30"), output_price_per_mtok=Decimal("2.50"))

	def test_rag_writes_snapshot_on_both_events(self):
		msg = AIMessage(content="ok", response_metadata={"model_name": "gemini-3.8-flash"},
			usage_metadata={"input_tokens": 10000, "output_tokens": 2000, "total_tokens": 12000})
		_track_token_usage(str(self.project.id), "hi", "ok", 10000, 2000, [msg], None)
		out = UsageEvent.objects.get(kind="chat_out")
		self.assertEqual((out.provider, out.cost_source, out.cost_usd), ("google", "token_calculated", Decimal("0.005")))
		self.assertEqual(out.price_out_per_mtok, Decimal("2.500000"))
		self.assertEqual(UsageEvent.objects.get(kind="chat_in").cost_usd, Decimal("0.003"))

	def test_image_cost_is_provider_reported(self):
		record_image_usage(str(self.project.id), "index", tokens=0, cost_usd=Decimal(0))
		e = UsageEvent.objects.get(kind="image")
		self.assertEqual((e.cost_usd, e.cost_source), (Decimal(0), "provider_reported"))

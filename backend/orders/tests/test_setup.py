from unittest.mock import MagicMock, patch

from django.test import TestCase

from accounts.models import Project
from knowledge.models import Document, KnowledgeBase
from orders.models import OrderSettings
from orders.setup import ExtractedPayment, ExtractedZone, OrderSettingsExtract, extract_order_settings, run_setup


class FakeLLM:
	def __init__(self, parsed):
		self.parsed = parsed

	def with_structured_output(self, schema, **kwargs):
		return self

	def invoke(self, messages):
		raw = MagicMock()
		raw.usage_metadata = {"input_tokens": 100, "output_tokens": 20}
		return {"raw": raw, "parsed": self.parsed}


class SetupJobTest(TestCase):
	def setUp(self):
		self.project = Project.objects.create(name="P", slug="p-setup")
		self.kb = KnowledgeBase.objects.create(project=self.project, name="Products", vector_collection="vc-setup")
		Document.objects.create(kb=self.kb, source_type="url", page_url="https://shop.test/delivery", markdown_content="Dhaka delivery 60 taka. bKash, cash on delivery.")
		self.settings = OrderSettings.objects.create(project=self.project)

	def run_extract(self, parsed):
		with patch("orders.setup.get_llm", return_value=FakeLLM(parsed)), \
				patch("orders.setup.llm_pricing", return_value={"chat_in": {"cost_usd": None}, "chat_out": {"cost_usd": None}}), \
				patch("orders.setup.track_token_usage") as track:
			extract_order_settings(str(self.project.id), str(self.kb.id))
		return track

	def test_setup_fills_empty_zones_only(self):
		OrderSettings.objects.filter(pk=self.settings.pk).update(zones=[{"name": "Keep", "charge": "10"}])
		self.run_extract(OrderSettingsExtract(zones=[ExtractedZone(name="Dhaka", charge="60")]))
		self.assertEqual(OrderSettings.objects.get(pk=self.settings.pk).zones, [{"name": "Keep", "charge": "10"}])

	def test_setup_report_lists_not_found(self):
		self.run_extract(OrderSettingsExtract(zones=[], payment_methods=[ExtractedPayment(label="bKash")]))
		report = OrderSettings.objects.get(pk=self.settings.pk).setup_report
		self.assertIn("delivery charges", report["not_found"])
		self.assertNotIn("payment methods", report["not_found"])
		self.assertEqual(report["found"]["payment_methods"], 1)

	def test_setup_tracks_tokens_with_source(self):
		track = self.run_extract(OrderSettingsExtract())
		sources = [c.args[3]["source"] for c in track.call_args_list]
		self.assertEqual(sources, ["order_settings_extract", "order_settings_extract"])

	def test_run_sets_running(self):
		with patch("orders.setup._dispatch") as dispatch:
			run_setup(str(self.project.id), str(self.kb.id), mode="restart")
		self.assertEqual(OrderSettings.objects.get(pk=self.settings.pk).setup_status, "running")
		dispatch.assert_called_once_with(str(self.project.id), str(self.kb.id), "content")

from decimal import Decimal
from unittest import mock

import requests
from django.core.cache import cache
from django.test import TestCase, override_settings

from agents.models import AIModel, ModelConfig
from agents.pricing import sync_openrouter_prices

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "pricing-sync-tests"}}


def fake_response(data):
	resp = mock.Mock()
	resp.json.return_value = {"data": data}
	return resp


@override_settings(CACHES=LOCMEM, OPENROUTER_API_KEY="test-key")
class SyncOpenRouterPricesTest(TestCase):
	def setUp(self):
		cache.clear()
		AIModel.objects.all().delete()  # drop the migration's seeded catalog rows so not_found is exact
		self.cheap =AIModel.objects.create(provider="openrouter", name="vendor/cheap", input_price_per_mtok=Decimal("9"), output_price_per_mtok=Decimal("9"))
		self.dynamic = AIModel.objects.create(provider="openrouter", name="vendor/router", input_price_per_mtok=Decimal("1"), output_price_per_mtok=Decimal("1"))
		self.missing = AIModel.objects.create(provider="openrouter", name="vendor/gone", input_price_per_mtok=Decimal("5"), output_price_per_mtok=Decimal("6"))
		self.google = AIModel.objects.create(provider="google", name="gemini-y", input_price_per_mtok=Decimal("2"), output_price_per_mtok=Decimal("3"))
		ModelConfig.objects.create(reply_model=self.cheap)  # warms the cache key the sync must clear

	@mock.patch("agents.pricing.requests.get")
	def test_updates_prices_per_million_and_reports(self, get):
		get.return_value = fake_response([
			{"id": "vendor/cheap", "pricing": {"prompt": "0.0000005", "completion": "0.0000015"}},
			{"id": "vendor/router", "pricing": {"prompt": "-1", "completion": "-1"}},
			{"id": "vendor/unrelated", "pricing": {"prompt": "0.001", "completion": "0.002"}},
		])
		result = sync_openrouter_prices()
		self.cheap.refresh_from_db()
		self.dynamic.refresh_from_db()
		self.missing.refresh_from_db()
		self.google.refresh_from_db()
		assert self.cheap.input_price_per_mtok == Decimal("0.500000")
		assert self.cheap.output_price_per_mtok == Decimal("1.500000")
		assert self.dynamic.input_price_per_mtok is None and self.dynamic.output_price_per_mtok is None
		assert self.missing.input_price_per_mtok == Decimal("5") and self.missing.output_price_per_mtok == Decimal("6")
		assert self.google.input_price_per_mtok == Decimal("2")  # not an openrouter row: untouched
		assert result == {"updated": 2, "unchanged": 0, "not_found": ["vendor/gone"]}
		assert get.call_args.kwargs["headers"] == {"Authorization": "Bearer test-key"}
		assert get.call_args.kwargs["timeout"] == 30

	@mock.patch("agents.pricing.requests.get")
	def test_second_run_reports_unchanged_and_non_numeric_price_is_none(self, get):
		get.return_value = fake_response([
			{"id": "vendor/cheap", "pricing": {"prompt": "0.0000005", "completion": "0.0000015"}},
			{"id": "vendor/router", "pricing": {"prompt": None, "completion": ""}},
		])
		sync_openrouter_prices()
		result = sync_openrouter_prices()
		assert result["unchanged"] == 2 and result["updated"] == 0
		self.dynamic.refresh_from_db()
		assert self.dynamic.input_price_per_mtok is None

	@mock.patch("agents.pricing.requests.get")
	def test_http_error_writes_nothing(self, get):
		get.side_effect = requests.ConnectionError("down")
		with self.assertRaises(requests.ConnectionError):
			sync_openrouter_prices()
		self.cheap.refresh_from_db()
		assert self.cheap.input_price_per_mtok == Decimal("9")
		assert ModelConfig.objects.get().reply_model_id == self.cheap.pk


from django.conf import settings

from agents.tasks import sync_openrouter_prices as sync_task


class SyncTaskTest(TestCase):
	def test_beat_runs_the_task_daily(self):
		entry = settings.CELERY_BEAT_SCHEDULE["sync-openrouter-prices"]
		assert entry["task"] == "agents.tasks.sync_openrouter_prices"
		assert entry["schedule"] == 86400

	@mock.patch("agents.pricing.requests.get")
	@override_settings(OPENROUTER_API_KEY="test-key")
	def test_task_returns_the_sync_summary(self, get):
		AIModel.objects.all().delete()  # drop seeded catalog rows so not_found is empty
		get.return_value = fake_response([])
		assert sync_task() == {"updated": 0, "unchanged": 0, "not_found": []}

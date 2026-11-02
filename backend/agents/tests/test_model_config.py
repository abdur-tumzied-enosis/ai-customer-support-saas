from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase, override_settings
from langchain_core.messages import AIMessage

from agents.llm import active_model, create_basic_agent, resolve_model
from agents.models import AIModel, ModelConfig
from usage.utils import llm_costs

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "model-config-tests"}}


@override_settings(CACHES=LOCMEM, LLM_PROVIDER="google", LLM_MODEL="env-model", MEMORY_LLM_MODEL=None)
class ModelConfigTest(TestCase):
	def setUp(self):
		cache.clear()
		self.deepseek = AIModel.objects.create(provider="openrouter", name="deepseek/x")
		self.gemini = AIModel.objects.create(provider="google", name="gemini-y")

	def test_env_until_admin_picks_then_db_wins(self):
		assert resolve_model() == ("google", "env-model")
		assert resolve_model(role="memory") == ("google", "env-model")
		ModelConfig.objects.create(reply_model=self.deepseek)
		assert resolve_model() == ("openrouter", "deepseek/x")  # save cleared the cache
		assert resolve_model(role="memory") == ("openrouter", "deepseek/x")  # memory unset → reply pick
		assert resolve_model("google", "explicit") == ("google", "explicit")

	def test_memory_order(self):
		ModelConfig.objects.create(reply_model=self.deepseek)
		with override_settings(MEMORY_LLM_MODEL="env-memory"):
			assert resolve_model(role="memory") == ("google", "env-memory")
			ModelConfig.objects.create(reply_model=self.deepseek, memory_model=self.gemini)
			assert resolve_model(role="memory") == ("google", "gemini-y")

	def test_singleton_and_fallback_role(self):
		ModelConfig.objects.create(fallback_model=self.gemini)
		ModelConfig.objects.create(fallback_model=self.deepseek)
		assert ModelConfig.objects.count() == 1
		assert active_model("fallback") == ("openrouter", "deepseek/x")
		create_basic_agent([])  # builds the DB fallback without errors


class LlmCostsTest(TestCase):
	def test_openrouter_charged_cost_and_google_priced(self):
		AIModel.objects.create(provider="google", name="gemini-y", input_price_per_mtok=Decimal("0.5"), output_price_per_mtok=Decimal("3"))
		openrouter = AIMessage("a", response_metadata={"cost": 0.00042, "model_name": "deepseek/x"})
		google = AIMessage("b", response_metadata={"model_name": "gemini-y"}, usage_metadata={"input_tokens": 2000, "output_tokens": 100, "total_tokens": 2100})
		assert llm_costs([openrouter]) == (Decimal(0), Decimal("0.00042"))
		assert llm_costs([google]) == (Decimal("0.001"), Decimal("0.0003"))
		assert llm_costs([openrouter, google]) == (Decimal("0.001"), Decimal("0.00072"))

	def test_unknown_price_is_none(self):
		msg = AIMessage("c", response_metadata={"model_name": "unpriced"}, usage_metadata={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2})
		assert llm_costs([msg]) == (None, None)

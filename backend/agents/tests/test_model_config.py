from decimal import Decimal
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from langchain_core.messages import AIMessage

from agents.llm import active_model, create_basic_agent, resolve_model
from agents.models import AIModel, ModelConfig
from usage.utils import llm_costs

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "model-config-tests"}}


def fake_response(data):
	resp = mock.Mock()
	resp.json.return_value = {"data": data}
	return resp


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


class EmbeddingPricingTest(TestCase):
	def test_embedding_model_stores_input_and_image_prices(self):
		m = AIModel.objects.create(
			kind="embedding", provider="openrouter", name="cohere/embed-5-fast-test",
			input_price_per_mtok=Decimal("0.08"), image_price_per_mtok=Decimal("0.40"),
		)
		m.refresh_from_db()
		self.assertEqual((m.kind, m.input_price_per_mtok, m.image_price_per_mtok), ("embedding", Decimal("0.08"), Decimal("0.40")))


@override_settings(CACHES=LOCMEM, LLM_PROVIDER="google", LLM_MODEL="gemini-env")
class VisionCapabilityTest(TestCase):
	def setUp(self):
		cache.clear()
		AIModel.objects.all().delete()  # drop the migration's seeded catalog rows so the routes are exact

	def test_aimodel_supports_vision_defaults_false(self):
		assert AIModel.objects.create(provider="openrouter", name="x").supports_vision is False

	def test_vision_route_env_google_none(self):
		# Photo turns are OpenRouter-only: a Google reply model never routes photos, even when it is vision-capable
		from agents.llm import vision_route
		assert vision_route() is None
		AIModel.objects.create(provider="google", name="gemini-env", kind="text", supports_vision=True)
		assert vision_route() is None

	@override_settings(LLM_PROVIDER="openrouter", LLM_MODEL="env-or")
	def test_vision_route_env_openrouter_none(self):
		from agents.llm import vision_route
		assert vision_route() is None

	def test_vision_route_reply_model_config_wins(self):
		from agents.llm import vision_route
		m = AIModel.objects.create(provider="openrouter", name="deepseek/x", input_modalities=["text"])
		ModelConfig.objects.create(reply_model=m)
		assert vision_route() is None  # reply pick wins over the google env model, and it is text-only
		m.input_modalities = ["text", "image"]
		m.save()
		assert vision_route() == ("openrouter", "deepseek/x")  # save cleared the state; no stale answer

	@mock.patch("agents.pricing.requests.get")
	def test_sync_stores_modalities_and_derives_flag(self, get):
		from agents.pricing import sync_openrouter_prices
		AIModel.objects.create(provider="openrouter", name="vendor/eyes")
		AIModel.objects.create(provider="openrouter", name="vendor/text")
		get.return_value = fake_response([
			{"id": "vendor/eyes", "pricing": {"prompt": "0.000001", "completion": "0.000002"},
			 "architecture": {"input_modalities": ["text", "image"]}},
			{"id": "vendor/text", "pricing": {"prompt": "0.000001", "completion": "0.000002"},
			 "architecture": {"input_modalities": ["text"]}},
		])
		assert sync_openrouter_prices(api_key="k")["updated"] == 2
		eyes, text = AIModel.objects.get(name="vendor/eyes"), AIModel.objects.get(name="vendor/text")
		assert (eyes.input_modalities, eyes.supports_vision) == (["text", "image"], True)
		assert (text.input_modalities, text.supports_vision) == (["text"], False)
		assert sync_openrouter_prices(api_key="k")["unchanged"] == 2  # second run: modalities compared too

	@mock.patch("agents.pricing.requests.get")
	def test_sync_missing_architecture_is_not_capable(self, get):
		from agents.pricing import sync_openrouter_prices
		m = AIModel.objects.create(provider="openrouter", name="vendor/bare", input_modalities=["text", "image"])
		get.return_value = fake_response([{"id": "vendor/bare", "pricing": {"prompt": "0.000001", "completion": "0.000002"}}])
		sync_openrouter_prices(api_key="k")
		m.refresh_from_db()
		assert (m.input_modalities, m.supports_vision) == ([], False)

	def test_vision_route_uses_vision_model_when_reply_is_text_only(self):
		from agents.llm import vision_route
		reply = AIModel.objects.create(provider="openrouter", name="deepseek/x", input_modalities=["text"])
		eyes = AIModel.objects.create(provider="openrouter", name="vendor/eyes", input_modalities=["text", "image"])
		ModelConfig.objects.create(reply_model=reply, vision_model=eyes)
		assert vision_route() == ("openrouter", "vendor/eyes")

	def test_vision_route_none_when_no_capable_model(self):
		from agents.llm import vision_route
		reply = AIModel.objects.create(provider="openrouter", name="deepseek/x", input_modalities=["text"])
		ModelConfig.objects.create(reply_model=reply)
		assert vision_route() is None

	def test_vision_route_never_resolves_to_text_reply(self):
		from agents.llm import resolve_model
		reply = AIModel.objects.create(provider="openrouter", name="deepseek/x", input_modalities=["text"])
		ModelConfig.objects.create(reply_model=reply)
		assert resolve_model(role="vision") != ("openrouter", "deepseek/x")

	def test_vision_model_clean_rejects_text_only_model(self):
		from django.core.exceptions import ValidationError
		text_only = AIModel.objects.create(provider="openrouter", name="vendor/text", input_modalities=["text"])
		with self.assertRaises(ValidationError):
			ModelConfig(vision_model=text_only).clean()

	def test_google_text_model_saves_as_vision_capable(self):
		m = AIModel.objects.create(provider="google", kind="text", name="gemini-z")
		assert (m.input_modalities, m.supports_vision) == (["text", "image"], True)

	def test_backfill_marks_google_text_models(self):
		import importlib
		from django.apps import apps as django_apps
		google_text = AIModel.objects.create(provider="google", kind="text", name="gemini-t")
		openrouter_text = AIModel.objects.create(provider="openrouter", kind="text", name="vendor/t", input_modalities=["text"])
		google_embed = AIModel.objects.create(provider="google", kind="embedding", name="embed-g")
		backfill = importlib.import_module("agents.migrations.0006_aimodel_supports_vision").backfill
		AIModel.objects.update(supports_vision=False, input_modalities=[])  # start from the pre-migration state
		backfill(django_apps, None)
		google_text.refresh_from_db(); openrouter_text.refresh_from_db(); google_embed.refresh_from_db()
		assert google_text.supports_vision is True
		assert openrouter_text.supports_vision is False
		assert google_embed.supports_vision is False

	def test_vision_route_builds_agent_on_vision_model(self):
		reply = AIModel.objects.create(provider="openrouter", name="deepseek/x", input_modalities=["text"])
		eyes = AIModel.objects.create(provider="openrouter", name="vendor/eyes", input_modalities=["text", "image"])
		ModelConfig.objects.create(reply_model=reply, vision_model=eyes)
		with mock.patch("agents.llm._reply_llm") as reply_llm, mock.patch("agents.llm.build_system_prompt", return_value="p"), mock.patch("agents.llm.create_agent") as make:
			create_basic_agent([], route="vision")
		assert reply_llm.call_args.args[:2] == ("openrouter", "vendor/eyes")
		assert make.called

	def test_vision_route_without_capable_model_raises(self):
		reply = AIModel.objects.create(provider="openrouter", name="deepseek/x", input_modalities=["text"])
		ModelConfig.objects.create(reply_model=reply)
		with self.assertRaises(ValueError):
			create_basic_agent([], route="vision")

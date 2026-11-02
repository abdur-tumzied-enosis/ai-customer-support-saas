from django.db import migrations

# Catalog only: nothing is assigned in ModelConfig, so the env settings stay in charge until an admin picks.
TEXT = [
	"deepseek/deepseek-v4.1-flash-1017",
	"google/gemma-4-31b-it",
	"openai/gpt-oss-120b",
	"deepseek/deepseek-v4.1-flash",
	"inclusionai/ling-3.0-flash",
	"openai/gpt-oss-20b:batch",
	"z-ai/glm-5.3-flash",
]
TRANSCRIPTION = [
	"openai/whisper-large-v3-turbo",
	"nvidia/nemotron-3.5-asr-streaming-multilingual-0.6b",
	"qwen/qwen3-asr-0.6b",
]


def seed(apps, schema_editor):
	AIModel = apps.get_model("agents", "AIModel")
	for kind, names in (("text", TEXT), ("transcription", TRANSCRIPTION)):
		for name in names:
			AIModel.objects.get_or_create(provider="openrouter", name=name, defaults={"kind": kind})


class Migration(migrations.Migration):
	dependencies = [("agents", "0001_initial")]
	operations = [migrations.RunPython(seed, migrations.RunPython.noop)]

import django.db.models.deletion
from django.db import migrations, models

SUPPORTED = ("elevenlabs/scribe-v2", "x-ai/grok-stt-2.0", "google/gemini-3.5-transcribe")


def seed(apps, schema_editor):
	AIModel = apps.get_model("agents", "AIModel")
	for name in SUPPORTED:
		AIModel.objects.get_or_create(provider="openrouter", name=name, defaults={"kind": "transcription"})


class Migration(migrations.Migration):

	dependencies = [("agents", "0006_aimodel_supports_vision")]

	operations = [
		migrations.AlterField(
			model_name="modelconfig",
			name="transcription_model",
			field=models.ForeignKey(
				blank=True, help_text="One of: " + ", ".join(SUPPORTED), limit_choices_to={"kind": "transcription"}, null=True,
				on_delete=django.db.models.deletion.PROTECT, related_name="+", to="agents.aimodel",
			),
		),
		migrations.RunPython(seed, migrations.RunPython.noop),
	]

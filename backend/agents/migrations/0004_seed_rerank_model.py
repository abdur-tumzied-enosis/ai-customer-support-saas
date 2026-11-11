from django.db import migrations


def seed(apps, schema_editor):
	# Price left blank: OpenRouter's page and third-party sources disagree on it. Set it in admin once confirmed.
	apps.get_model("agents", "AIModel").objects.get_or_create(
		provider="openrouter", name="cohere/rerank-5-fast", defaults={"kind": "rerank"},
	)


class Migration(migrations.Migration):
	dependencies = [("agents", "0003_embedding_pricing")]
	operations = [migrations.RunPython(seed, migrations.RunPython.noop)]

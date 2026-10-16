import django.db.models.functions.text
from django.db import migrations, models


def mark_defaults(apps, schema_editor):
	# Until now the default KB was found by its name
	apps.get_model("knowledge", "KnowledgeBase").objects.filter(name="default").update(is_default=True)


class Migration(migrations.Migration):
	dependencies = [
		("knowledge", "0011_memory_suggestion"),
	]

	operations = [
		migrations.AddField(
			model_name="knowledgebase",
			name="is_default",
			field=models.BooleanField(default=False),
		),
		migrations.RunPython(mark_defaults, migrations.RunPython.noop),
		migrations.AddConstraint(
			model_name="knowledgebase",
			constraint=models.UniqueConstraint(
				django.db.models.functions.text.Lower("name"), "project", name="kb_unique_name_per_project"
			),
		),
		migrations.AddConstraint(
			model_name="knowledgebase",
			constraint=models.UniqueConstraint(
				condition=models.Q(("is_default", True)), fields=("project",), name="kb_one_default_per_project"
			),
		),
	]

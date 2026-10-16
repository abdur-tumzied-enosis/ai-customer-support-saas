import knowledge.models
from django.db import migrations, models


class Migration(migrations.Migration):
	dependencies = [
		("knowledge", "0012_knowledgebase_default_and_unique_name"),
	]

	operations = [
		migrations.AlterField(
			model_name="document",
			name="file",
			field=models.FileField(blank=True, max_length=255, null=True, upload_to=knowledge.models.document_upload_path),
		),
	]

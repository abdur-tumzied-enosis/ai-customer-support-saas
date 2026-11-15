from django.db import migrations, models


class Migration(migrations.Migration):

	dependencies = [("agents", "0007_seed_transcription_models")]

	operations = [
		migrations.AddField(
			model_name="aimodel",
			name="input_price_per_second",
			field=models.DecimalField(blank=True, decimal_places=12, help_text="USD per second of audio (transcription models)", max_digits=18, null=True),
		),
	]

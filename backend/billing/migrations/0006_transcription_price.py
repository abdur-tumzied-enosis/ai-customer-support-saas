from decimal import Decimal

from django.db import migrations, models


def seed(apps, schema_editor):
	ActionPrice = apps.get_model("billing", "ActionPrice")
	ActionPrice.objects.get_or_create(action="transcription", defaults={"credits": Decimal("1")})


def unseed(apps, schema_editor):
	apps.get_model("billing", "ActionPrice").objects.filter(action="transcription").delete()


class Migration(migrations.Migration):
	dependencies = [("billing", "0005_package_features")]
	operations = [
		migrations.AddField(
			model_name="pricingsettings",
			name="transcription_seconds_per_credit",
			field=models.DecimalField(decimal_places=2, default=Decimal("5"), max_digits=6),
		),
		migrations.AlterField(
			model_name="actionprice",
			name="action",
			field=models.CharField(choices=[("reply", "AI reply"), ("photo_search", "Photo search"), ("product_import", "Catalog product"), ("photo_index", "Product photo indexed"), ("order_setup", "Order setup from your documents"), ("instruction_improve", "Improve instructions"), ("product_suggest", "Column mapping suggestion"), ("transcription", "Voice note transcription")], max_length=40, unique=True),
		),
		migrations.RunPython(seed, unseed),
	]

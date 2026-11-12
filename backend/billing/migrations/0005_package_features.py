from django.db import migrations

# Packs differ only by price and bonus credits; no feature is gated by package, so the lists say so.
FEATURES = {
	"Starter": [
		"All channels (Messenger, WhatsApp, Instagram, web widget)",
		"AI replies",
		"Agent memory",
		"Order taking from chat",
		"Catalog import",
		"Photo search for products (on request)",
		"Credits never expire",
	],
	"Growth": [
		"Everything in Starter",
		"20% bonus credits",
	],
	"Pro": [
		"Everything in Starter",
		"30% bonus credits",
		"Priority support",
		"Help setting up your catalog",
	],
}


def update(apps, schema_editor):
	CreditPackage = apps.get_model("billing", "CreditPackage")
	for name, features in FEATURES.items():
		CreditPackage.objects.filter(name=name).update(features=features)


class Migration(migrations.Migration):
	dependencies = [("billing", "0004_credit_request")]
	operations = [migrations.RunPython(update, migrations.RunPython.noop)]

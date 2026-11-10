from decimal import Decimal

from django.db import migrations

# Kept in step with billing.models.ACTIONS (migrations must not import live models).
PRICES = {"reply": 1, "photo_search": 1, "product_import": 2, "photo_index": 0, "order_setup": 5, "instruction_improve": 0, "product_suggest": 0}
PACKAGES = [
	("Starter", 1000, 0, "500", False, [
		"All channels (Messenger, WhatsApp, Instagram, web widget)",
		"AI replies in Bangla & English",
		"Knowledge base",
		"Credits never expire",
	]),
	("Growth", 5000, 1000, "2000", True, [
		"Everything in Starter",
		"Photo search for products",
		"Order taking from chat",
		"Catalog import",
		"20% bonus credits",
	]),
	("Pro", 20000, 6000, "7000", False, [
		"Everything in Growth",
		"30% bonus credits",
		"Priority support",
		"Help setting up your catalog",
	]),
]


def seed(apps, schema_editor):
	ActionPrice = apps.get_model("billing", "ActionPrice")
	PricingSettings = apps.get_model("billing", "PricingSettings")
	CreditPackage = apps.get_model("billing", "CreditPackage")
	for action, credits in PRICES.items():
		ActionPrice.objects.get_or_create(action=action, defaults={"credits": Decimal(credits)})
	PricingSettings.objects.get_or_create(pk=1)
	for i, (name, credits, bonus, price, highlighted, features) in enumerate(PACKAGES):
		CreditPackage.objects.get_or_create(name=name, defaults={
			"credits": credits, "bonus_credits": bonus, "price": Decimal(price), "features": features,
			"highlighted": highlighted, "sort_order": i,
		})


class Migration(migrations.Migration):
	dependencies = [("billing", "0001_initial")]
	operations = [migrations.RunPython(seed, migrations.RunPython.noop)]

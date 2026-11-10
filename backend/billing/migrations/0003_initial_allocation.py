from decimal import Decimal

from django.db import migrations

AMOUNT = Decimal("100.00")


def allocate_initial(apps, schema_editor):
	"""Give every existing project 100 credits once. Historical models on purpose: migrations must not import billing.service."""
	Project = apps.get_model("accounts", "Project")
	CreditWallet = apps.get_model("billing", "CreditWallet")
	CreditTransaction = apps.get_model("billing", "CreditTransaction")
	for project in Project.objects.all():
		wallet, _ = CreditWallet.objects.get_or_create(project=project, defaults={"project_name": project.name})
		key = f"initial:{project.pk}"
		if CreditTransaction.objects.filter(idempotency_key=key).exists():
			continue
		wallet.balance += AMOUNT
		CreditTransaction.objects.create(
			wallet=wallet, amount=AMOUNT, balance_after=wallet.balance, kind="initial_allocation", idempotency_key=key,
		)
		wallet.save(update_fields=["balance"])


class Migration(migrations.Migration):

	dependencies = [
		("billing", "0002_seed"),
		("accounts", "0006_project_created_by"),
	]

	operations = [
		migrations.RunPython(allocate_initial, migrations.RunPython.noop),
	]

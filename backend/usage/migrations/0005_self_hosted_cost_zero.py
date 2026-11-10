from django.db import migrations


def backfill(apps, schema_editor):
    # Self-hosted embeddings and photo indexing/search cost nothing
    apps.get_model("usage", "UsageEvent").objects.filter(kind__in=["image", "embed"], cost_usd__isnull=True).update(cost_usd=0)


class Migration(migrations.Migration):

    dependencies = [
        ("usage", "0004_usageevent_conversation_message"),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]

from django.db import migrations

OLD, NEW = "/webhooks/facebook/", "/webhooks/meta/"


def forward(apps, schema_editor):
    apps.get_model("channels_app", "Platform").objects.filter(webhook_path=OLD).update(webhook_path=NEW)


def backward(apps, schema_editor):
    apps.get_model("channels_app", "Platform").objects.filter(webhook_path=NEW).update(webhook_path=OLD)


class Migration(migrations.Migration):

    dependencies = [
        ('channels_app', '0017_image_search_enabled'),
    ]

    operations = [
        migrations.RunPython(forward, backward),
    ]

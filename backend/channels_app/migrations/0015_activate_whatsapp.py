from django.db import migrations

META_WEBHOOK_PATH = "/webhooks/facebook/"  # one Meta callback URL, routed on payload "object"


def activate(apps, schema_editor):
    Platform = apps.get_model("channels_app", "Platform")
    Platform.objects.filter(name="whatsapp").update(
        is_active=True, webhook_path=META_WEBHOOK_PATH, api_base_url="https://graph.facebook.com/v21.0",
    )
    Platform.objects.filter(name="instagram").update(webhook_path=META_WEBHOOK_PATH)  # /webhooks/instagram/ was removed


def deactivate(apps, schema_editor):
    apps.get_model("channels_app", "Platform").objects.filter(name="whatsapp").update(is_active=False)


class Migration(migrations.Migration):

    dependencies = [
        ('channels_app', '0014_instagram_channel'),
    ]

    operations = [
        migrations.RunPython(activate, deactivate),
    ]

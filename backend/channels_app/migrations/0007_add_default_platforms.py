# Generated data migration to add default platforms

from django.db import migrations


def add_default_platforms(apps, schema_editor):
    """
    Add default platform entries for Facebook and WhatsApp.
    This populates the Platform table with the supported messaging platforms.
    """
    Platform = apps.get_model('channels_app', 'Platform')
    
    # Define default platforms with their credential schemas
    defaults = [
        {
            "name": "facebook",
            "display_name": "Facebook Messenger",
            "description": "Connect your Facebook Page to receive and respond to Messenger conversations with AI-powered responses.",
            "icon": "facebook",
            "is_active": True,
            "webhook_path": "/webhooks/facebook/",
            "api_base_url": "https://graph.facebook.com/v18.0",
            "documentation_url": "https://developers.facebook.com/docs/messenger-platform",
            "credential_schema": [
                {
                    "name": "page_id",
                    "label": "Page ID",
                    "type": "text",
                    "required": True,
                    "description": "Your Facebook Page ID (found in Page Settings > Page Info)",
                    "placeholder": "123456789012345"
                },
                {
                    "name": "page_access_token",
                    "label": "Page Access Token",
                    "type": "password",
                    "required": True,
                    "description": "Long-lived Page Access Token from Meta Developer Console",
                    "placeholder": "EAAxxxxxxx..."
                },
                {
                    "name": "meta_app_id",
                    "label": "App ID",
                    "type": "text",
                    "required": True,
                    "description": "Facebook App ID from Meta Developer Dashboard",
                    "placeholder": "1234567890123456"
                },
                {
                    "name": "meta_app_secret",
                    "label": "App Secret",
                    "type": "password",
                    "required": True,
                    "description": "App Secret for webhook signature verification",
                    "placeholder": "abcdef123456..."
                },
                {
                    "name": "verify_token",
                    "label": "Verify Token",
                    "type": "text",
                    "required": True,
                    "description": "Custom token for webhook verification (create your own)",
                    "placeholder": "my_custom_verify_token_123"
                },
            ],
        },
        {
            "name": "whatsapp",
            "display_name": "WhatsApp Business",
            "description": "Connect your WhatsApp Business account to handle customer conversations via the WhatsApp Cloud API.",
            "icon": "whatsapp",
            "is_active": False,  # Coming soon
            "webhook_path": "/webhooks/whatsapp/",
            "api_base_url": "https://graph.facebook.com/v18.0",
            "documentation_url": "https://developers.facebook.com/docs/whatsapp/cloud-api",
            "credential_schema": [
                {
                    "name": "phone_number_id",
                    "label": "Phone Number ID",
                    "type": "text",
                    "required": True,
                    "description": "WhatsApp Business Phone Number ID from Meta Business Suite",
                    "placeholder": "123456789012345"
                },
                {
                    "name": "business_account_id",
                    "label": "Business Account ID",
                    "type": "text",
                    "required": True,
                    "description": "WhatsApp Business Account ID (WABA ID)",
                    "placeholder": "123456789012345"
                },
                {
                    "name": "access_token",
                    "label": "Access Token",
                    "type": "password",
                    "required": True,
                    "description": "Permanent access token for WhatsApp Cloud API",
                    "placeholder": "EAAxxxxxxx..."
                },
                {
                    "name": "app_id",
                    "label": "App ID",
                    "type": "text",
                    "required": True,
                    "description": "Meta App ID from Developer Dashboard",
                    "placeholder": "1234567890123456"
                },
                {
                    "name": "app_secret",
                    "label": "App Secret",
                    "type": "password",
                    "required": True,
                    "description": "App Secret for webhook signature verification",
                    "placeholder": "abcdef123456..."
                },
                {
                    "name": "verify_token",
                    "label": "Verify Token",
                    "type": "text",
                    "required": True,
                    "description": "Custom token for webhook verification (create your own)",
                    "placeholder": "my_whatsapp_verify_token_123"
                },
            ],
        },
    ]
    
    for platform_data in defaults:
        Platform.objects.get_or_create(
            name=platform_data["name"],
            defaults=platform_data
        )


def remove_default_platforms(apps, schema_editor):
    """
    Remove default platform entries (for rollback).
    """
    Platform = apps.get_model('channels_app', 'Platform')
    Platform.objects.filter(name__in=["facebook", "whatsapp"]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('channels_app', '0006_platform_alter_channelcredential_options_and_more'),
    ]

    operations = [
        migrations.RunPython(add_default_platforms, remove_default_platforms),
    ]


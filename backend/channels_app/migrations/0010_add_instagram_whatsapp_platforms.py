# Generated data migration to add Instagram and activate WhatsApp platforms

from django.db import migrations


def add_new_platforms(apps, schema_editor):
	"""
	Add Instagram platform and activate WhatsApp platform.
	"""
	Platform = apps.get_model('channels_app', 'Platform')
	
	# Add Instagram platform
	instagram_data = {
		"name": "instagram",
		"display_name": "Instagram Messaging",
		"description": "Connect your Instagram Business account to handle customer conversations and direct messages with AI-powered responses.",
		"icon": "instagram",
		"is_active": True,
		"webhook_path": "/webhooks/instagram/",
		"api_base_url": "https://graph.facebook.com/v18.0",
		"documentation_url": "https://developers.facebook.com/docs/messenger-platform/instagram",
		"credential_schema": [
			{
				"name": "instagram_account_id",
				"label": "Instagram Account ID",
				"type": "text",
				"required": True,
				"description": "Your Instagram Business Account ID (found in Instagram settings or Graph API)",
				"placeholder": "17841400000000000"
			},
			{
				"name": "page_id",
				"label": "Facebook Page ID",
				"type": "text",
				"required": True,
				"description": "Connected Facebook Page ID",
				"placeholder": "123456789012345"
			},
			{
				"name": "page_access_token",
				"label": "Page Access Token",
				"type": "password",
				"required": True,
				"description": "Long-lived Page Access Token with Instagram permissions",
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
				"placeholder": "my_instagram_verify_token_123"
			},
		],
	}
	
	Platform.objects.get_or_create(
		name="instagram",
		defaults=instagram_data
	)
	


def remove_new_platforms(apps, schema_editor):
	"""
	Remove Instagram platform and deactivate WhatsApp (for rollback).
	"""


class Migration(migrations.Migration):

	dependencies = [
		('channels_app', '0009_webhookevent'),
	]

	operations = [
		migrations.RunPython(add_new_platforms, remove_new_platforms),
	]



from django.test import SimpleTestCase, override_settings

from channels_app.credential_crypto import ENC_PREFIX, decrypt_credentials
from channels_app.models import FacebookCredentials, InstagramCredentials

TEST_KEY = "DdZ_qWzIlhW-IcbMX1pHYAAqQNYv9h9rcyfDRCeRwPY="


@override_settings(TOKEN_ENCRYPTION_KEY=TEST_KEY)
class CredentialDataclassTest(SimpleTestCase):
	def test_oauth_facebook_needs_no_app_secret_or_verify_token(self):
		creds = FacebookCredentials(page_id="1", page_access_token="t", meta_app_id="1478910681051836",
			connected_via="oauth", meta_user_id="u1")
		assert creds.is_configured()

	def test_oauth_instagram_needs_instagram_account_id(self):
		creds = InstagramCredentials(instagram_account_id="ig1", page_id="1", page_access_token="t",
			meta_app_id="1478910681051836", connected_via="oauth", meta_user_id="u1")
		assert creds.is_configured()
		assert not InstagramCredentials(instagram_account_id="", page_id="1", page_access_token="t",
			connected_via="oauth").is_configured()

	def test_manual_rows_still_need_all_five_fields(self):
		assert not FacebookCredentials(page_id="1", page_access_token="t", meta_app_id="a",
			meta_app_secret="s").is_configured()
		assert FacebookCredentials(page_id="1", page_access_token="t", meta_app_id="a",
			meta_app_secret="s", verify_token="v").is_configured()

	def test_new_fields_survive_from_dict(self):
		creds = FacebookCredentials.from_dict({"page_id": "1", "connected_via": "oauth", "meta_user_id": "u1"})
		assert creds.connected_via == "oauth" and creds.meta_user_id == "u1"

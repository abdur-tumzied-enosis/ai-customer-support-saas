from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings

from channels_app.credential_crypto import (
	ENC_PREFIX, decrypt_credentials, decrypt_secret, encrypt_credentials, encrypt_secret,
)

TEST_KEY = "DdZ_qWzIlhW-IcbMX1pHYAAqQNYv9h9rcyfDRCeRwPY="


@override_settings(TOKEN_ENCRYPTION_KEY=TEST_KEY)
class CredentialCryptoTest(SimpleTestCase):
	def test_roundtrip(self):
		token = encrypt_secret("EAAsecret")
		assert token.startswith(ENC_PREFIX)
		assert decrypt_secret(token) == "EAAsecret"

	def test_ciphertext_is_not_the_plaintext(self):
		assert "EAAsecret" not in encrypt_secret("EAAsecret")

	def test_legacy_plaintext_passes_through_unchanged(self):
		assert decrypt_secret("EAAplaintext") == "EAAplaintext"

	def test_encrypt_credentials_only_touches_the_token(self):
		out = encrypt_credentials({"page_id": "1", "page_access_token": "t", "verify_token": "v"})
		assert out["page_id"] == "1" and out["verify_token"] == "v"
		assert out["page_access_token"].startswith(ENC_PREFIX)
		assert encrypt_credentials(out)["page_access_token"] == out["page_access_token"]  # no double encryption

	def test_decrypt_credentials_handles_legacy_and_encrypted(self):
		encrypted = encrypt_credentials({"page_access_token": "new", "verify_token": "v"})
		assert decrypt_credentials(encrypted)["page_access_token"] == "new"
		assert decrypt_credentials({"page_access_token": "old"})["page_access_token"] == "old"

	@override_settings(TOKEN_ENCRYPTION_KEY="")
	def test_missing_key_fails_loudly(self):
		with self.assertRaises(ImproperlyConfigured):
			encrypt_secret("t")

	@override_settings(TOKEN_ENCRYPTION_KEY="not-a-fernet-key")
	def test_malformed_key_fails_loudly(self):
		with self.assertRaises(ImproperlyConfigured):
			encrypt_secret("t")

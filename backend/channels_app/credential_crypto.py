from cryptography.fernet import Fernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

ENC_PREFIX = "enc:v1:"


def _fernet() -> Fernet:
	# Read per call so a missing or malformed key fails on every write and read, never falls back to plaintext
	key = settings.TOKEN_ENCRYPTION_KEY
	if not key:
		raise ImproperlyConfigured("TOKEN_ENCRYPTION_KEY is required to store Meta tokens")
	try:
		return Fernet(key.encode())
	except ValueError:
		raise ImproperlyConfigured(
			"TOKEN_ENCRYPTION_KEY is malformed; generate one with Fernet.generate_key()"
		)


def encrypt_secret(plaintext: str) -> str:
	return ENC_PREFIX + _fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(value: str) -> str:
	# Legacy plaintext rows have no prefix and pass through unchanged
	if not value.startswith(ENC_PREFIX):
		return value
	return _fernet().decrypt(value[len(ENC_PREFIX):].encode()).decode()


def encrypt_credentials(data: dict) -> dict:
	token = data.get("page_access_token")
	if not token or token.startswith(ENC_PREFIX):
		return data
	return {**data, "page_access_token": encrypt_secret(token)}


def decrypt_credentials(data: dict) -> dict:
	token = data.get("page_access_token")
	if not token or not token.startswith(ENC_PREFIX):
		return data
	return {**data, "page_access_token": decrypt_secret(token)}

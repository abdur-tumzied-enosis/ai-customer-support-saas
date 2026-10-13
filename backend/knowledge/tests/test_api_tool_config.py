from django.test import SimpleTestCase

from knowledge.api_tool_config import ApiConfigError, apply_api_config, public_api_domains


class ApplyApiConfigTest(SimpleTestCase):
	def test_normalizes_domain_from_url(self):
		enabled, domains = apply_api_config({
			"api_tool_enabled": True,
			"api_domains": [{"domain": "https://API.Shop.com:8443/orders?x=1", "auth_header": "X-Key", "secret": "k"}],
		})
		assert enabled is True
		assert domains == [{"domain": "api.shop.com", "auth_header": "X-Key", "secret": "k"}]

	def test_enabled_without_domains_rejected(self):
		with self.assertRaises(ApiConfigError):
			apply_api_config({"api_tool_enabled": True, "api_domains": []})

	def test_invalid_domains_rejected(self):
		for bad in ("127.0.0.1", "localhost", "", "[::1]"):
			with self.assertRaises(ApiConfigError, msg=bad):
				apply_api_config({"api_domains": [{"domain": bad}]})

	def test_bad_header_and_secret_without_header_rejected(self):
		with self.assertRaises(ApiConfigError):
			apply_api_config({"api_domains": [{"domain": "shop.com", "auth_header": "Bad Header", "secret": "k"}]})
		with self.assertRaises(ApiConfigError):
			apply_api_config({"api_domains": [{"domain": "shop.com", "secret": "k"}]})

	def test_too_many_and_duplicate_domains_rejected(self):
		with self.assertRaises(ApiConfigError):
			apply_api_config({"api_domains": [{"domain": f"d{i}.com"} for i in range(11)]})
		with self.assertRaises(ApiConfigError):
			apply_api_config({"api_domains": [{"domain": "shop.com"}, {"domain": "SHOP.com"}]})

	def test_blank_secret_keeps_existing(self):
		current = [{"domain": "shop.com", "auth_header": "X-Key", "secret": "old"}]
		_, domains = apply_api_config(
			{"api_domains": [{"domain": "shop.com", "auth_header": "X-Key", "secret": ""}]},
			current_enabled=True, current_domains=current,
		)
		assert domains[0]["secret"] == "old"

	def test_removing_header_clears_secret(self):
		current = [{"domain": "shop.com", "auth_header": "X-Key", "secret": "old"}]
		_, domains = apply_api_config({"api_domains": [{"domain": "shop.com"}]}, current_domains=current)
		assert domains == [{"domain": "shop.com"}]

	def test_missing_keys_keep_current(self):
		current = [{"domain": "shop.com"}]
		assert apply_api_config({"label": "x"}, True, current) == (True, current)

	def test_public_output_hides_secret(self):
		out = public_api_domains([{"domain": "shop.com", "auth_header": "X-Key", "secret": "k"}, {"domain": "b.com"}])
		assert out == [
			{"domain": "shop.com", "auth_header": "X-Key", "has_secret": True},
			{"domain": "b.com", "auth_header": "", "has_secret": False},
		]

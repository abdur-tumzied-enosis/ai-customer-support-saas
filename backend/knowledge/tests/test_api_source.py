import json
from unittest.mock import MagicMock, patch

import requests
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import Project, ProjectUser
from knowledge.models import KnowledgeBase, Product, ProductSource
from knowledge.product_api import clean_api_config, clean_secret, decrypt_blob, encrypt_blob, fetch_api_table
from knowledge.product_sources import TableError
from knowledge.product_suggest import SuggestionList, column_profile, suggest_mapping
from knowledge.tasks import sync_product_source

User = get_user_model()
BASE = "/api/kb/products/sources/"
API = "https://api.example.com/v1/products"
SECRET = "sk-test-SECRET-123"
ITEMS = [
	{"id": 1, "name": "Shoe", "price": 100, "image": "https://img.example.com/a.jpg"},
	{"id": 2, "name": "Bag", "price": 50.5, "image": None},
]


def api_json(payload, status=200):
	body = json.dumps(payload).encode()
	return MagicMock(status_code=status, headers={}, iter_content=lambda chunk_size: [body], close=lambda: None)


def cfg(**extra):
	return {"method": "GET", "url": API, "auth_type": "bearer", "response_path": "data.items", **extra}


class ApiConfigTests(TestCase):
	def test_rejects_plain_http_and_bad_method(self):
		with self.assertRaises(TableError):
			clean_api_config(cfg(url="http://api.example.com/x"))
		with self.assertRaises(TableError):
			clean_api_config(cfg(method="DELETE"))

	def test_credential_names_refused_as_plain_headers_and_params(self):
		for kwargs in ({"headers": {"X-Api-Key": "a"}}, {"query": {"access_token": "a"}}):
			with self.assertRaises(TableError):
				clean_api_config(cfg(**kwargs))

	def test_secret_required_and_encrypted_round_trip(self):
		with self.assertRaises(TableError):
			clean_secret("bearer", "")
		token = encrypt_blob({"secret": SECRET})
		self.assertNotIn(SECRET, token)
		self.assertEqual(decrypt_blob(token)["secret"], SECRET)

	def test_too_many_pages_is_refused_not_silently_cut(self):
		full = [api_json({"data": {"items": [{"id": n}]}}) for n in (1, 2)]
		with patch("knowledge.product_api._is_public_host", return_value=True), 				patch("knowledge.product_api.requests.request", side_effect=full):
			with self.assertRaises(TableError) as cm:
				fetch_api_table(clean_api_config(cfg(page_param="page", max_pages=2)), SECRET)
		self.assertEqual(cm.exception.code, "too_many_pages")

	def test_api_ignoring_page_number_does_not_repeat_rows(self):
		same = api_json({"data": {"items": ITEMS}})
		with patch("knowledge.product_api._is_public_host", return_value=True), 				patch("knowledge.product_api.requests.request", return_value=same) as req:
			_, rows = fetch_api_table(clean_api_config(cfg(page_param="page", max_pages=5)), SECRET)
		self.assertEqual(len(rows), 2)
		self.assertEqual(req.call_count, 2)

	def test_page_numbers_and_offset_cannot_mix(self):
		with self.assertRaises(TableError):
			clean_api_config(cfg(page_param="page", offset_param="skip", size_param="limit", page_size=10))

	def test_offset_needs_a_sensible_page_size(self):
		with self.assertRaises(TableError):
			clean_api_config(cfg(offset_param="skip", page_size=0))

	def test_page_count_is_capped(self):
		self.assertEqual(clean_api_config(cfg(page_param="page", max_pages=999))["max_pages"], 20)
		self.assertEqual(clean_api_config(cfg(max_pages=5))["max_pages"], 1)  # no page parameter: one request


class ApiFetchTests(TestCase):
	def test_reads_nested_list_and_flattens_values(self):
		with patch("knowledge.product_api._is_public_host", return_value=True), \
				patch("knowledge.product_api.requests.request", return_value=api_json({"data": {"items": ITEMS}})) as req:
			headers, rows = fetch_api_table(clean_api_config(cfg()), SECRET)
		self.assertEqual(headers, ["id", "name", "price", "image"])
		self.assertEqual(rows[0], {"id": "1", "name": "Shoe", "price": "100", "image": "https://img.example.com/a.jpg"})
		self.assertEqual(rows[1]["price"], "50.5")
		self.assertEqual(req.call_args.kwargs["headers"]["Authorization"], f"Bearer {SECRET}")
		self.assertFalse(req.call_args.kwargs["allow_redirects"])

	def test_pages_until_empty_page(self):
		page2 = [{"id": 3, "name": "Hat", "price": 5, "image": None}]
		pages = [api_json({"data": {"items": ITEMS}}), api_json({"data": {"items": page2}}), api_json({"data": {"items": []}})]
		with patch("knowledge.product_api._is_public_host", return_value=True), \
				patch("knowledge.product_api.requests.request", side_effect=pages) as req:
			_, rows = fetch_api_table(clean_api_config(cfg(page_param="page", max_pages=10)), SECRET)
		self.assertEqual([r["name"] for r in rows], ["Shoe", "Bag", "Hat"])
		self.assertEqual([c.kwargs["params"]["page"] for c in req.call_args_list], [1, 2, 3])

	def test_object_response_names_its_fields(self):
		with patch("knowledge.product_api._is_public_host", return_value=True), 				patch("knowledge.product_api.requests.request", return_value=api_json({"products": ITEMS, "total": 2})):
			with self.assertRaises(TableError) as cm:
				fetch_api_table(clean_api_config(cfg(response_path="")), SECRET)
		self.assertEqual(cm.exception.code, "invalid_response")
		self.assertIn("products, total", cm.exception.message)

	def test_offset_paging_stops_at_a_short_page(self):
		pages = [api_json({"data": {"items": ITEMS}}), api_json({"data": {"items": [{"id": 3, "name": "Hat"}]}})]
		config = clean_api_config(cfg(offset_param="skip", size_param="limit", page_size=2, max_pages=5))
		with patch("knowledge.product_api._is_public_host", return_value=True), 				patch("knowledge.product_api.requests.request", side_effect=pages) as req:
			_, rows = fetch_api_table(config, SECRET)
		self.assertEqual([r["name"] for r in rows], ["Shoe", "Bag", "Hat"])
		self.assertEqual([(c.kwargs["params"]["skip"], c.kwargs["params"]["limit"]) for c in req.call_args_list], [(0, 2), (2, 2)])

	def test_offset_paging_refuses_when_pages_remain(self):
		full = [api_json({"data": {"items": ITEMS}}), api_json({"data": {"items": [{"id": 9, "name": "Cap"}, {"id": 8, "name": "Mug"}]}})]
		config = clean_api_config(cfg(offset_param="skip", size_param="limit", page_size=2, max_pages=2))
		with patch("knowledge.product_api._is_public_host", return_value=True), 				patch("knowledge.product_api.requests.request", side_effect=full):
			with self.assertRaises(TableError) as cm:
				fetch_api_table(config, SECRET)
		self.assertEqual(cm.exception.code, "too_many_pages")

	def test_errors_never_echo_the_secret(self):
		config = clean_api_config(cfg(auth_type="query", auth_name="key"))
		with patch("knowledge.product_api._is_public_host", return_value=True), \
				patch("knowledge.product_api.requests.request", side_effect=requests.RequestException(f"failed {API}?key={SECRET}")):
			with self.assertRaises(TableError) as cm:
				fetch_api_table(config, SECRET)
		self.assertNotIn(SECRET, cm.exception.message)

	def test_redirect_and_private_host_refused(self):
		redirect = MagicMock(status_code=302, headers={}, close=lambda: None)
		with patch("knowledge.product_api._is_public_host", return_value=True), \
				patch("knowledge.product_api.requests.request", return_value=redirect):
			with self.assertRaises(TableError) as cm:
				fetch_api_table(clean_api_config(cfg()), SECRET)
		self.assertEqual(cm.exception.code, "redirect_not_followed")
		with patch("knowledge.product_api._is_public_host", return_value=False):
			with self.assertRaises(TableError) as cm:
				fetch_api_table(clean_api_config(cfg()), SECRET)
		self.assertEqual(cm.exception.code, "blocked_host")


@override_settings(IMAGE_SEARCH_ENABLED=False)
class ApiSourceEndpointTests(TestCase):
	def setUp(self):
		self.user = User.objects.create_user(username="a@example.com", email="a@example.com", password="x")
		self.project = Project.objects.create(name="P", slug="p-api")
		ProjectUser.objects.create(project=self.project, user=self.user, role="admin")
		self.kb = KnowledgeBase.objects.create(project=self.project, name="default", is_default=True)
		self.client = APIClient()
		self.client.force_authenticate(user=self.user)

	def ids(self):
		return {"project_id": str(self.project.id), "kb_id": str(self.kb.id)}

	def qs(self):
		return f"project_id={self.project.id}&kb_id={self.kb.id}"  # PATCH reads the KB from the query string

	def body(self, **extra):
		mapping = {"external_id": "id", "name": "name", "price": "price", "image_url": "image"}
		return {**self.ids(), "api": cfg(), "secret": SECRET, "mapping": mapping, **extra}

	def test_create_stores_encrypted_secret_and_never_returns_it(self):
		with patch("knowledge.product_api._is_public_host", return_value=True), \
				patch("knowledge.product_api.requests.request", return_value=api_json({"data": {"items": ITEMS}})), \
				patch("knowledge.tasks.fetch_product_images.delay"), self.captureOnCommitCallbacks(execute=True):
			r = self.client.post(BASE + "api/", self.body(), format="json")
		self.assertEqual(r.status_code, 201, r.content)
		src = ProductSource.objects.get()
		self.assertNotIn(SECRET, src.secret_ciphertext)
		self.assertEqual(decrypt_blob(src.secret_ciphertext)["secret"], SECRET)
		self.assertNotIn(SECRET, r.content.decode())
		self.assertTrue(r.json()["data"]["source"]["has_secret"])
		self.assertEqual(Product.objects.filter(source_ref=src).count(), 2)
		self.assertNotIn(SECRET, self.client.get(BASE, self.ids()).content.decode())

	def test_body_is_encrypted_and_never_returned(self):
		body = {"api_key": "BODYSECRET"}
		with patch("knowledge.product_api._is_public_host", return_value=True), 				patch("knowledge.product_api.requests.request", return_value=api_json({"data": {"items": ITEMS}})), 				patch("knowledge.tasks.fetch_product_images.delay"), self.captureOnCommitCallbacks(execute=True):
			r = self.client.post(BASE + "api/", self.body(api=cfg(method="POST", body=body)), format="json")
		self.assertEqual(r.status_code, 201, r.content)
		src = ProductSource.objects.get()
		self.assertNotIn("BODYSECRET", str(src.api_config))
		self.assertNotIn("BODYSECRET", r.content.decode())
		self.assertEqual(decrypt_blob(src.secret_ciphertext)["body"], body)

	def test_suggest_endpoint_returns_503_when_model_fails(self):
		with patch("knowledge.catalog_views.suggest_mapping", side_effect=RuntimeError("no model")):
			r = self.client.post(BASE + "suggest-mapping/", {**self.ids(), "headers": ["Name"], "sample": [{"Name": "Shoe"}]}, format="json")
		self.assertEqual(r.status_code, 503)
		self.assertEqual(r.json()["error"]["code"], "suggest_failed")

	def test_suggest_endpoint_returns_suggestions(self):
		fake = [{"target": "name", "column": "Name", "confidence": 0.9, "reason": "label"}]
		with patch("knowledge.catalog_views.suggest_mapping", return_value=fake):
			r = self.client.post(BASE + "suggest-mapping/", {**self.ids(), "headers": ["Name"], "sample": [{"Name": "Shoe"}]}, format="json")
		self.assertEqual(r.json()["data"]["suggestions"], fake)

	def test_preview_stores_nothing(self):
		with patch("knowledge.product_api._is_public_host", return_value=True), \
				patch("knowledge.product_api.requests.request", return_value=api_json({"data": {"items": ITEMS}})):
			r = self.client.post(BASE + "api/preview/", self.body(), format="json")
		self.assertEqual(r.status_code, 200)
		self.assertEqual(r.json()["data"]["mapping"]["name"], "name")
		self.assertEqual((ProductSource.objects.count(), Product.objects.count()), (0, 0))

	def test_sync_uses_saved_request_and_key(self):
		src = ProductSource.objects.create(kb=self.kb, kind="api", api_config=clean_api_config(cfg()), secret_ciphertext=encrypt_blob({"secret": SECRET}),
			auto_sync_enabled=True, mapping={"external_id": "id", "name": "name", "price": "price"})
		with patch("knowledge.product_api._is_public_host", return_value=True), \
				patch("knowledge.product_api.requests.request", return_value=api_json({"data": {"items": ITEMS[:1]}})) as req:
			result = sync_product_source(str(src.id))
		self.assertEqual(result["created"], 1)
		self.assertEqual(req.call_args.kwargs["headers"]["Authorization"], f"Bearer {SECRET}")

	def test_schedule_works_for_api_and_csv_is_refused(self):
		src = ProductSource.objects.create(kb=self.kb, kind="api", api_config=clean_api_config(cfg()), auto_sync_enabled=False)
		r = self.client.patch(f"{BASE}{src.id}/schedule/?{self.qs()}", {"auto_sync_enabled": True}, format="json")
		self.assertEqual(r.status_code, 200)
		csv_src = ProductSource.objects.create(kb=self.kb, kind="csv")
		r = self.client.patch(f"{BASE}{csv_src.id}/schedule/?{self.qs()}", {"auto_sync_enabled": True}, format="json")
		self.assertEqual(r.status_code, 400)


class MappingSuggestionTests(TestCase):
	def test_sensitive_columns_withheld_and_urls_stripped(self):
		sample = [{"Email": "a@b.com", "Photo": "https://img.example.com/p.jpg?token=SECRET", "Price": "100", "Name": "Shoe"}]
		profile = {c["name"]: c for c in column_profile(list(sample[0]), sample)}
		self.assertEqual(profile["Email"], {"name": "Email", "type": "withheld"})
		self.assertEqual(profile["Photo"]["type"], "url")
		self.assertEqual(profile["Photo"]["examples"], ["img.example.com/p.jpg"])
		self.assertEqual(profile["Price"]["type"], "number")

	def test_invalid_model_output_is_dropped(self):
		parsed = SuggestionList(suggestions=[
			{"target": "name", "column": "Title", "confidence": 0.9},
			{"target": "name", "column": "Name", "confidence": 0.8},  # target already taken
			{"target": "price", "column": "Title", "confidence": 0.7},  # column already used
			{"target": "ghost", "column": "Price", "confidence": 0.99},  # not a target
			{"target": "price", "column": "Nope", "confidence": 0.99},  # not in the file
		])
		llm = MagicMock()
		llm.with_structured_output.return_value.invoke.return_value = {"parsed": parsed, "raw": MagicMock(usage_metadata={})}
		with patch("knowledge.product_suggest.get_llm", return_value=llm), patch("knowledge.product_suggest.track_token_usage"), \
				patch("knowledge.product_suggest.llm_pricing", return_value={"chat_in": {"cost_usd": None}, "chat_out": {"cost_usd": None}}):
			out = suggest_mapping(["Title", "Name", "Price"], [{"Title": "x", "Name": "Shoe", "Price": "1"}], "p", "k")
		self.assertEqual([(s["target"], s["column"]) for s in out], [("name", "Title")])

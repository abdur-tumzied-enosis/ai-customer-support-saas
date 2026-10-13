from types import SimpleNamespace
from django.test import SimpleTestCase
from knowledge.tasks import normalize_crawl_url, _extract_page_fields


class NormalizeCrawlUrlTests(SimpleTestCase):
	BASE = "https://shop.example.com/category/shirts"

	def test_resolves_relative_and_strips_fragment_and_trailing_slash(self):
		self.assertEqual(normalize_crawl_url("/p/42/#reviews", self.BASE), "https://shop.example.com/p/42")

	def test_fragment_variants_dedupe_to_same_url(self):
		self.assertEqual(normalize_crawl_url("/p/42#a", self.BASE), normalize_crawl_url("/p/42/", self.BASE))

	def test_rejects_unwanted_links(self):
		for href in ["", "mailto:a@b.com", "javascript:void(0)", "https://other.com/p/1", "/files/catalog.PDF", "/img/a.jpg"]:
			self.assertIsNone(normalize_crawl_url(href, self.BASE), href)

	def test_keeps_query_string(self):
		self.assertEqual(normalize_crawl_url("/shop?category=shirt", self.BASE), "https://shop.example.com/shop?category=shirt")


class ExtractPageFieldsTests(SimpleTestCase):
	def _result(self, **overrides):
		data = dict(
			markdown=SimpleNamespace(fit_markdown="", raw_markdown="# Hello"),
			metadata={"title": None},
			links={"internal": [{"href": "/a"}]},
			media={"images": []},
		)
		data.update(overrides)
		return SimpleNamespace(**data)

	def test_null_title_falls_back_to_url(self):
		fields = _extract_page_fields(self._result(), "https://x.com/p")
		self.assertEqual(fields["page_title"], "https://x.com/p")

	def test_falls_back_to_raw_markdown_and_handles_missing_parts(self):
		fields = _extract_page_fields(self._result(metadata=None, links=None, media=None), "https://x.com/p")
		self.assertEqual(fields["markdown_content"], "# Hello")
		self.assertEqual(fields["links_metadata"], {"internal": [], "external": []})

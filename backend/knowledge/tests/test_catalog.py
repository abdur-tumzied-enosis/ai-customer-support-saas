from decimal import Decimal

from django.test import TestCase

from accounts.models import Project
from knowledge.catalog import apply_owner_edit, compute_status, match_product, normalise_name, product_key, sellable
from knowledge.models import KnowledgeBase, Product, ProductField, ProductVariant


class CatalogRulesTest(TestCase):
	def setUp(self):
		self.project = Project.objects.create(name="P", slug="0123456789abcdef0123456789abcd")
		self.kb = KnowledgeBase.objects.create(project=self.project, name="default", vector_collection="vc-a4")

	def test_compute_status_no_price_needs_info(self):
		p = Product(kb=self.kb, name="x", price=None)
		self.assertEqual(compute_status(p, []), "needs_info")

	def test_compute_status_required_custom_missing(self):
		p = Product(kb=self.kb, name="x", price=Decimal("10"), custom={})
		f = ProductField(kb=self.kb, key="size", label="Size", required=True)
		self.assertEqual(compute_status(p, [f]), "needs_info")
		p.custom = {"size": "M"}
		self.assertEqual(compute_status(p, [f]), "active")

	def test_compute_status_all_ok_active(self):
		p = Product(kb=self.kb, name="x", price=Decimal("10"))
		self.assertEqual(compute_status(p, []), "active")

	def test_variant_without_price_falls_back_to_product_price(self):
		p = Product.objects.create(kb=self.kb, name="x", price=Decimal("10"))
		v = ProductVariant.objects.create(product=p, label="L", price=None, available=True)
		self.assertEqual(compute_status(p, []), "active")  # variant falls back to product price
		p.price = None
		self.assertEqual(compute_status(p, []), "needs_info")
		v.delete()

	def test_hidden_is_kept(self):
		p = Product(kb=self.kb, name="x", price=Decimal("10"), status="hidden")
		self.assertEqual(compute_status(p, []), "hidden")

	def test_sellable(self):
		self.assertTrue(sellable(Product(kb=self.kb, name="x", status="active", available=True)))
		self.assertFalse(sellable(Product(kb=self.kb, name="x", status="active", available=False)))
		self.assertFalse(sellable(Product(kb=self.kb, name="x", status="needs_info", available=True)))

	def test_product_key(self):
		self.assertEqual(product_key(Product(kb=self.kb, name="x", external_id="E1", sku="S1")), "E1")
		self.assertEqual(product_key(Product(kb=self.kb, name="x", external_id=None, sku="S1")), "S1")
		self.assertEqual(product_key(Product(kb=self.kb, name="x", external_id=None, sku="")), "")

	def test_normalise_matches_bengali_digits(self):
		self.assertEqual(normalise_name("Saree ৪৫০"), normalise_name("saree 450"))
		self.assertEqual(normalise_name("  Red-Saree!!  Big "), "red saree big")

	def test_normalise_keeps_bengali_marks(self):
		self.assertEqual(normalise_name("শাড়ি"), "শাড়ি")
		self.assertNotEqual(normalise_name("কি"), normalise_name("কী"))
		self.assertEqual(normalise_name("শাড়ি, লাল!"), "শাড়ি লাল")

	def test_match_external_id_beats_sku(self):
		by_sku = Product.objects.create(kb=self.kb, name="a", sku="S1")
		by_ext = Product.objects.create(kb=self.kb, name="b", external_id="E1")
		found, how = match_product(self.kb, "E1", "S1", "zzz", {})
		self.assertEqual(found, by_ext)
		self.assertEqual(how, "external_id")
		found, how = match_product(self.kb, None, "S1", "zzz", {})
		self.assertEqual(found, by_sku)
		self.assertEqual(how, "sku")

	def test_name_match_reports_matched_by_name(self):
		p = Product.objects.create(kb=self.kb, name="Red Saree 450")
		found, how = match_product(self.kb, None, "", "red  saree ৪৫০", {})
		self.assertEqual(found, p)
		self.assertEqual(how, "name")

	def test_name_match_uses_variant_attributes(self):
		p = Product.objects.create(kb=self.kb, name="Tee")
		ProductVariant.objects.create(product=p, label="M", attributes={"Size": "M"})
		self.assertEqual(match_product(self.kb, None, "", "tee", {"size": "m"})[0], p)
		self.assertEqual(match_product(self.kb, None, "", "tee", {"size": "xl"}), (None, ""))

	def test_no_match(self):
		self.assertEqual(match_product(self.kb, "E9", "S9", "nothing", {}), (None, ""))

	def test_other_kb_not_matched(self):
		other = KnowledgeBase.objects.create(project=self.project, name="o", vector_collection="vc-a4b")
		Product.objects.create(kb=other, name="a", external_id="E1")
		self.assertEqual(match_product(self.kb, "E1", "", "a", {}), (None, ""))

	def test_apply_owner_edit_locks_price(self):
		p = Product.objects.create(kb=self.kb, name="x")
		apply_owner_edit(p, {"price": Decimal("99")}, [])
		self.assertEqual(p.price, Decimal("99"))
		self.assertEqual(p.locked_fields, ["price"])
		self.assertEqual(p.status, "active")

	def test_apply_owner_edit_status_is_owner_choice(self):
		p = Product.objects.create(kb=self.kb, name="x", price=Decimal("500"), status="active")
		apply_owner_edit(p, {"status": "needs_info"}, [])
		self.assertEqual(p.status, "needs_info")
		self.assertIn("status", p.locked_fields)

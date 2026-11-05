from django.db import IntegrityError, transaction
from django.test import TestCase

from accounts.models import Project
from knowledge.models import KnowledgeBase, Product


class ProductConstraintTest(TestCase):
	def setUp(self):
		self.project = Project.objects.create(name="P", slug="0123456789abcdef0123456789abcf")
		self.kb = KnowledgeBase.objects.create(project=self.project, name="default", vector_collection="vc-cat1")
		self.kb2 = KnowledgeBase.objects.create(project=self.project, name="other", vector_collection="vc-cat2")

	def test_default_status_needs_info(self):
		p = Product.objects.create(kb=self.kb, name="x")
		self.assertEqual(p.status, "needs_info")

	def test_db_rejects_active_without_price(self):
		with self.assertRaises(IntegrityError):
			with transaction.atomic():
				Product.objects.create(kb=self.kb, name="x", status="active", price=None)

	def test_external_id_unique_per_kb_when_set(self):
		Product.objects.create(kb=self.kb, name="a", external_id="A1")
		with self.assertRaises(IntegrityError):
			with transaction.atomic():
				Product.objects.create(kb=self.kb, name="b", external_id="A1")

	def test_null_external_id_not_unique(self):
		Product.objects.create(kb=self.kb, name="a", external_id=None)
		Product.objects.create(kb=self.kb, name="b", external_id=None)
		self.assertEqual(Product.objects.filter(kb=self.kb).count(), 2)

	def test_same_external_id_allowed_in_other_kb(self):
		Product.objects.create(kb=self.kb, name="a", external_id="A1")
		Product.objects.create(kb=self.kb2, name="a", external_id="A1")
		self.assertEqual(Product.objects.filter(external_id="A1").count(), 2)

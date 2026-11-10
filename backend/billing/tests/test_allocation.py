import importlib
import threading
from decimal import Decimal
from unittest import mock

import django.apps
from django.core.cache import cache
from django.db import connection
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient

from accounts.models import Project, ProjectUser, User
from accounts.views import purge_project
from billing import service
from billing.models import CreditTransaction, CreditWallet, PricingSettings
from channels_app.models import Conversation, CustomerContact, Message
from knowledge.models import KnowledgeBase

SIGNUP = {"first_name": "Jo", "last_name": "Doe", "email": "jo@example.com", "password": "testpass123", "terms_version": "2026-10-29"}


def signup(client):
	r = client.post("/api/auth/signup/", SIGNUP, format="json")
	assert r.status_code == 201, r.content
	return User.objects.get(email=SIGNUP["email"])


class AllocationTests(TestCase):
	def setUp(self):
		cache.clear()
		self.client = APIClient()

	def test_initial_allocation_once(self):
		projects = [Project.objects.create(name=n, slug=n) for n in ("a", "b")]
		migration = importlib.import_module("billing.migrations.0003_initial_allocation")
		migration.allocate_initial(django.apps.apps, None)
		migration.allocate_initial(django.apps.apps, None)
		for p in projects:
			self.assertEqual(CreditWallet.objects.get(project=p).balance, Decimal("100.00"))
			self.assertEqual(CreditTransaction.objects.filter(wallet__project=p, kind="initial_allocation").count(), 1)

	def test_signup_sets_created_by(self):
		user = signup(self.client)
		self.assertEqual(Project.objects.get(members__user=user).created_by, user)

	def test_verify_grants_signup_bonus(self):
		user = signup(self.client)
		with mock.patch("accounts.views.check_verification_code", return_value=True):
			r = self.client.post("/api/auth/verify-email/", {"email": user.email, "code": "123456"}, format="json")
		self.assertEqual(r.status_code, 200, r.content)
		project = Project.objects.get(created_by=user)
		self.assertEqual(service.balance_of(project), Decimal("100.00"))
		self.assertEqual(CreditTransaction.objects.get(wallet__project=project).kind, "signup_bonus")
		service.grant_signup_bonus(user)
		self.assertEqual(CreditTransaction.objects.filter(wallet__project=project).count(), 1)

	def test_unverified_existing_user_no_second_allocation(self):
		user = User.objects.create_user("u", "u@example.com", "pw", email_verified=False)
		project = Project.objects.create(name="own", slug="own", created_by=user)
		importlib.import_module("billing.migrations.0003_initial_allocation").allocate_initial(django.apps.apps, None)
		with mock.patch("accounts.views.check_verification_code", return_value=True):
			r = self.client.post("/api/auth/verify-email/", {"email": user.email, "code": "123456"}, format="json")
		self.assertEqual(r.status_code, 200, r.content)
		self.assertFalse(CreditTransaction.objects.filter(kind="signup_bonus").exists())
		self.assertEqual(service.balance_of(project), Decimal("100.00"))

	def test_invited_member_still_gets_bonus(self):
		user = User.objects.create_user("u", "u@example.com", "pw")
		other = Project.objects.create(name="other", slug="other")
		ProjectUser.objects.create(project=other, user=user, role="member")
		own = Project.objects.create(name="own", slug="own", created_by=user)
		service.grant_signup_bonus(user)
		self.assertEqual(service.balance_of(own), Decimal("100.00"))
		self.assertEqual(service.balance_of(other), Decimal("0"))

	def test_second_created_project_gets_no_bonus(self):
		user = User.objects.create_user("u", "u@example.com", "pw")
		first = Project.objects.create(name="first", slug="first", created_by=user)
		service.grant_signup_bonus(user)
		second = Project.objects.create(name="second", slug="second", created_by=user)
		self.assertIsNone(service.grant_signup_bonus(user))
		self.assertEqual(service.balance_of(first), Decimal("100.00"))
		self.assertEqual(service.balance_of(second), Decimal("0"))

	def test_signup_bonus_disabled(self):
		PricingSettings(signup_credits=0).save()
		user = User.objects.create_user("u", "u@example.com", "pw")
		Project.objects.create(name="own", slug="own", created_by=user)
		self.assertIsNone(service.grant_signup_bonus(user))
		self.assertFalse(CreditTransaction.objects.exists())

	def test_existing_verified_user_no_bonus(self):
		user = User.objects.create_user("u", "u@example.com", "pw", email_verified=True)
		Project.objects.create(name="own", slug="own", created_by=user)
		importlib.import_module("billing.migrations.0003_initial_allocation").allocate_initial(django.apps.apps, None)
		self.assertFalse(CreditTransaction.objects.filter(kind="signup_bonus").exists())

	def test_purge_project_keeps_ledger(self):
		project = Project.objects.create(name="Gone", slug="gone")
		kb = KnowledgeBase.objects.create(project=project, name="kb", vector_collection="vc-alloc")
		contact = CustomerContact.objects.create(project=project, platform="facebook", external_id="p1", display_name="Ann")
		conv = Conversation.objects.create(project=project, contact=contact, knowledge_base=kb)
		msg = Message.objects.create(project=project, contact=contact, conversation=conv, direction="in", text="hi")
		txn = service.charge(project, "reply", key="k1", conversation=conv, trigger_message=msg)
		with mock.patch("agents.vector_store.delete_collection"), mock.patch("core.storage.delete_prefix"):
			purge_project(project)
		wallet = CreditWallet.objects.get()
		self.assertIsNone(wallet.project)
		self.assertEqual(wallet.project_name, "Gone")
		txn.refresh_from_db()
		self.assertIsNone(txn.conversation)
		self.assertIsNone(txn.trigger_message)


class ConcurrentSignupBonusTest(TransactionTestCase):
	def test_simultaneous_claims_grant_one_bonus(self):
		user = User.objects.create_user("c", "c@example.com", "pw")
		project = Project.objects.create(name="own", slug="own-conc", created_by=user)

		def work():
			try:
				service.grant_signup_bonus(user)
			finally:
				connection.close()

		threads = [threading.Thread(target=work) for _ in range(4)]
		for t in threads:
			t.start()
		for t in threads:
			t.join()
		self.assertEqual(CreditTransaction.objects.filter(kind="signup_bonus").count(), 1)
		self.assertEqual(service.balance_of(project), Decimal("100.00"))

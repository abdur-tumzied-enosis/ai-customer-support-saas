import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from accounts.models import Project, ProjectUser
from billing import service
from billing.models import ActionPrice, CreditPackage, CreditTransaction, PricingSettings
from channels_app.models import Conversation, CustomerContact, Message
from knowledge.models import KnowledgeBase

User = get_user_model()
FORBIDDEN = ["usd", "usd_per_credit", "target", "notes", "overdraft", "cost"]


class BillingApiBase(TestCase):
	def setUp(self):
		self.project = Project.objects.create(name="P", slug="p-bapi")
		self.admin = User.objects.create_user(username="a@example.com", email="a@example.com", password="x")
		self.member = User.objects.create_user(username="m@example.com", email="m@example.com", password="x")
		self.stranger = User.objects.create_user(username="s@example.com", email="s@example.com", password="x")
		ProjectUser.objects.create(project=self.project, user=self.admin, role="admin")
		ProjectUser.objects.create(project=self.project, user=self.member, role="member")
		self.client = APIClient()

	def get(self, user, url, **params):
		self.client.force_login(user)
		return self.client.get(url, {"project_id": str(self.project.id), **params})

	def assert_clean(self, res):
		text = json.dumps(res.json()).lower()
		for word in FORBIDDEN:
			self.assertNotIn(word, text)


class WalletTest(BillingApiBase):
	def wallet(self):
		return self.get(self.member, "/api/billing/wallet/").json()["data"]

	def test_flags_at_150_50_0(self):
		self.assertEqual(self.wallet(), {"balance": "0.00", "low": True, "empty": True})
		service.grant(self.project, Decimal("50"), "adjust", key="a")
		self.assertEqual(self.wallet(), {"balance": "50.00", "low": True, "empty": False})
		service.grant(self.project, Decimal("100"), "adjust", key="b")
		self.assertEqual(self.wallet(), {"balance": "150.00", "low": False, "empty": False})

	def test_two_projects_two_balances_membership_untouched(self):
		other = Project.objects.create(name="Q", slug="q-bapi")
		ProjectUser.objects.create(project=other, user=self.admin, role="member")
		service.grant(self.project, Decimal("10"), "adjust", key="a")
		service.grant(other, Decimal("70"), "adjust", key="b")
		self.client.force_login(self.admin)
		balance = lambda p: self.client.get("/api/billing/wallet/", {"project_id": str(p.id)}).json()["data"]["balance"]
		self.assertEqual((balance(self.project), balance(other)), ("10.00", "70.00"))
		ProjectUser.objects.filter(project=self.project, user=self.admin).delete()
		self.assertEqual(service.balance_of(self.project), Decimal("10"))
		self.assertEqual(CreditTransaction.objects.filter(wallet__project=self.project).count(), 1)

	def test_non_member_404(self):
		self.assertEqual(self.get(self.stranger, "/api/billing/wallet/").status_code, 404)

	def test_no_leak(self):
		self.assert_clean(self.get(self.member, "/api/billing/wallet/"))


class TransactionsTest(BillingApiBase):
	def setUp(self):
		super().setUp()
		service.grant(self.project, Decimal("100"), "grant", key="g", note="Welcome")
		service.charge(self.project, "reply", key="c1")
		service.charge(self.project, "reply", key="c2")

	def test_member_forbidden_stranger_404(self):
		self.assertEqual(self.get(self.member, "/api/billing/transactions/").status_code, 403)
		self.assertEqual(self.get(self.stranger, "/api/billing/transactions/").status_code, 404)

	def test_admin_rows_and_filter(self):
		data = self.get(self.admin, "/api/billing/transactions/").json()["data"]
		self.assertEqual(data["count"], 3)
		first = data["results"][0]
		self.assertEqual(set(first), {"id", "created_at", "kind", "action", "action_label", "amount", "balance_after", "note", "conversation_id"})
		self.assertEqual((first["amount"], first["action_label"]), ("-1.00", "AI reply"))
		grant = data["results"][-1]
		self.assertEqual((grant["kind"], grant["note"], grant["amount"]), ("grant", "Welcome", "100.00"))
		filtered = self.get(self.admin, "/api/billing/transactions/", kind="charge").json()["data"]
		self.assertEqual((filtered["count"], len(filtered["results"])), (2, 2))

	def test_charge_note_hidden(self):
		CreditTransaction.objects.filter(kind="charge").update(note="internal")
		rows = self.get(self.admin, "/api/billing/transactions/", kind="charge").json()["data"]["results"]
		self.assertTrue(all(r["note"] == "" for r in rows))

	def test_limit_capped(self):
		for i in range(105):
			service.grant(self.project, Decimal("1"), "adjust", key=f"x{i}")
		data = self.get(self.admin, "/api/billing/transactions/", limit=500).json()["data"]
		self.assertEqual((len(data["results"]), data["count"]), (100, 108))

	def test_no_leak(self):
		self.assert_clean(self.get(self.admin, "/api/billing/transactions/"))


class PricingTest(TestCase):
	def test_public_and_clean(self):
		ActionPrice.objects.update_or_create(action="reply", defaults={"credits": Decimal("1"), "notes": "secret cost note"})
		ActionPrice.objects.update_or_create(action="photo_index", defaults={"credits": Decimal("0")})
		CreditPackage.objects.all().delete()
		CreditPackage.objects.create(name="Starter", credits=1000, bonus_credits=100, price=Decimal("500"), features=["a"], highlighted=True)
		CreditPackage.objects.create(name="Off", credits=1, price=Decimal("1"), active=False)
		res = APIClient().get("/api/billing/pricing/")
		self.assertEqual(res.status_code, 200)
		text = json.dumps(res.json()).lower()
		for word in FORBIDDEN:
			self.assertNotIn(word, text)
		data = res.json()["data"]
		self.assertEqual([p["name"] for p in data["packages"]], ["Starter"])
		self.assertEqual(set(data["packages"][0]), {"id", "name", "credits", "bonus_credits", "price", "currency", "features", "highlighted"})
		self.assertEqual([a["action"] for a in data["actions"]], ["reply", "photo_search", "product_import", "order_setup"])
		self.assertEqual(data["actions"][0], {"action": "reply", "label": "AI reply", "credits": "1.00"})
		self.assertEqual(data["signup_credits"], f"{PricingSettings.get().signup_credits:.2f}")


class ConversationCreditsTest(BillingApiBase):
	def setUp(self):
		super().setUp()
		self.kb = KnowledgeBase.objects.create(project=self.project, name="kb", vector_collection="vc-bapi")
		self.contact = CustomerContact.objects.create(project=self.project, platform="facebook", external_id="psid")
		self.conv = Conversation.objects.create(project=self.project, knowledge_base=self.kb, contact=self.contact)
		self.inb = self.msg("in", "hi")
		self.out = self.msg("out", "yo")
		self.plain = self.msg("in", "x")
		service.grant(self.project, Decimal("100"), "adjust", key="seed")
		service.charge(self.project, "reply", key="r", conversation=self.conv, trigger_message=self.inb, result_message=self.out)
		service.charge(self.project, "photo_search", key="p", conversation=self.conv, trigger_message=self.inb)
		self.client.force_login(self.member)
		self.url = f"/api/channels/conversations/{self.conv.id}/messages"

	def msg(self, direction, text):
		return Message.objects.create(project=self.project, contact=self.contact, conversation=self.conv, direction=direction, text=text)

	def test_list_credits_used(self):
		res = self.client.get("/api/channels/conversations", {"project_slug": self.project.slug})
		self.assertEqual(res.json()["data"]["conversations"][0]["credits_used"], "2.00")

	def test_list_zero_default(self):
		Conversation.objects.filter(pk=self.conv.pk).delete()
		Conversation.objects.create(project=self.project, knowledge_base=self.kb, contact=self.contact)
		res = self.client.get("/api/channels/conversations", {"project_slug": self.project.slug})
		self.assertEqual(res.json()["data"]["conversations"][0]["credits_used"], "0")

	def test_detail_credits_used(self):
		res = self.client.get(f"/api/channels/conversations/{self.conv.id}")
		self.assertEqual(res.json()["data"]["conversation"]["credits_used"], "2.00")

	def test_message_credits(self):
		by_id = {m["id"]: m["credits"] for m in self.client.get(self.url).json()["data"]["messages"]}
		self.assertEqual(by_id[str(self.out.id)], [{"action": "reply", "label": "AI reply", "credits": "1.00"}])
		self.assertEqual(by_id[str(self.inb.id)], [{"action": "photo_search", "label": "Photo search", "credits": "1.00"}])
		self.assertEqual(by_id[str(self.plain.id)], [])

	def test_messages_query_count_does_not_grow(self):
		with CaptureQueriesContext(connection) as before:
			self.client.get(self.url)
		for i in range(5):
			m = self.msg("out", f"o{i}")
			service.charge(self.project, "reply", key=f"k{i}", conversation=self.conv, result_message=m)
		with CaptureQueriesContext(connection) as after:
			self.client.get(self.url)
		self.assertEqual(len(before), len(after))


class CreditRequestTest(BillingApiBase):
	def setUp(self):
		super().setUp()
		self.pkg = CreditPackage.objects.create(name="Starter", credits=100, price=Decimal("500"))
		self.url = "/api/billing/credit-requests/"

	def post(self, user, url, **data):
		self.client.force_login(user)
		return self.client.post(url, {"project_id": str(self.project.id), **data}, format="json")

	def test_request_then_payment(self):
		from unittest.mock import patch
		with patch("billing.api.notify_telegram.delay") as tg, self.captureOnCommitCallbacks(execute=True):
			res = self.post(self.admin, self.url, package_id=self.pkg.id, phone="01700000000")
			self.assertEqual(res.status_code, 201)
			rid = res.json()["data"]["id"]
			res = self.post(self.admin, f"{self.url}{rid}/payment/", trx_id="ABC123", bkash_phone="01800000000")
		self.assertEqual(res.status_code, 200)
		self.assertEqual(tg.call_count, 2)
		from billing.models import CreditRequest
		r = CreditRequest.objects.get(pk=rid)
		self.assertEqual((r.status, r.trx_id), ("paid", "ABC123"))

	def test_validation_and_access(self):
		self.assertEqual(self.post(self.admin, self.url, package_id=self.pkg.id, phone="1").status_code, 400)
		self.assertEqual(self.post(self.member, self.url, package_id=self.pkg.id, phone="01700000000").status_code, 403)

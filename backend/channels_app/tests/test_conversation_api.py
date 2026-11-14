from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Project, ProjectUser
from billing import service
from channels_app.models import CustomerContact, Message
from channels_app.service.conversation_service import ensure_contact_conversation
from knowledge.models import KnowledgeBase


class ConversationCreateTest(TestCase):
	def test_malformed_knowledge_base_id_is_404(self):
		user = get_user_model().objects.create_user(username="u@x.com", email="u@x.com", password="pw-12345678")
		project = Project.objects.create(name="P", slug="p-conv")
		ProjectUser.objects.create(project=project, user=user, role="admin")
		client = APIClient()
		client.force_authenticate(user=user)
		res = client.post("/api/channels/conversations", {"project_slug": "p-conv", "knowledge_base_id": "not-a-uuid"}, format="json")
		assert res.status_code == 404


class TranscriptionCreditsTest(TestCase):
	def setUp(self):
		user = get_user_model().objects.create_user(username="t@x.com", email="t@x.com", password="pw-12345678")
		self.project = Project.objects.create(name="P", slug="p-tr")
		ProjectUser.objects.create(project=self.project, user=user, role="admin")
		self.client = APIClient()
		self.client.force_authenticate(user=user)
		kb = KnowledgeBase.objects.create(project=self.project, name="kb", vector_collection="vc-tr")
		contact = CustomerContact.objects.create(project=self.project, platform="facebook", external_id="psid-tr", display_name="Ann")
		self.conv = ensure_contact_conversation(contact, kb, name="Ann")
		self.msg = Message.objects.create(project=self.project, contact=contact, conversation=self.conv, direction="in", text="hi")
		service.grant(self.project, Decimal("10"), "adjust", key="seed")

	def _credits(self):
		res = self.client.get(f"/api/channels/conversations/{self.conv.id}/messages")
		return next(m for m in res.json()["data"]["messages"] if m["id"] == str(self.msg.id))["credits"]

	def _charge(self):
		return service.charge(self.project, "transcription", key=f"transcription:{self.msg.id}:0", units=2,
		                      conversation=self.conv, trigger_message=self.msg)

	def test_transcription_charge_shows_on_the_customer_message(self):
		self._charge()
		assert self._credits() == [{"action": "transcription", "label": "Voice note transcription", "credits": "2.00"}]

	def test_refunded_transcription_charge_is_hidden(self):
		txn = self._charge()
		service.grant(self.project, -txn.amount, "adjust", key=f"transcription_refund:{self.msg.id}:0")
		assert not any(c["action"] == "transcription" for c in self._credits())

import socket
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Project, ProjectUser
from channels_app.models import CustomerContact, Message, ProjectWebhook
from channels_app.service.conversation_service import create_conversation, ensure_contact_conversation
from knowledge.models import KnowledgeBase

BASE = "/api/channels"
PUBLIC = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
PRIVATE = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 0))]


class HandoffApiTest(TestCase):
	def setUp(self):
		self.user = get_user_model().objects.create_user(username="a@x.com", email="a@x.com", password="pw123456")
		self.project = Project.objects.create(name="P", slug="p-api")
		ProjectUser.objects.create(project=self.project, user=self.user, role="admin")
		self.client = APIClient()
		self.client.force_authenticate(self.user)
		kb = KnowledgeBase.objects.create(project=self.project, name="kb", vector_collection="vc-api")
		contact = CustomerContact.objects.create(project=self.project, platform="facebook", external_id="psid", display_name="Ann")
		self.fb = ensure_contact_conversation(contact, kb, name="Ann")
		self.ui = create_conversation(project=self.project, knowledge_base=kb, user_id=str(self.user.id))
		self.emit = mock.patch("channels_app.handoff.emit_event").start()
		self.api_emit = mock.patch("channels_app.api.emit_event").start()
		self.send = mock.patch("channels_app.tasks.send_text_message", return_value={"message_id": "mid"}).start()
		cred = mock.Mock()
		cred.get_credentials.return_value.page_access_token = "tok"
		mock.patch("channels_app.tasks.get_facebook_credential", return_value=cred).start()
		self.addCleanup(mock.patch.stopall)

	def customer_says(self, text="hi", hours_ago=0):
		m = Message.objects.create(project=self.project, contact=self.fb.contact, conversation=self.fb, direction="in", text=text)
		if hours_ago:
			Message.objects.filter(id=m.id).update(created_at=timezone.now() - timedelta(hours=hours_ago))
		return m

	def test_handoff_and_resume(self):
		r = self.client.post(f"{BASE}/conversations/{self.fb.id}/handoff", {"reason": "VIP"}, format="json")
		assert r.status_code == 200, r.content
		conv = r.json()["data"]["conversation"]
		assert conv["is_paused"] and conv["handoff_reason"] == "VIP" and conv["resumes_at"]
		r = self.client.post(f"{BASE}/conversations/{self.fb.id}/resume", format="json")
		assert r.json()["data"]["conversation"]["is_paused"] is False
		assert [c.args[1] for c in self.emit.call_args_list] == ["conversation.handoff", "conversation.resumed"]

	def test_non_member_rejected(self):
		other = get_user_model().objects.create_user(username="b@x.com", email="b@x.com", password="pw123456")
		self.client.force_authenticate(other)
		assert self.client.post(f"{BASE}/conversations/{self.fb.id}/handoff", format="json").status_code == 400

	def test_staff_reply_facebook_sends_and_pauses(self):
		self.customer_says()
		r = self.client.post(f"{BASE}/conversations/{self.fb.id}/staff-reply", {"text": "Hi Ann, Sam here"}, format="json")
		assert r.status_code == 201, r.content
		assert r.json()["data"]["message"]["sender"] == "staff"
		self.send.assert_called_once()
		self.fb.refresh_from_db()
		assert self.fb.is_paused() and self.fb.handoff_reason == "staff took over"
		assert self.api_emit.call_args.args[1] == "message.staff_reply"

	def test_staff_reply_facebook_outside_window_rejected(self):
		self.customer_says(hours_ago=25)
		r = self.client.post(f"{BASE}/conversations/{self.fb.id}/staff-reply", {"text": "late"}, format="json")
		assert r.status_code == 400 and r.json()["error"]["code"] == "outside_messaging_window"
		assert not Message.objects.filter(sender="staff").exists()

	def test_staff_reply_facebook_without_inbound_is_rejected(self):
		r = self.client.post(f"{BASE}/conversations/{self.fb.id}/staff-reply", {"text": "hello?"}, format="json")
		assert r.status_code == 400 and r.json()["error"]["code"] == "outside_messaging_window"

	def test_staff_reply_send_failure_returns_502_and_keeps_message(self):
		self.customer_says()
		self.send.return_value = {}
		r = self.client.post(f"{BASE}/conversations/{self.fb.id}/staff-reply", {"text": "Hi"}, format="json")
		assert r.status_code == 502 and r.json()["error"]["code"] == "send_failed"
		assert Message.objects.get(sender="staff").meta["send_error"]

	def test_staff_reply_text_validated(self):
		r = self.client.post(f"{BASE}/conversations/{self.ui.id}/staff-reply", {"text": "  "}, format="json")
		assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_text"

	def test_staff_reply_on_test_conversation_needs_no_window(self):
		r = self.client.post(f"{BASE}/conversations/{self.ui.id}/staff-reply", {"text": "Staff here"}, format="json")
		assert r.status_code == 201
		self.send.assert_not_called()

	@mock.patch("channels_app.api.schedule_reply")
	def test_test_message_only_for_self_conversations(self, schedule):
		r = self.client.post(f"{BASE}/conversations/{self.fb.id}/messages", {"text": "hi"}, format="json")
		assert r.status_code == 400 and r.json()["error"]["code"] == "not_a_test_conversation"
		r = self.client.post(f"{BASE}/conversations/{self.ui.id}/messages", {"text": "hi"}, format="json")
		assert r.status_code == 201
		schedule.assert_called_once()

	def test_list_needs_human_filter_order_and_fields(self):
		self.fb.handoff_at = timezone.now()
		self.fb.handoff_reason = "angry"
		self.fb.save()
		r = self.client.get(f"{BASE}/conversations", {"project_slug": self.project.slug})
		data = r.json()["data"]
		assert data["needs_human_count"] == 1
		first = data["conversations"][0]
		assert first["id"] == str(self.fb.id) and first["channel"] == "facebook" and first["contact_display_name"] == "Ann"
		r = self.client.get(f"{BASE}/conversations", {"project_slug": self.project.slug, "needs_human": "1"})
		assert [c["id"] for c in r.json()["data"]["conversations"]] == [str(self.fb.id)]

	def test_messages_include_sender(self):
		self.customer_says("hello")
		r = self.client.get(f"{BASE}/conversations/{self.fb.id}/messages")
		assert r.json()["data"]["messages"][0]["sender"] == "customer"


	def test_failed_staff_send_is_marked_failed_without_webhook(self):
		self.customer_says()
		self.send.return_value = {}
		r = self.client.post(f"{BASE}/conversations/{self.fb.id}/staff-reply", {"text": "Hi"}, format="json")
		assert r.status_code == 502
		assert Message.objects.get(sender="staff").status == "failed"
		self.api_emit.assert_not_called()



@mock.patch("agents.api_tool.socket.getaddrinfo", return_value=PUBLIC)
class WebhookApiTest(TestCase):
	def setUp(self):
		user = get_user_model().objects.create_user(username="c@x.com", email="c@x.com", password="pw123456")
		self.project = Project.objects.create(name="P", slug="p-wh")
		ProjectUser.objects.create(project=self.project, user=user, role="admin")
		self.client = APIClient()
		self.client.force_authenticate(user)

	def test_get_creates_with_secret(self, _dns):
		r = self.client.get(f"{BASE}/webhook", {"project_slug": self.project.slug})
		hook = r.json()["data"]["webhook"]
		assert len(hook["secret"]) == 64 and hook["is_active"] is False and hook["url"] == ""

	def test_put_validates_url(self, dns):
		ok = self.client.put(f"{BASE}/webhook", {"project_slug": self.project.slug, "url": "https://hooks.example.com/x", "is_active": True}, format="json")
		assert ok.status_code == 200 and ok.json()["data"]["webhook"]["is_active"] is True
		dns.return_value = PRIVATE
		bad = self.client.put(f"{BASE}/webhook", {"project_slug": self.project.slug, "url": "https://internal.example.com", "is_active": True}, format="json")
		assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_url"
		no_url = self.client.put(f"{BASE}/webhook", {"project_slug": self.project.slug, "url": "", "is_active": True}, format="json")
		assert no_url.status_code == 400

	def test_rotate_secret(self, _dns):
		old = self.client.get(f"{BASE}/webhook", {"project_slug": self.project.slug}).json()["data"]["webhook"]["secret"]
		new = self.client.post(f"{BASE}/webhook/rotate-secret", {"project_slug": self.project.slug}, format="json").json()["data"]["webhook"]["secret"]
		assert new != old and ProjectWebhook.objects.get(project=self.project).secret == new

	@mock.patch("channels_app.webhooks.requests.post")
	def test_send_test_event(self, post, _dns):
		post.return_value = mock.Mock(status_code=204)
		self.client.put(f"{BASE}/webhook", {"project_slug": self.project.slug, "url": "https://hooks.example.com/x", "is_active": True}, format="json")
		r = self.client.post(f"{BASE}/webhook/test", {"project_slug": self.project.slug}, format="json")
		assert r.status_code == 200 and r.json()["data"] == {"ok": True}
		assert post.call_args.kwargs["headers"]["X-AMP-Event"] == "ping"

	@mock.patch("channels_app.webhooks.requests.post")
	def test_send_test_event_hides_endpoint_status(self, post, _dns):
		post.return_value = mock.Mock(status_code=500)
		self.client.put(f"{BASE}/webhook", {"project_slug": self.project.slug, "url": "https://hooks.example.com/x", "is_active": True}, format="json")
		r = self.client.post(f"{BASE}/webhook/test", {"project_slug": self.project.slug}, format="json")
		assert r.status_code == 502 and r.json()["error"]["code"] == "delivery_failed"
		assert b"500" not in r.content

import hashlib
import hmac
import json
from unittest import mock

from django.test import Client, SimpleTestCase, TestCase

from accounts.models import Project
from channels_app.facebook_client import WHATSAPP_MAX_TEXT_CHARS, send_whatsapp_text
from channels_app.models import ChannelCredential, CustomerContact, Message
from channels_app.reply import deliver
from channels_app.service.message_service import create_outbound_message
from channels_app.service.whatsapp_messaging import handle_whatsapp_message
from knowledge.models import KnowledgeBase


def payload(*messages, phone_number_id="pn-1", statuses=()):
	return {"object": "whatsapp_business_account", "entry": [{"id": "waba-1", "changes": [{"field": "messages", "value": {
		"messaging_product": "whatsapp",
		"metadata": {"display_phone_number": "15550001111", "phone_number_id": phone_number_id},
		"contacts": [{"wa_id": "8801700000000", "profile": {"name": "Ann Lee"}}],
		"messages": list(messages),
		**({"statuses": list(statuses)} if statuses else {}),
	}}]}]}


def text(wamid, body="hi", **extra):
	return {"from": "8801700000000", "id": wamid, "timestamp": "1700000000", "type": "text", "text": {"body": body}, **extra}


class WhatsAppBase(TestCase):
	def setUp(self):
		self.project = Project.objects.create(name="P", slug="p-wa")
		self.kb = KnowledgeBase.objects.create(project=self.project, name="kb", vector_collection="vc-wa")
		self.cred = ChannelCredential.objects.create(
			project=self.project, channel="whatsapp", knowledge_base=self.kb,
			credentials={"phone_number_id": "pn-1", "access_token": "wa-tok", "verify_token": "wa-verify", "app_secret": "wa-secret"},
		)


@mock.patch("channels_app.service.whatsapp_messaging.schedule_reply")
class WhatsAppInboundTest(WhatsAppBase):
	def test_text_creates_whatsapp_conversation_and_schedules_reply(self, schedule):
		handle_whatsapp_message(payload(text("wamid.1")))
		contact = CustomerContact.objects.get(external_id="8801700000000")
		assert contact.platform == "whatsapp" and contact.display_name == "Ann Lee"
		assert contact.conversation.channel == "whatsapp" and contact.conversation.name == "Ann Lee"
		msg = Message.objects.get(external_message_id="wamid.1")
		assert msg.text == "hi" and msg.status == "pending" and msg.conversation_id == contact.conversation_id
		schedule.assert_called_once_with(msg)

	def test_duplicate_delivery_is_skipped(self, schedule):
		handle_whatsapp_message(payload(text("wamid.1")))
		handle_whatsapp_message(payload(text("wamid.1")))
		assert Message.objects.count() == 1 and schedule.call_count == 1

	def test_reply_context_links_quoted_message(self, schedule):
		handle_whatsapp_message(payload(text("wamid.1")))
		handle_whatsapp_message(payload(text("wamid.2", context={"from": "15550001111", "id": "wamid.1"})))
		msg = Message.objects.get(external_message_id="wamid.2")
		assert msg.is_reply and msg.reply_to_message.external_message_id == "wamid.1"

	def test_media_is_stored_without_reply(self, schedule):
		image = {"from": "8801700000000", "id": "wamid.img", "type": "image", "image": {"id": "media-1", "mime_type": "image/jpeg"}}
		handle_whatsapp_message(payload(image))
		msg = Message.objects.get(external_message_id="wamid.img")
		assert msg.has_attachments and msg.status == "completed"
		assert msg.attachment_info == [{"type": "image", "payload": {"id": "media-1", "mime_type": "image/jpeg"}}]
		schedule.assert_not_called()

	def test_status_updates_and_unknown_numbers_are_ignored(self, schedule):
		handle_whatsapp_message(payload(statuses=[{"id": "wamid.out", "status": "delivered"}]))
		handle_whatsapp_message(payload(text("wamid.1"), phone_number_id="unknown"))
		assert not Message.objects.exists() and not CustomerContact.objects.exists()
		schedule.assert_not_called()


@mock.patch("channels_app.service.whatsapp_messaging.schedule_reply")
class WhatsAppSendTest(WhatsAppBase):
	def setUp(self):
		super().setUp()
		handle_whatsapp_message(payload(text("wamid.1")))
		self.inbound = Message.objects.get(external_message_id="wamid.1")
		self.conv = self.inbound.conversation

	def out(self, body):
		return create_outbound_message(project=self.project, contact=self.conv.contact, text=body, conversation=self.conv)

	@mock.patch("channels_app.tasks.send_whatsapp_text", return_value={"message_id": "wamid.out"})
	def test_reply_uses_whatsapp_credential_and_splits_long_text(self, send, schedule):
		out = self.out("Our shop is open. " * 400)
		assert deliver(self.conv, out, self.inbound)
		assert send.call_count > 1
		for call in send.call_args_list:
			assert call.args[:3] == ("wa-tok", "pn-1", "8801700000000")
			assert len(call.args[3]) <= WHATSAPP_MAX_TEXT_CHARS
		out.refresh_from_db()
		assert out.external_message_id == "wamid.out"

	@mock.patch("channels_app.tasks.send_whatsapp_text", return_value={"error": "Re-engagement message"})
	def test_send_failure_records_api_error(self, send, schedule):
		out = self.out("Hello")
		assert not deliver(self.conv, out, self.inbound)
		out.refresh_from_db()
		assert out.meta["send_error"] == "Re-engagement message"


@mock.patch("channels_app.service.whatsapp_messaging.schedule_reply")
class WhatsAppWebhookTest(WhatsAppBase):
	def post(self, body, secret):
		sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
		return Client().post("/webhooks/facebook/", data=body, content_type="application/json",
			HTTP_X_HUB_SIGNATURE_256=f"sha256={sig}")

	def test_signed_with_the_whatsapp_apps_own_secret(self, schedule):
		body = json.dumps(payload(text("wamid.1"))).encode()
		assert self.post(body, "wa-secret").status_code == 200
		schedule.assert_called_once()

	def test_wrong_secret_is_rejected(self, schedule):
		body = json.dumps(payload(text("wamid.1"))).encode()
		assert self.post(body, "nope").status_code == 403
		schedule.assert_not_called()

	def test_verification_with_whatsapp_verify_token(self, schedule):
		resp = Client().get("/webhooks/facebook/", {"hub.mode": "subscribe", "hub.verify_token": "wa-verify", "hub.challenge": "42"})
		assert resp.status_code == 200 and resp.content == b"42"


class SendWhatsAppTextTest(SimpleTestCase):
	@mock.patch("channels_app.facebook_client.requests.post")
	def test_returns_message_id_or_api_error(self, post):
		post.return_value.status_code = 200
		post.return_value.json.return_value = {"messages": [{"id": "wamid.out"}]}
		assert send_whatsapp_text("tok", "pn-1", "880", "hi") == {"message_id": "wamid.out"}
		assert post.call_args.kwargs["json"] == {"messaging_product": "whatsapp", "to": "880", "type": "text", "text": {"body": "hi"}}

		post.return_value.status_code = 400
		post.return_value.json.return_value = {"error": {"code": 131047, "message": "Re-engagement message"}}
		assert send_whatsapp_text("tok", "pn-1", "880", "hi") == {"error": "Re-engagement message"}

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import Project, ProjectUser
from channels_app.api import _message_dict
from channels_app.models import CustomerContact, Message
from channels_app.service.conversation_service import create_conversation, ensure_contact_conversation
from channels_app.service.message_service import create_outbound_message
from knowledge.models import KnowledgeBase


class OutboundPhotoTest(TestCase):
	def setUp(self):
		self.project = Project.objects.create(name="P", slug="p-staff-photo", image_search_allowed=True)
		self.kb = KnowledgeBase.objects.create(project=self.project, name="kb", vector_collection="vc-staff-photo")
		self.contact = CustomerContact.objects.create(project=self.project, platform="whatsapp", external_id="w1")
		self.conv = ensure_contact_conversation(self.contact, self.kb)

	def message(self, info, **extra):
		return Message.objects.create(
			project=self.project, contact=self.contact, conversation=self.conv, direction="in", text="",
			has_attachments=bool(info), attachment_info=info, **extra,
		)

	def test_outbound_message_records_attachment(self):
		msg = create_outbound_message(
			project=self.project, contact=self.contact, conversation=self.conv, text="here",
			sender="staff", attachment_info=[{"type": "image", "path": "chat_media/p/x.jpg"}],
		)
		msg.refresh_from_db()
		assert msg.has_attachments is True
		assert msg.attachment_info == [{"type": "image", "path": "chat_media/p/x.jpg"}]

	def test_outbound_message_without_attachment_is_unchanged(self):
		msg = create_outbound_message(project=self.project, contact=self.contact, conversation=self.conv, text="hi")
		assert msg.has_attachments is False and msg.attachment_info == []

	def test_photo_urls_lists_every_stored_photo(self):
		msg = self.message([
			{"type": "image", "path": "chat_media/p/a.webp"},
			{"type": "audio", "path": "chat_media/p/v.ogg"},
			{"type": "image", "path": "chat_media/p/b.webp"},
		])
		assert _message_dict(msg)["photo_urls"] == [
			f"/api/channels/messages/{msg.id}/photo?index=0",
			f"/api/channels/messages/{msg.id}/photo?index=1",
		]

	def test_photo_urls_empty_without_a_stored_photo(self):
		unstored = self.message([{"type": "image", "payload": {"url": "https://scontent.xx.fbcdn.net/x"}}])
		d = _message_dict(unstored)
		assert d["has_photo"] is True and d["photo_urls"] == []
		plain = self.message([])
		assert _message_dict(plain)["has_photo"] is False and _message_dict(plain)["photo_urls"] == []

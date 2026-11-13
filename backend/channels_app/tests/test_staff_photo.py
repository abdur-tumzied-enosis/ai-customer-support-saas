from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import TestCase

from accounts.models import Project, ProjectUser
from agents.image_search import make_send_variant
from channels_app import media_store
from channels_app.api import _message_dict
from channels_app.models import CustomerContact, Message
from channels_app.service.conversation_service import create_conversation, ensure_contact_conversation
from channels_app.service.message_service import create_outbound_message
from channels_app.tests.test_widget import png_bytes
from core.tests.utils import fresh_storage, stored_files
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


@fresh_storage
class OutboundStoreTest(TestCase):
	def setUp(self):
		self.project = Project.objects.create(name="P", slug="p-staff-photo", image_search_allowed=True)
		self.kb = KnowledgeBase.objects.create(project=self.project, name="kb", vector_collection="vc-staff-photo")
		self.contact = CustomerContact.objects.create(project=self.project, platform="whatsapp", external_id="w1")
		self.conv = ensure_contact_conversation(self.contact, self.kb)
		self.tmp = f"tmp/chat/p-staff-photo/{self.conv.id}/abc.jpg"

	def upload(self, data=None):
		default_storage.save(self.tmp, ContentFile(png_bytes() if data is None else data))
		return {"type": "image", "path": self.tmp}

	def test_send_variant_is_jpeg(self):
		data = make_send_variant(png_bytes(600))
		assert data[:3] == b"\xff\xd8\xff"  # JPEG magic

	def test_store_outbound_photo_writes_jpeg_and_thumb(self):
		key = media_store.store_outbound_photo(self.conv, self.upload())
		assert key.startswith("chat_media/p-staff-photo/") and key.endswith(".jpg")
		files = stored_files("chat_media/p-staff-photo/")
		assert key in files and f"{key[:-4]}_thumb.webp" in files
		assert not stored_files(f"tmp/chat/p-staff-photo/{self.conv.id}/")  # temp copy deleted

	def test_store_outbound_photo_rejects_non_image(self):
		with self.assertRaises(Exception):
			media_store.store_outbound_photo(self.conv, self.upload(b"nope, not an image"))

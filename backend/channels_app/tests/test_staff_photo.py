from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import TestCase
from rest_framework.test import APIClient

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


@fresh_storage
class MessagePhotoViewTest(TestCase):
	def setUp(self):
		self.user = get_user_model().objects.create_user(username="pp@x.com", email="pp@x.com", password="pw123456")
		self.project = Project.objects.create(name="P", slug="p-photo-view", image_search_allowed=True)
		ProjectUser.objects.create(project=self.project, user=self.user, role="admin")
		self.kb = KnowledgeBase.objects.create(project=self.project, name="kb", vector_collection="vc-photo-view")
		self.contact = CustomerContact.objects.create(project=self.project, platform="whatsapp", external_id="w1")
		self.conv = ensure_contact_conversation(self.contact, self.kb)
		self.client = APIClient()
		self.client.force_authenticate(self.user)

	def photo_message(self, info, conv=None):
		conv = conv or self.conv
		return Message.objects.create(
			project=conv.project, contact=conv.contact, conversation=conv, direction="in", text="",
			has_attachments=True, attachment_info=info,
		)

	def get(self, msg, **params):
		query = "".join(f"&{k}={v}" for k, v in params.items())
		return self.client.get(f"/api/channels/messages/{msg.id}/photo?index=0{query}")

	def test_redirects_to_presigned_url(self):
		msg = self.photo_message([{"type": "image", "path": "chat_media/p-photo-view/x.jpg"}])
		with mock.patch("channels_app.api.presign_get", return_value="https://r2.test/x.jpg") as presign:
			r = self.get(msg)
		assert r.status_code == 302 and r["Location"] == "https://r2.test/x.jpg"
		presign.assert_called_once_with("chat_media/p-photo-view/x.jpg")

	def test_thumb_variant_is_presigned(self):
		msg = self.photo_message([{"type": "image", "path": "chat_media/p-photo-view/x.jpg"}])
		with mock.patch("channels_app.api.presign_get", return_value="https://r2.test/t.webp") as presign:
			assert self.get(msg, size="thumb").status_code == 302
		presign.assert_called_once_with("chat_media/p-photo-view/x_thumb.webp")

	def test_second_index_addresses_the_second_photo(self):
		msg = self.photo_message([
			{"type": "image", "path": "chat_media/p-photo-view/a.jpg"},
			{"type": "image", "path": "chat_media/p-photo-view/b.jpg"},
		])
		with mock.patch("channels_app.api.presign_get", return_value="https://r2.test/b.jpg") as presign:
			r = self.client.get(f"/api/channels/messages/{msg.id}/photo?index=1")
		assert r.status_code == 302
		presign.assert_called_once_with("chat_media/p-photo-view/b.jpg")

	def test_404_for_missing_index_or_unstored_photo(self):
		msg = self.photo_message([
			{"type": "image", "path": "chat_media/p-photo-view/a.jpg"},
			{"type": "image", "payload": {"url": "https://scontent.xx.fbcdn.net/x"}},
		])
		assert self.client.get(f"/api/channels/messages/{msg.id}/photo?index=1").status_code == 404
		assert self.client.get(f"/api/channels/messages/{msg.id}/photo?index=5").status_code == 404
		assert self.client.get(f"/api/channels/messages/{msg.id}/photo").status_code == 404  # no index

	def test_404_for_another_project(self):
		other = Project.objects.create(name="O", slug="o-photo-view")
		okb = KnowledgeBase.objects.create(project=other, name="o", vector_collection="vc-o-view")
		ocontact = CustomerContact.objects.create(project=other, platform="whatsapp", external_id="w9")
		msg = self.photo_message([{"type": "image", "path": "chat_media/o-photo-view/x.jpg"}], conv=ensure_contact_conversation(ocontact, okb))
		assert self.get(msg).status_code == 404

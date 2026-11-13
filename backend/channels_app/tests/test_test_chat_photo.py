from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import Project, ProjectUser
from channels_app.api import _message_dict
from channels_app.models import Message
from channels_app.service.conversation_service import create_conversation
from channels_app.tests.test_widget import fake_presign, png_bytes
from core.tests.utils import fresh_storage
from knowledge.models import KnowledgeBase


@fresh_storage
@override_settings(IMAGE_SEARCH_ENABLED=True)
class TestChatPhotoTest(TestCase):
	def setUp(self):
		self.user = get_user_model().objects.create_user(username="tc@x.com", email="tc@x.com", password="pw123456")
		self.project = Project.objects.create(name="P", slug="p-tc-photo", image_search_allowed=True)
		ProjectUser.objects.create(project=self.project, user=self.user, role="admin")
		self.kb = KnowledgeBase.objects.create(project=self.project, name="kb", vector_collection="vc-tc-photo")
		self.conv = create_conversation(project=self.project, knowledge_base=self.kb, user_id=str(self.user.id))
		self.client = APIClient()
		self.client.force_authenticate(self.user)
		mock.patch("channels_app.media.presign_put", side_effect=fake_presign).start()
		self.store = mock.patch("channels_app.api.store_message_media.delay").start()
		self.addCleanup(mock.patch.stopall)

	def url(self, conv=None, presign=False):
		return f"/api/channels/conversations/{(conv or self.conv).id}/photo" + ("/presign" if presign else "")

	def presign(self, conv=None, content_type="image/jpeg", size=100):
		return self.client.post(self.url(conv, presign=True), {"content_type": content_type, "size": size}, format="json")

	def uploaded_key(self, data=None):
		key = self.presign().json()["data"]["key"]
		default_storage.save(key, ContentFile(png_bytes() if data is None else data))
		return key

	def post(self, conv=None, key=None, **extra):
		return self.client.post(self.url(conv), {"key": key or self.uploaded_key(), **extra}, format="json")

	def test_presign_returns_conversation_scoped_key(self):
		r = self.presign()
		assert r.status_code == 200, r.content
		key = r.json()["data"]["key"]
		assert key.startswith(f"tmp/chat/p-tc-photo/{self.conv.id}/") and key.endswith(".jpg")

	def test_presign_rejects_large(self):
		r = self.presign(size=5 * 1024 * 1024 + 1)
		assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_image"

	def test_test_chat_photo_queues_store(self):
		r = self.post(text="this one?")
		assert r.status_code == 201, r.content
		data = r.json()["data"]["message"]
		assert data["has_photo"] is True and data["text"] == "this one?"
		msg = Message.objects.get(id=data["id"])
		assert msg.meta["photo_reply"] is True and msg.has_attachments
		assert msg.attachment_info[0]["path"].startswith(f"tmp/chat/p-tc-photo/{self.conv.id}/")
		self.store.assert_called_once_with(str(msg.id), True)

	def test_confirm_rejects_foreign_key(self):
		other_conv = create_conversation(project=self.project, knowledge_base=self.kb, user_id=str(self.user.id))
		key = self.uploaded_key()
		for k in (key.replace(str(self.conv.id), str(other_conv.id)), f"tmp/chat/p-tc-photo/{self.conv.id}/../x.jpg", None):
			with self.subTest(key=k):
				r = self.client.post(self.url(), {"key": k}, format="json")
				assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_upload"
		assert self.client.post(self.url(other_conv), {"key": key}, format="json").json()["error"]["code"] == "invalid_upload"
		self.store.assert_not_called()

	def test_test_chat_photo_invalid_image_400(self):
		r = self.post(key=self.uploaded_key(b"nope, not an image"))
		assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_image"
		self.store.assert_not_called()

	def test_test_chat_photo_tenant_off_404(self):
		Project.objects.filter(id=self.project.id).update(image_search_enabled=False)
		for r in (self.presign(), self.client.post(self.url(), {"key": "tmp/x"}, format="json")):
			assert r.status_code == 404 and r.json()["error"]["code"] == "feature_disabled"
		self.store.assert_not_called()

	def test_test_chat_photo_other_project_404(self):
		other = Project.objects.create(name="O", slug="o-tc-photo", image_search_allowed=True)
		okb = KnowledgeBase.objects.create(project=other, name="o", vector_collection="vc-o-tc")
		oconv = create_conversation(project=other, knowledge_base=okb, user_id="x")
		assert self.presign(conv=oconv).status_code == 404
		assert self.client.post(self.url(oconv), {"key": "tmp/x"}, format="json").status_code == 404

	def test_test_chat_photo_non_self_conversation_400(self):
		from channels_app.models import CustomerContact
		from channels_app.service.conversation_service import ensure_contact_conversation
		contact = CustomerContact.objects.create(project=self.project, platform="whatsapp", external_id="w1")
		conv = ensure_contact_conversation(contact, self.kb)
		for r in (self.presign(conv=conv), self.client.post(self.url(conv), {"key": "tmp/x"}, format="json")):
			assert r.status_code == 400 and r.json()["error"]["code"] == "not_a_test_conversation"

	def test_message_dict_exposes_photo_matches(self):
		msg = Message.objects.create(project=self.project, contact=self.conv.contact, conversation=self.conv,
			direction="in", text="t", has_attachments=True, attachment_info=[{"type": "image", "path": "chat_media/x.webp"}],
			meta={"photo_matches": "Red shirt"})
		d = _message_dict(msg)
		assert d["has_photo"] is True and d["photo_matches"] == "Red shirt"
		plain = Message.objects.create(project=self.project, contact=self.conv.contact, conversation=self.conv, direction="in", text="t")
		assert _message_dict(plain)["has_photo"] is False and _message_dict(plain)["photo_matches"] == ""

	def test_message_dict_renders_photo_notes(self):
		msg = Message.objects.create(project=self.project, contact=self.conv.contact, conversation=self.conv,
			direction="in", text="t", has_attachments=True, attachment_info=[{"type": "image", "path": "chat_media/x.webp"}],
			meta={"photo_notes": [{"note": "[Customer sent photo 1. No catalog product looks similar. Photo attached.]"}, {"note": ""}]})
		assert _message_dict(msg)["photo_matches"] == "[Customer sent photo 1. No catalog product looks similar. Photo attached.]"

	def test_has_photo_only_for_photo_attachments(self):
		from channels_app.widget import _visitor_message
		cases = [
			([{"type": "image", "payload": {"url": "https://scontent.xx.fbcdn.net/x", "sticker_id": 369239263222822}}], False),
			([{"type": "audio", "payload": {"url": "https://cdn.fbsbx.com/a.mp4"}}], False),
			([{"type": "file", "payload": {}}, "junk"], False),
			([{"type": "audio"}, {"type": "image", "payload": {"url": "https://scontent.xx.fbcdn.net/x"}}], True),
		]
		for info, expected in cases:
			msg = Message.objects.create(project=self.project, contact=self.conv.contact, conversation=self.conv,
				direction="in", text="", has_attachments=True, attachment_info=info)
			assert _message_dict(msg)["has_photo"] is expected, info
			assert _visitor_message(msg)["has_photo"] is expected, info

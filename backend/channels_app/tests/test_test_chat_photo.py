import tempfile
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import Project, ProjectUser
from channels_app.api import _message_dict
from channels_app.models import Message
from channels_app.service.conversation_service import create_conversation
from channels_app.tests.test_widget import png_upload
from knowledge.models import KnowledgeBase


@override_settings(IMAGE_SEARCH_ENABLED=True)
class TestChatPhotoTest(TestCase):
	def setUp(self):
		tmp = tempfile.TemporaryDirectory()
		self.addCleanup(tmp.cleanup)
		media = override_settings(MEDIA_ROOT=tmp.name)
		media.enable()
		self.addCleanup(media.disable)
		self.user = get_user_model().objects.create_user(username="tc@x.com", email="tc@x.com", password="pw123456")
		self.project = Project.objects.create(name="P", slug="p-tc-photo", image_search_allowed=True)
		ProjectUser.objects.create(project=self.project, user=self.user, role="admin")
		self.kb = KnowledgeBase.objects.create(project=self.project, name="kb", vector_collection="vc-tc-photo")
		self.conv = create_conversation(project=self.project, knowledge_base=self.kb, user_id=str(self.user.id))
		self.client = APIClient()
		self.client.force_authenticate(self.user)
		self.schedule = mock.patch("channels_app.api.schedule_reply").start()
		self.addCleanup(mock.patch.stopall)

	def post(self, conv=None, **extra):
		return self.client.post(f"/api/channels/conversations/{(conv or self.conv).id}/photo",
			{"image": png_upload(), **extra}, format="multipart")

	def test_test_chat_photo_schedules_reply(self):
		r = self.post(text="this one?")
		assert r.status_code == 201, r.content
		data = r.json()["data"]["message"]
		assert data["has_photo"] is True and data["text"] == "this one?"
		msg = Message.objects.get(id=data["id"])
		assert msg.meta["photo_reply"] is True and msg.has_attachments
		assert msg.attachment_info[0]["path"].startswith("chat_uploads/p-tc-photo/")
		self.schedule.assert_called_once_with(msg)

	def test_test_chat_photo_tenant_off_404(self):
		Project.objects.filter(id=self.project.id).update(image_search_enabled=False)
		r = self.post()
		assert r.status_code == 404 and r.json()["error"]["code"] == "feature_disabled"
		self.schedule.assert_not_called()

	def test_test_chat_photo_other_project_404(self):
		other = Project.objects.create(name="O", slug="o-tc-photo", image_search_allowed=True)
		okb = KnowledgeBase.objects.create(project=other, name="o", vector_collection="vc-o-tc")
		oconv = create_conversation(project=other, knowledge_base=okb, user_id="x")
		assert self.post(conv=oconv).status_code == 404

	def test_test_chat_photo_non_self_conversation_400(self):
		from channels_app.models import CustomerContact
		from channels_app.service.conversation_service import ensure_contact_conversation
		contact = CustomerContact.objects.create(project=self.project, platform="whatsapp", external_id="w1")
		r = self.post(conv=ensure_contact_conversation(contact, self.kb))
		assert r.status_code == 400 and r.json()["error"]["code"] == "not_a_test_conversation"

	def test_test_chat_photo_invalid_image_400(self):
		from django.core.files.uploadedfile import SimpleUploadedFile
		r = self.client.post(f"/api/channels/conversations/{self.conv.id}/photo",
			{"image": SimpleUploadedFile("x.png", b"nope")}, format="multipart")
		assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_image"

	def test_message_dict_exposes_photo_matches(self):
		msg = Message.objects.create(project=self.project, contact=self.conv.contact, conversation=self.conv,
			direction="in", text="t", has_attachments=True, attachment_info=[{"type": "image", "path": "chat_uploads/x.png"}],
			meta={"photo_matches": "Red shirt"})
		d = _message_dict(msg)
		assert d["has_photo"] is True and d["photo_matches"] == "Red shirt"
		plain = Message.objects.create(project=self.project, contact=self.conv.contact, conversation=self.conv, direction="in", text="t")
		assert _message_dict(plain)["has_photo"] is False and _message_dict(plain)["photo_matches"] == ""

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

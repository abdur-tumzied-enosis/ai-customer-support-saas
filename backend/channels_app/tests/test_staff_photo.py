from unittest import mock

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Project, ProjectUser
from agents.image_search import make_send_variant
from channels_app import media_store
from channels_app.api import _message_dict
from channels_app.models import Conversation, CustomerContact, Message
from channels_app.reply import _facebook_send, _send_parts, _whatsapp_send
from channels_app.service.conversation_service import create_conversation, ensure_contact_conversation
from channels_app.service.message_service import create_outbound_message
from channels_app.tests.test_widget import fake_presign, png_bytes
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


class DeliverImagePartTest(TestCase):
	def setUp(self):
		self.project = Project.objects.create(name="P", slug="p-deliver-img")
		self.kb = KnowledgeBase.objects.create(project=self.project, name="kb", vector_collection="vc-deliver-img")
		self.contact = CustomerContact.objects.create(project=self.project, platform="facebook", external_id="psid")
		self.conv = ensure_contact_conversation(self.contact, self.kb)
		self.image = mock.patch("channels_app.tasks.send_image_message", return_value={"message_id": "img1"}).start()
		self.text = mock.patch("channels_app.tasks.send_text_message", return_value={"message_id": "txt1"}).start()
		cred = mock.Mock()
		cred.get_credentials.return_value.page_access_token = "tok"
		mock.patch("channels_app.tasks.get_facebook_credential", return_value=cred).start()
		self.addCleanup(mock.patch.stopall)
		self.msg = Message.objects.create(
			project=self.project, contact=self.contact, conversation=self.conv, direction="out",
			text="hello", status="pending", attachment_info=[{"type": "image", "path": "chat_media/p/a.jpg"}],
		)

	def send_parts(self, parts, conv=None):
		conv = conv or self.conv
		with mock.patch("channels_app.reply.presign_get", return_value="https://r2.test/a.jpg"):
			return _send_parts(self.msg, parts, _facebook_send(conv, self.msg, self.msg), conv.channel)

	def test_image_part_reaches_send_image_message(self):
		assert self.send_parts([{"image": "chat_media/p/a.jpg"}]) is True
		assert [c.kwargs["image_url"] for c in self.image.call_args_list] == ["https://r2.test/a.jpg"]
		assert self.msg.meta["parts_sent"] == 1

	def test_image_then_text_sends_both(self):
		assert self.send_parts([{"image": "chat_media/p/a.jpg"}, "hello"]) is True
		assert [c.kwargs["text"] for c in self.text.call_args_list] == ["hello"]
		assert self.msg.meta["parts_sent"] == 2

	def test_resume_after_the_image_part_skips_it(self):
		Message.objects.filter(id=self.msg.id).update(meta={"parts_sent": 1, "parts_total": 2})
		self.msg.refresh_from_db()
		assert self.send_parts([{"image": "chat_media/p/a.jpg"}, "hello"]) is True
		self.image.assert_not_called()  # the photo already landed
		assert self.text.call_count == 1

	def test_whatsapp_image_part_uses_image_link(self):
		wa = mock.patch("channels_app.tasks.send_whatsapp_image", return_value={"message_id": "w1"}).start()
		creds = mock.Mock(access_token="tok", phone_number_id="123")
		cred = mock.Mock()
		cred.get_credentials.return_value = creds
		mock.patch("channels_app.tasks.get_whatsapp_credential", return_value=cred).start()
		CustomerContact.objects.filter(id=self.contact.id).update(platform="whatsapp")  # channel follows contact.platform
		self.conv.refresh_from_db()
		self.contact.refresh_from_db()
		with mock.patch("channels_app.reply.presign_get", return_value="https://r2.test/a.jpg"):
			assert _send_parts(self.msg, [{"image": "chat_media/p/a.jpg"}], _whatsapp_send(self.conv, self.msg, self.msg), "whatsapp") is True
		assert [c.kwargs["image_url"] for c in wa.call_args_list] == ["https://r2.test/a.jpg"]

	def test_unknown_image_send_holds_the_message(self):
		self.image.return_value = {"unknown": True}
		assert self.send_parts([{"image": "chat_media/p/a.jpg"}]) is False
		assert self.msg.meta["inflight"] == 0


@fresh_storage
class StaffPhotoApiTest(TestCase):
	def setUp(self):
		self.user = get_user_model().objects.create_user(username="sp@x.com", email="sp@x.com", password="pw123456")
		self.project = Project.objects.create(name="P", slug="p-staff-api", image_search_allowed=True)
		ProjectUser.objects.create(project=self.project, user=self.user, role="admin")
		self.kb = KnowledgeBase.objects.create(project=self.project, name="kb", vector_collection="vc-staff-api")
		self.contact = CustomerContact.objects.create(project=self.project, platform="facebook", external_id="psid")
		self.conv = ensure_contact_conversation(self.contact, self.kb)
		self.client = APIClient()
		self.client.force_authenticate(self.user)
		mock.patch("channels_app.media.presign_put", side_effect=fake_presign).start()
		self.deliver = mock.patch("channels_app.api.deliver", return_value=True).start()
		self.addCleanup(mock.patch.stopall)
		# a recent customer message: staff may only send free-form inside the 24h window
		self.last_in = Message.objects.create(
			project=self.project, contact=self.contact, conversation=self.conv, direction="in", text="hi")

	def url(self, presign=False, conv=None):
		return f"/api/channels/conversations/{(conv or self.conv).id}/staff-reply/photo" + ("/presign" if presign else "")

	def uploaded_key(self, conv=None, data=None):
		key = self.client.post(self.url(presign=True, conv=conv), {"content_type": "image/jpeg", "size": 100}, format="json").json()["data"]["key"]
		default_storage.save(key, ContentFile(png_bytes() if data is None else data))
		return key

	def test_presign_issues_conversation_scoped_key(self):
		r = self.client.post(self.url(presign=True), {"content_type": "image/jpeg", "size": 100}, format="json")
		assert r.status_code == 200, r.content
		assert r.json()["data"]["key"].startswith(f"tmp/chat/p-staff-api/{self.conv.id}/")

	def test_presign_rejects_bad_type_and_size(self):
		for body in ({"content_type": "image/gif", "size": 100}, {"content_type": "image/jpeg", "size": 6 * 1024 * 1024}):
			r = self.client.post(self.url(presign=True), body, format="json")
			assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_image"

	def test_send_creates_a_stored_jpeg_outbound_message(self):
		r = self.client.post(self.url(), {"key": self.uploaded_key(), "text": "this one?"}, format="json")
		assert r.status_code == 201, r.content
		data = r.json()["data"]["message"]
		assert data["photo_urls"] == [f"/api/channels/messages/{data['id']}/photo?index=0"]
		assert data["text"] == "this one?"
		msg = Message.objects.get(id=data["id"])
		assert msg.sender == "staff" and msg.direction == "out"
		assert msg.attachment_info[0]["path"].startswith("chat_media/p-staff-api/") and msg.attachment_info[0]["path"].endswith(".jpg")
		self.deliver.assert_called_once()

	def test_send_without_text_is_allowed(self):
		r = self.client.post(self.url(), {"key": self.uploaded_key()}, format="json")
		assert r.status_code == 201 and r.json()["data"]["message"]["text"] == ""

	def stored(self, path="chat_media/p-staff-api/"):
		"""Files under `path`; a directory that was never created counts as empty (stored_files raises)."""
		try:
			return stored_files(path)
		except FileNotFoundError:
			return []

	def test_send_rejects_foreign_key(self):
		other = ensure_contact_conversation(
			CustomerContact.objects.create(project=self.project, platform="facebook", external_id="psid2"), self.kb)
		# inside the window, so the upload check is what refuses it and not the messaging window
		Message.objects.create(project=self.project, contact=other.contact, conversation=other, direction="in", text="hi")
		r = self.client.post(self.url(conv=other), {"key": self.uploaded_key()}, format="json")
		assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_upload"
		self.deliver.assert_not_called()

	def test_send_rejects_non_image_body_and_leaves_no_message(self):
		before = self.stored()
		r = self.client.post(self.url(), {"key": self.uploaded_key(data=b"nope")}, format="json")
		assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_image"
		assert not Message.objects.filter(conversation=self.conv, direction="out").exists()
		assert self.stored() == before  # this request stored nothing

	def test_send_outside_24h_window_refused(self):
		Message.objects.filter(conversation=self.conv, direction="in").update(created_at=timezone.now() - timedelta(hours=25))
		r = self.client.post(self.url(), {"key": self.uploaded_key()}, format="json")
		assert r.status_code == 400 and r.json()["error"]["code"] == "outside_messaging_window"
		self.deliver.assert_not_called()

	def test_send_without_any_inbound_message_refused(self):
		Message.objects.filter(conversation=self.conv, direction="in").delete()
		r = self.client.post(self.url(), {"key": self.uploaded_key()}, format="json")
		assert r.status_code == 400 and r.json()["error"]["code"] == "outside_messaging_window"
		self.deliver.assert_not_called()

	def test_definite_failure_marks_failed_and_deletes_the_photo(self):
		self.deliver.return_value = False
		r = self.client.post(self.url(), {"key": self.uploaded_key(), "text": "x"}, format="json")
		assert r.status_code == 502 and r.json()["error"]["code"] == "send_failed"
		msg = Message.objects.get(conversation=self.conv, direction="out")
		assert msg.status == "failed"
		key = msg.attachment_info[0]["path"]
		assert key not in self.stored() and f"{key[:-4]}_thumb.webp" not in self.stored()

	def test_unknown_outcome_keeps_the_photo(self):
		def deliver_and_leave_inflight(conversation, msg, reference_msg):
			msg.meta = {**msg.meta, "inflight": 0}
			msg.save(update_fields=["meta"])
			return False
		self.deliver.side_effect = deliver_and_leave_inflight
		r = self.client.post(self.url(), {"key": self.uploaded_key()}, format="json")
		assert r.status_code == 502 and r.json()["error"]["code"] == "send_unknown"
		msg = Message.objects.get(conversation=self.conv, direction="out")
		assert msg.status == "unknown"
		assert msg.attachment_info[0]["path"] in self.stored()  # the customer may already have it

	def test_test_conversation_can_send_staff_photo(self):
		ui = create_conversation(project=self.project, knowledge_base=self.kb, user_id=str(self.user.id))
		key = self.uploaded_key(conv=ui)
		assert self.client.post(self.url(conv=ui), {"key": key}, format="json").status_code == 201

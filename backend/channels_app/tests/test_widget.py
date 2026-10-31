from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import Project, ProjectUser
from channels_app.models import CustomerContact, Message, Widget, normalize_origin
from channels_app.reply import process_reply
from channels_app.service.conversation_service import create_conversation
from channels_app.tasks import build_reply_context
from knowledge.models import KnowledgeBase

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "widget-tests"}}
DASH = "/api/channels/widgets"


@override_settings(CACHES=LOCMEM)  # throttle counters must not leak between tests or into the dev Redis
class WidgetBase(TestCase):
	def setUp(self):
		from django.core.cache import cache
		cache.clear()
		self.user = get_user_model().objects.create_user(username="w@x.com", email="w@x.com", password="pw123456")
		self.project = Project.objects.create(name="P", slug="p-widget")
		ProjectUser.objects.create(project=self.project, user=self.user, role="admin")
		self.kb = KnowledgeBase.objects.create(project=self.project, name="kb", vector_collection="vc-widget")
		self.widget = Widget.objects.create(
			project=self.project, name="Site", knowledge_base=self.kb, allowed_origins=["https://shop.example"]
		)
		self.dashboard = APIClient()
		self.dashboard.force_authenticate(self.user)
		self.visitor = APIClient()
		self.schedule = mock.patch("channels_app.widget.schedule_reply").start()
		self.addCleanup(mock.patch.stopall)

	def token(self, widget=None):
		r = self.visitor.post(f"/api/widget/{(widget or self.widget).public_key}/session")
		assert r.status_code == 201, r.content
		return r.json()["data"]["token"]

	def as_visitor(self, token):
		self.visitor.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

	def say(self, text="hi", **extra):
		return self.visitor.post("/api/widget/messages", {"text": text, **extra}, format="json")


class OriginTest(TestCase):
	def test_normalize(self):
		assert normalize_origin(" https://Shop.Example/page?q=1 ") == "https://shop.example"
		assert normalize_origin("http://localhost:3000") == "http://localhost:3000"
		assert normalize_origin("https://*.shop.example") == "https://*.shop.example"
		for bad in ["shop.example", "ftp://shop.example", "https://", "https://a.*.example", "https://u:p@shop.example",
				"https://a.com;script-src *", "https://a.com x", "https://a.com:99999", "https://a.com:x"]:
			with self.assertRaises(ValidationError, msg=bad):
				normalize_origin(bad)


class DashboardApiTest(WidgetBase):
	def test_create_normalizes_and_defaults(self):
		r = self.dashboard.post(DASH, {
			"project_slug": self.project.slug, "name": "Shop",
			"allowed_origins": ["https://Shop.example/x", "https://shop.example"],
			"appearance": {"primary_color": "#ff0000", "evil": "x"},
		}, format="json")
		assert r.status_code == 201, r.content
		w = r.json()["data"]["widget"]
		assert w["allowed_origins"] == ["https://shop.example"]
		assert w["appearance"]["primary_color"] == "#ff0000" and "evil" not in w["appearance"]
		assert w["knowledge_base"]["id"] == str(KnowledgeBase.get_default(self.project).id)
		assert w["public_key"].startswith("wgt_") and w["public_key"] in w["snippet"]

	def test_rejects_bad_input(self):
		other_kb = KnowledgeBase.objects.create(project=Project.objects.create(name="O", slug="o"), name="o", vector_collection="vc-o")
		for data in [
			{"name": ""},
			{"knowledge_base_id": str(other_kb.id)},
			{"knowledge_base_id": "not-a-uuid"},
			{"allowed_origins": ["javascript:alert(1)"]},
			{"allowed_origins": "https://shop.example"},
			{"appearance": {"primary_color": "red;}"}},
			{"appearance": {"position": "top"}},
			{"appearance": {"privacy_url": "javascript:alert(1)"}},
			{"appearance": {"show_branding": "no"}},
		]:
			r = self.dashboard.patch(f"{DASH}/{self.widget.id}", data, format="json")
			assert r.status_code == 400, (data, r.content)

	def test_other_users_cannot_see_or_edit(self):
		other = get_user_model().objects.create_user(username="o@x.com", email="o@x.com", password="pw123456")
		self.dashboard.force_authenticate(other)
		assert self.dashboard.get(DASH, {"project_slug": self.project.slug}).status_code == 400
		assert self.dashboard.patch(f"{DASH}/{self.widget.id}", {"name": "x"}, format="json").status_code == 400
		assert self.dashboard.delete(f"{DASH}/{self.widget.id}").status_code == 400
		assert Widget.objects.filter(id=self.widget.id, name="Site").exists()


class EmbedTest(WidgetBase):
	def test_embed_sets_frame_ancestors_and_public_config_only(self):
		self.widget.appearance = {"title": "Hello <script>"}
		self.widget.save()
		r = self.client.get(f"/embed/{self.widget.public_key}/")
		assert r.status_code == 200
		assert "frame-ancestors https://shop.example" in r["Content-Security-Policy"]
		assert "X-Frame-Options" not in r
		body = r.content.decode()
		assert str(self.kb.id) not in body and "<script>\"" not in body and "Hello \\u003Cscript\\u003E" in body

	def test_embed_config_carries_disclosure_links(self):
		r = self.dashboard.patch(f"{DASH}/{self.widget.id}", {
			"appearance": {"privacy_url": "https://shop.example/privacy", "show_branding": False},
		}, format="json")
		assert r.status_code == 200, r.content
		body = self.client.get(f"/embed/{self.widget.public_key}/").content.decode()
		assert '"privacy_url": "https://shop.example/privacy"' in body and '"show_branding": false' in body
		assert f'"site_url": "{settings.SITE_URL}"' in body

	def test_no_origins_means_nowhere(self):
		self.widget.allowed_origins = []
		self.widget.save()
		assert "frame-ancestors 'none'" in self.client.get(f"/embed/{self.widget.public_key}/")["Content-Security-Policy"]

	def test_inactive_or_unknown_is_404(self):
		self.widget.is_active = False
		self.widget.save()
		assert self.client.get(f"/embed/{self.widget.public_key}/").status_code == 404
		assert self.client.get("/embed/wgt_nope/").status_code == 404
		assert self.client.post(f"/api/widget/{self.widget.public_key}/session").status_code == 404

	def test_assets(self):
		r = self.client.get("/widget/loader.js")
		assert r.status_code == 200 and r["Content-Type"] == "text/javascript" and r["X-Content-Type-Options"] == "nosniff"
		assert self.client.get("/widget/settings.py").status_code == 404


class VisitorApiTest(WidgetBase):
	def test_first_message_creates_contact_and_conversation(self):
		self.as_visitor(self.token())
		assert not CustomerContact.objects.filter(platform="widget").exists()  # nothing stored for a session alone
		r = self.say("Do you ship to Dhaka?", client_id="c1")
		assert r.status_code == 201, r.content
		contact = CustomerContact.objects.get(platform="widget")
		assert contact.external_id.startswith(f"{self.widget.id}:")
		conv = contact.conversation
		assert conv.knowledge_base_id == self.kb.id and conv.channel == "widget"
		msg = Message.objects.get(conversation=conv)
		assert msg.status == "pending" and msg.direction == "in"
		self.schedule.assert_called_once_with(msg)

	def test_retried_send_is_stored_once(self):
		self.as_visitor(self.token())
		first = self.say("hi", client_id="c1").json()["data"]["message"]
		again = self.say("hi", client_id="c1")
		assert again.status_code == 200 and again.json()["data"]["message"]["id"] == first["id"]
		assert Message.objects.count() == 1 and self.schedule.call_count == 1

	def test_message_validation(self):
		self.as_visitor(self.token())
		assert self.say("   ").status_code == 400
		assert self.say("x" * 2001).status_code == 400
		assert self.say("x" * 2000).status_code == 201

	def test_bad_tokens_get_401(self):
		assert self.say().status_code == 401  # none
		token = self.token()
		self.as_visitor(token[:-2] + "xx")
		assert self.say().status_code == 401  # tampered
		self.as_visitor(token)
		self.widget.is_active = False
		self.widget.save()
		assert self.say().status_code == 401  # widget disabled
		self.widget.is_active = True
		self.widget.public_key = "wgt_rotated"
		self.widget.save()
		assert self.say().status_code == 401  # key rotated

	def test_visitors_only_see_their_own_messages_and_no_internals(self):
		self.as_visitor(self.token())
		self.say("mine")
		conv = CustomerContact.objects.get(platform="widget").conversation
		Message.objects.create(
			project=self.project, contact=conv.contact, conversation=conv, direction="out", sender="bot",
			text="answer", meta={"trace": {"secret": "x"}, "api_calls": [{"url": "https://internal"}]},
		)
		data = self.visitor.get("/api/widget/messages").json()["data"]
		assert [m["text"] for m in data["messages"]] == ["mine", "answer"] and data["pending"] is True
		assert "trace" not in str(data) and "internal" not in str(data)

		self.as_visitor(self.token())  # another visitor of the same widget
		assert self.visitor.get("/api/widget/messages").json()["data"] == {"messages": [], "pending": False}
		other = Widget.objects.create(project=Project.objects.create(name="B", slug="b"), name="B",
			knowledge_base=KnowledgeBase.objects.create(project=Project.objects.get(slug="b"), name="b", vector_collection="vc-b"))
		self.as_visitor(self.token(other))  # a different widget's visitor
		assert self.visitor.get("/api/widget/messages").json()["data"]["messages"] == []

	def test_after_returns_only_newer(self):
		self.as_visitor(self.token())
		first = self.say("one").json()["data"]["message"]
		self.say("two")
		r = self.visitor.get("/api/widget/messages", {"after": first["created_at"]})
		assert [m["text"] for m in r.json()["data"]["messages"]] == ["two"]
		assert self.visitor.get("/api/widget/messages", {"after": "garbage"}).status_code == 200

	@override_settings(WIDGET_THROTTLE_RATES={**settings.WIDGET_THROTTLE_RATES, "visitor_messages": "2/min"})
	def test_message_throttle(self):
		self.as_visitor(self.token())
		assert [self.say(str(i)).status_code for i in range(3)] == [201, 201, 429]

	@override_settings(WIDGET_THROTTLE_RATES={**settings.WIDGET_THROTTLE_RATES, "session": "1/hour"})
	def test_session_throttle(self):
		self.token()
		assert self.visitor.post(f"/api/widget/{self.widget.public_key}/session").status_code == 429


class WidgetReplyTest(WidgetBase):
	def test_reply_is_delivered_by_storing_it(self):
		mock.patch("channels_app.tasks.generate_reply", return_value="We ship everywhere").start()
		mock.patch("channels_app.handoff.emit_event").start()
		self.as_visitor(self.token())
		self.say("Do you ship?")
		msg = Message.objects.get(direction="in")
		process_reply.apply(args=[str(msg.conversation_id), str(msg.id)])
		out = Message.objects.get(direction="out")
		assert out.text == "We ship everywhere" and out.status == "completed"
		data = self.visitor.get("/api/widget/messages").json()["data"]
		assert data["messages"][-1]["text"] == "We ship everywhere" and data["pending"] is False

	def test_api_tool_is_off_for_widget_visitors(self):
		mock.patch("channels_app.tasks.collect_api_domains", return_value=[{"domain": "api.shop.example"}]).start()
		self.as_visitor(self.token())
		self.say("hi")
		widget_conv = CustomerContact.objects.get(platform="widget").conversation
		ui_conv = create_conversation(project=self.project, knowledge_base=self.kb, user_id=str(self.user.id))
		assert build_reply_context(self.project, self.kb, widget_conv)[0].api_domains == []
		assert build_reply_context(self.project, self.kb, ui_conv)[0].api_domains


def png_upload(name="p.png", size=4):
	import io
	from django.core.files.uploadedfile import SimpleUploadedFile
	from PIL import Image
	buf = io.BytesIO()
	Image.new("RGB", (size, size), "red").save(buf, "PNG")
	return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")


@override_settings(IMAGE_SEARCH_ENABLED=True)
class WidgetPhotoTest(WidgetBase):
	def setUp(self):
		super().setUp()
		import tempfile
		tmp = tempfile.TemporaryDirectory()
		self.addCleanup(tmp.cleanup)
		self.media = override_settings(MEDIA_ROOT=tmp.name)
		self.media.enable()
		self.addCleanup(self.media.disable)
		Project.objects.filter(id=self.project.id).update(image_search_allowed=True, image_search_enabled=True)
		Widget.objects.filter(id=self.widget.id).update(image_search_enabled=True)
		self.project.refresh_from_db()
		self.as_visitor(self.token())

	def photo(self, **extra):
		return self.visitor.post("/api/widget/messages/photo", {"image": png_upload(), **extra}, format="multipart")

	def test_session_reports_image_upload(self):
		r = self.visitor.post(f"/api/widget/{self.widget.public_key}/session")
		assert r.json()["data"]["image_upload"] is True
		Widget.objects.filter(id=self.widget.id).update(image_search_enabled=False)
		r = self.visitor.post(f"/api/widget/{self.widget.public_key}/session")
		assert r.json()["data"]["image_upload"] is False

	def test_photo_upload_schedules_reply(self):
		r = self.photo(text="like this?", client_id="c1")
		assert r.status_code == 201, r.content
		assert r.json()["data"]["message"]["has_photo"] is True
		msg = Message.objects.get(id=r.json()["data"]["message"]["id"])
		assert msg.has_attachments and msg.status == "pending" and msg.text == "like this?"
		assert msg.meta["photo_reply"] is True and msg.meta["client_id"] == "c1"
		info = msg.attachment_info[0]
		assert info["type"] == "image" and info["path"].startswith(f"widget_uploads/{self.widget.id}/")
		self.schedule.assert_called_once()
		# a retry with the same client_id returns the same message
		again = self.photo(client_id="c1")
		assert again.status_code == 200 and again.json()["data"]["message"]["id"] == str(msg.id)
		self.schedule.assert_called_once()

	def test_photo_upload_disabled_403(self):
		Widget.objects.filter(id=self.widget.id).update(image_search_enabled=False)
		r = self.photo()
		assert r.status_code == 403 and r.json()["error"]["code"] == "photo_disabled"
		Widget.objects.filter(id=self.widget.id).update(image_search_enabled=True)
		Project.objects.filter(id=self.project.id).update(image_search_enabled=False)
		r = self.photo()
		assert r.status_code == 403 and r.json()["error"]["code"] == "photo_disabled"
		self.schedule.assert_not_called()

	def test_photo_upload_invalid_image_400(self):
		from django.core.files.uploadedfile import SimpleUploadedFile
		bad = SimpleUploadedFile("x.png", b"not an image", content_type="image/png")
		r = self.visitor.post("/api/widget/messages/photo", {"image": bad}, format="multipart")
		assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_image"
		r = self.visitor.post("/api/widget/messages/photo", {}, format="multipart")
		assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_image"
		self.schedule.assert_not_called()

	def test_photo_upload_requires_token(self):
		r = APIClient().post("/api/widget/messages/photo", {"image": png_upload()}, format="multipart")
		assert r.status_code == 401


@override_settings(IMAGE_SEARCH_ENABLED=True)
class EmbedImageUploadTest(WidgetBase):
	def test_embed_config_reports_image_upload(self):
		Project.objects.filter(id=self.project.id).update(image_search_allowed=True)
		Widget.objects.filter(id=self.widget.id).update(image_search_enabled=True)
		assert b'"image_upload": true' in self.visitor.get(f"/embed/{self.widget.public_key}/").content.replace(b'":true', b'": true')

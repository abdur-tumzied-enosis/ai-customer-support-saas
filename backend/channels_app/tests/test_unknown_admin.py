from django.contrib.auth import get_user_model

from channels_app.models import Message
from channels_app.tests.test_reply_pipeline import PipelineBase


class UnknownDeliveryAdminTest(PipelineBase):
	def setUp(self):
		super().setUp()
		self.client.force_login(get_user_model().objects.create_superuser(username="admin", email="admin@test.local", password="pw"))
		self.held = Message.objects.create(
			project=self.project, contact=self.contact, conversation=self.conv, direction="out", sender="bot",
			status="unknown", text="original", meta={"inflight": 0, "parts_total": 1},
		)
		self.other = Message.objects.create(
			project=self.project, contact=self.contact, conversation=self.conv, direction="in", text="a customer message",
		)

	def act(self, action):
		return self.client.post("/admin/channels_app/message/", {"action": action, "_selected_action": [str(self.held.id)]})

	def test_lists_only_replies_held_as_unknown(self):
		page = self.client.get("/admin/channels_app/message/").content.decode()
		assert "original" in page and "a customer message" not in page

	def test_messages_cannot_be_edited(self):
		resp = self.client.post(f"/admin/channels_app/message/{self.held.id}/change/", {"text": "hacked", "status": "completed"})
		assert resp.status_code != 302  # not the redirect that follows a save (the middleware turns the refusal into a 500)
		self.held.refresh_from_db()
		assert self.held.text == "original" and self.held.status == "unknown"

	def test_mark_delivered_action_still_works(self):
		self.act("mark_delivered")
		self.held.refresh_from_db()
		assert self.held.status == "completed"

	def test_send_again_action_still_works(self):
		self.act("resend")
		self.held.refresh_from_db()
		assert self.held.status == "pending" and "inflight" not in self.held.meta

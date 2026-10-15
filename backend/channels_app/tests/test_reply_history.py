from unittest import mock

from django.test import TestCase

from accounts.models import Project
from channels_app.models import CustomerContact, Message
from channels_app.tasks import HISTORY_MAX_MESSAGES, build_history


class BuildHistoryTest(TestCase):
	def setUp(self):
		self.project = Project.objects.create(name="p")
		self.contact = CustomerContact.objects.create(project=self.project, external_id="psid")

	def msg(self, text, direction="in"):
		return Message.objects.create(project=self.project, contact=self.contact, direction=direction, text=text)

	def test_history_is_recent_chronological_and_excludes_current(self):
		for i in range(HISTORY_MAX_MESSAGES + 5):
			self.msg(f"m{i}", "in" if i % 2 == 0 else "out")
		current = self.msg("current")
		self.msg("newer")

		history = build_history(current)

		assert [h["content"] for h in history] == [f"m{i}" for i in range(5, HISTORY_MAX_MESSAGES + 5)]
		assert history[0]["role"] == "assistant" and history[-1]["role"] == "user"

	@mock.patch("channels_app.tasks.HISTORY_TOKEN_BUDGET", 600)
	def test_history_stops_at_token_budget(self):
		for i in range(10):
			self.msg(f"{i}" + "x" * 399)  # ~100 tokens each
		history = build_history(self.msg("current"))
		assert [h["content"][0] for h in history] == ["4", "5", "6", "7", "8", "9"]

	@mock.patch("channels_app.tasks.HISTORY_TOKEN_BUDGET", 10)
	def test_newest_message_kept_even_if_over_budget(self):
		self.msg("old")
		self.msg("y" * 1000)
		history = build_history(self.msg("current"))
		assert [h["content"] for h in history] == ["y" * 1000]

	def test_history_since_drops_summarized_messages(self):
		old = self.msg("summarized already")
		self.msg("after episode")
		history = build_history(self.msg("current"), since=old.created_at)
		assert [h["content"] for h in history] == ["after episode"]

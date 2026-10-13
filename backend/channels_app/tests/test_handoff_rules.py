from datetime import timedelta

from django.test import SimpleTestCase, override_settings
from django.utils import timezone

from channels_app.handoff import wants_human
from channels_app.models import Conversation


class WantsHumanTest(SimpleTestCase):
	def test_hits(self):
		for text in (
			"can I talk to a person",
			"HUMAN please",
			"I want customer service",
			"let me speak to someone",
			"is there a real person here?",
			"talk to an agent",
		):
			assert wants_human(text), text

	def test_misses(self):
		for text in ("my travel agent said it was fine", "what are your hours", "", None):
			assert not wants_human(text), text


@override_settings(HANDOFF_AUTO_RESUME_HOURS=24)
class IsPausedTest(SimpleTestCase):
	def test_not_paused_without_handoff(self):
		assert not Conversation().is_paused()

	def test_paused_within_window_and_expires(self):
		now = timezone.now()
		conv = Conversation(handoff_at=now - timedelta(hours=23))
		assert conv.is_paused(now)
		assert conv.resumes_at() == conv.handoff_at + timedelta(hours=24)
		conv.handoff_at = now - timedelta(hours=25)
		assert not conv.is_paused(now)

	def test_channel_defaults_to_self_without_contact(self):
		assert Conversation().channel == "self"

from django.test import TestCase

from accounts.models import Project
from channels_app.models import CustomerContact, Message
from channels_app.tasks import HISTORY_LIMIT, build_history


class BuildHistoryTest(TestCase):
	def test_history_is_recent_chronological_and_excludes_current(self):
		project = Project.objects.create(name="p")
		contact = CustomerContact.objects.create(project=project, external_id="psid")
		for i in range(HISTORY_LIMIT + 5):
			Message.objects.create(
				project=project, contact=contact, direction="in" if i % 2 == 0 else "out", text=f"m{i}"
			)
		current = Message.objects.create(project=project, contact=contact, direction="in", text="current")
		Message.objects.create(project=project, contact=contact, direction="in", text="newer")

		history = build_history(current)

		assert [h["content"] for h in history] == [f"m{i}" for i in range(5, HISTORY_LIMIT + 5)]
		assert history[0]["role"] == "assistant" and history[-1]["role"] == "user"

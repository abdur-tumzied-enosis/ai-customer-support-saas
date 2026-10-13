from unittest import mock

from django.test import TestCase

from accounts.models import Project
from channels_app.models import CustomerContact, Message
from channels_app.tasks import FALLBACK_REPLY, handle_incoming_message
from knowledge.models import KnowledgeBase


class RetryAfterPostTest(TestCase):
	def test_failed_reply_after_post_is_not_retried_and_calls_are_logged(self):
		project = Project.objects.create(name="p", slug="p-retry")
		kb = KnowledgeBase.objects.create(project=project, name="kb", vector_collection="vc-retry")
		contact = CustomerContact.objects.create(project=project, external_id="psid")
		msg = Message.objects.create(project=project, contact=contact, direction="in", text="book it")
		calls = [{"method": "POST", "url": "https://shop.com/book", "status": 201, "ms": 5}]

		def post_then_fail(question, context, *args, **kwargs):
			context.api_call_log.extend(calls)
			raise RuntimeError("llm down")

		with mock.patch("channels_app.tasks.generate_reply", side_effect=post_then_fail) as gen, \
				mock.patch("channels_app.tasks.get_facebook_credential", return_value=None):
			handle_incoming_message.apply(args=[str(msg.id), str(kb.id)])

		assert gen.call_count == 1  # a retry would re-send the POST
		out = Message.objects.get(contact=contact, direction="out")
		assert out.text == FALLBACK_REPLY
		assert out.meta["api_calls"] == calls

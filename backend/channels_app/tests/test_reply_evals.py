import json
import os
import tempfile
from io import StringIO
from unittest import mock

from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, TestCase

from accounts.models import Project
from channels_app.management.commands.run_reply_evals import score_case
from knowledge.models import KnowledgeBase


class ScoreCaseTest(SimpleTestCase):
	def test_reports_each_failed_expectation(self):
		case = {"question": "q", "must_include": ["30 days", "receipt"], "must_not_include": ["guarantee"], "expect_handoff": False}
		assert score_case(case, "Refunds within 30 DAYS with a Receipt.", None) == []
		assert score_case(case, "We guarantee it", "angry") == [
			"missing '30 days'", "missing 'receipt'", "contains 'guarantee'", "expected no handoff, got 'angry'",
		]
		assert score_case({"question": "q", "expect_handoff": True}, "ok", None) == ["expected a handoff"]


class RunReplyEvalsTest(TestCase):
	def test_runs_cases_against_the_agent_and_fails_on_any_miss(self):
		project = Project.objects.create(name="P", slug="p-eval")
		KnowledgeBase.objects.create(project=project, name="default", vector_collection="vc-eval")
		cases = {"project": "p-eval", "cases": [
			{"question": "refund window?", "must_include": ["30 days"]},
			{"question": "hours?", "must_include": ["9-5"]},
		]}
		with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
			json.dump(cases, f)
		self.addCleanup(os.remove, f.name)
		out = StringIO()
		with mock.patch("channels_app.management.commands.run_reply_evals.generate_reply", return_value="Within 30 days."):
			with self.assertRaises(CommandError):
				call_command("run_reply_evals", f.name, stdout=out)
		text = out.getvalue()
		assert "PASS  refund window?" in text and "FAIL  hours?" in text and "missing '9-5'" in text and "1/2 passed" in text

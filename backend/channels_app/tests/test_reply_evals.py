import json
import os
import shutil
import tempfile
from io import StringIO
from unittest import mock

from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, TestCase, override_settings

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


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "eval-tests"}})
class VisionEvalTest(TestCase):
	def setUp(self):
		self.project = Project.objects.create(name="P", slug="p-eval-img")
		KnowledgeBase.objects.create(project=self.project, name="default", vector_collection="vc-eval-img")
		self.dir = tempfile.mkdtemp()
		self.addCleanup(shutil.rmtree, self.dir)
		os.makedirs(os.path.join(self.dir, "photos"))
		for name in ("a.png", "b.png"):
			with open(os.path.join(self.dir, "photos", name), "wb") as f:
				f.write(b"\x89PNG fake bytes")

	def run_cases(self, cases, route=("google", "gemini-3.8-flash")):
		path = os.path.join(self.dir, "cases.json")
		with open(path, "w", encoding="utf-8") as f:
			json.dump({"project": "p-eval-img", "cases": cases}, f)
		out = StringIO()
		seen = []

		def fake_generate(question, context, deps=None, history=None, project_id=None):
			seen.append((question, list(context.vision_images)))
			return "ok"

		with mock.patch("channels_app.management.commands.run_reply_evals.generate_reply", side_effect=fake_generate), \
				mock.patch("channels_app.management.commands.run_reply_evals.encode_vision_image", side_effect=lambda data: {"uri": "data:image/jpeg;base64,AAA"}), \
				mock.patch("channels_app.management.commands.run_reply_evals.vision_route", return_value=route):
			try:
				call_command("run_reply_evals", path, stdout=out)
			except CommandError:
				pass
		return out.getvalue(), seen

	def test_image_case_loads_a_data_uri(self):
		_, seen = self.run_cases([{"question": "what is this?", "image_paths": ["photos/a.png"]}])
		assert seen[0][1] == ["data:image/jpeg;base64,AAA"]

	def test_image_case_sends_production_photo_notes(self):
		_, seen = self.run_cases([{"question": "is it broken?", "category": "damage", "image_paths": ["photos/a.png", "photos/b.png"]}])
		question = seen[0][0]
		assert "[Customer sent photo 1." in question and "[Customer sent photo 2." in question and "Photo attached" in question
		assert "suggests damage or a problem" in question

	def test_missing_image_file_fails_the_case(self):
		out, _ = self.run_cases([{"question": "x", "image_paths": ["missing.jpg"]}])
		assert "error FileNotFoundError" in out

	def test_text_cases_still_have_empty_images(self):
		_, seen = self.run_cases([{"question": "hours?"}])
		assert seen == [("hours?", [])]

	def test_image_case_warns_on_text_only_model(self):
		out, _ = self.run_cases([{"question": "x", "image_paths": ["photos/a.png"]}], route=None)
		assert "vision-capable" in out

	def test_unattached_photo_case_renders_not_attached_note(self):
		_, seen = self.run_cases([{"question": "x", "image_paths": ["photos/a.png"], "unattached_photos": 1}])
		question, images = seen[0]
		assert "Photo not attached" in question and len(images) == 1

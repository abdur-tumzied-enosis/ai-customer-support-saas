"""
Run a fixed set of customer questions through the reply agent and check the answers (agent memory M6 / backlog O2).
Use it before and after changing prompts, retrieval or instructions:

    python manage.py run_reply_evals docs/evals/example.json

File format (JSON):
    {"project": "<project slug>", "knowledge_base": "default",
     "cases": [{"question": "...", "must_include": ["30 days"], "must_not_include": ["guarantee"], "expect_handoff": false}]}

Makes real LLM calls (tokens are not billed to the project). Exits non-zero if any case fails.
"""
import json

from django.core.management.base import BaseCommand, CommandError

from accounts.models import Project
from agents.rag import generate_reply
from channels_app.tasks import build_reply_context
from knowledge.models import KnowledgeBase


def score_case(case: dict, reply: str, handoff_reason) -> list[str]:
	"""Failed expectations for one case ([] = pass). Text checks are case-insensitive substrings."""
	text = reply.lower()
	failures = [f"missing {s!r}" for s in case.get("must_include", []) if s.lower() not in text]
	failures += [f"contains {s!r}" for s in case.get("must_not_include", []) if s.lower() in text]
	expect = case.get("expect_handoff")
	if expect is True and not handoff_reason:
		failures.append("expected a handoff")
	elif expect is False and handoff_reason:
		failures.append(f"expected no handoff, got {handoff_reason!r}")
	return failures


class Command(BaseCommand):
	help = "Run reply eval cases from a JSON file against the live agent"

	def add_arguments(self, parser):
		parser.add_argument("path")

	def handle(self, path, **options):
		with open(path, encoding="utf-8") as f:
			spec = json.load(f)
		project = Project.objects.filter(slug=spec["project"]).first()
		if not project:
			raise CommandError(f"Project {spec['project']!r} not found")
		kb = KnowledgeBase.objects.filter(project=project, name=spec.get("knowledge_base", "default")).first()
		if not kb:
			raise CommandError(f"Knowledge base {spec.get('knowledge_base', 'default')!r} not found")

		passed = 0
		cases = spec["cases"]
		for case in cases:
			context, deps = build_reply_context(project, kb)
			try:
				reply = generate_reply(case["question"], context, deps, history=[])
				failures = score_case(case, reply, context.handoff_reason)
			except Exception as exc:
				reply, failures = "", [f"error {type(exc).__name__}: {exc}"]
			t = context.trace
			passed += not failures
			self.stdout.write(
				f"{'FAIL' if failures else 'PASS'}  {case['question']}  "
				f"({t.get('ms', '?')}ms, {t.get('tokens_in', '?')}+{t.get('tokens_out', '?')} tokens)"
			)
			for reason in failures:
				self.stdout.write(f"      - {reason}")
			if failures and reply:
				self.stdout.write(f"      reply: {reply[:300]}")

		self.stdout.write(f"\n{passed}/{len(cases)} passed")
		if passed < len(cases):
			raise CommandError(f"{len(cases) - passed} case(s) failed")

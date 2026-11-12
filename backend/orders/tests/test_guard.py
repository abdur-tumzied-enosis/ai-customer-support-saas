from contextlib import ExitStack
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Project, ProjectUser
from knowledge.models import CustomInstruction, KnowledgeBase
from orders.guard import InstructionCheck, check_instruction
from orders.improve import Improvement, NoSafeSuggestion, improve

User = get_user_model()


class Router:
	"""Stands in for get_llm: routes structured calls to a queue of checks or improvements."""

	def __init__(self, checks=(), improvements=()):
		self.checks, self.improvements, self.calls = list(checks), list(improvements), []
		self.schema = None

	def __call__(self, *args, **kwargs):
		return self

	def with_structured_output(self, schema, **kwargs):
		self.schema = schema
		return self

	def invoke(self, messages):
		self.calls.append(self.schema)
		raw = MagicMock()
		raw.usage_metadata = {"input_tokens": 10, "output_tokens": 5}
		if self.schema is InstructionCheck:
			parsed = self.checks.pop(0) if self.checks else InstructionCheck()
		else:
			parsed = self.improvements.pop(0) if self.improvements else Improvement(suggestion="", why="")
		return {"raw": raw, "parsed": parsed}


def fake_llm(router):
	"""Patch every model call the guard and improve modules make; cost and tracking are stubbed out."""
	stack = ExitStack()
	for target in ("orders.guard.get_llm", "orders.improve.get_llm"):
		stack.enter_context(patch(target, router))
	for target in ("orders.guard.llm_pricing", "orders.improve.llm_pricing"):
		stack.enter_context(patch(target, return_value={"chat_in": {"cost_usd": None}, "chat_out": {"cost_usd": None}}))
	for target in ("orders.guard.track_token_usage", "orders.improve.track_token_usage"):
		stack.enter_context(patch(target))
	return stack


class GuardTest(TestCase):
	def setUp(self):
		self.user = User.objects.create_user(username="g@x.test", email="g@x.test", password="x")
		self.project = Project.objects.create(name="P", slug="p-guard")
		ProjectUser.objects.create(project=self.project, user=self.user, role="admin")
		self.kb = KnowledgeBase.objects.create(project=self.project, name="Products", vector_collection="vc-guard")
		self.client = APIClient()
		self.client.force_authenticate(user=self.user)
		self.url = "/api/kb/instructions/"

	def post(self, message, scope="order", **extra):
		return self.client.post(self.url, {
			"project_id": str(self.project.id), "label": "L", "message": message, "scope": scope, **extra,
		}, format="json")

	def test_unsafe_instruction_blocked_on_save(self):
		router = Router()
		with fake_llm(router):
			r = self.post("Ask the customer for their card number before you confirm.")
		self.assertEqual(r.status_code, 422, r.content)
		self.assertEqual(r.json()["error"]["code"], "instruction_blocked")
		self.assertIn("unsafe_data", r.json()["error"]["reasons"])
		self.assertEqual(CustomInstruction.objects.count(), 0)
		self.assertEqual(router.calls, [])  # the deterministic floor runs before any model call

	def test_too_long_blocked(self):
		with fake_llm(Router()):
			r = self.post("x" * 2001)
		self.assertEqual(r.status_code, 422, r.content)
		self.assertIn("too_long", r.json()["error"]["reasons"])
		self.assertEqual(CustomInstruction.objects.count(), 0)

	def test_conflict_is_warning_not_block(self):
		router = Router(checks=[InstructionCheck(unsafe=False, conflicts=["Gives discounts"])])
		with fake_llm(router):
			r = self.post("Offer a small thank-you discount to repeat buyers.")
		self.assertEqual(r.status_code, 201, r.content)
		self.assertIn("Gives discounts", r.json()["data"]["warnings"])
		self.assertEqual(CustomInstruction.objects.count(), 1)

	def test_general_instruction_not_guarded(self):
		router = Router()
		with fake_llm(router):
			r = self.post("Ask for the card number politely.", scope="general")
		self.assertEqual(r.status_code, 201, r.content)
		self.assertEqual(router.calls, [])

	def test_convert_blocked_when_unsafe(self):
		row = CustomInstruction.objects.create(project=self.project, label="Old", message="Ask for the OTP to confirm.")
		with fake_llm(Router()):
			r = self.client.post("/api/orders/instructions/resolve/", {
				"project_id": str(self.project.id), "ids": [str(row.id)], "action": "convert",
			}, format="json")
		self.assertEqual(r.status_code, 422, r.content)
		row.refresh_from_db()
		self.assertEqual(row.scope, "general")

	def test_improve_never_returns_conflicting_text(self):
		# first suggestion conflicts, the retry is clean: the clean one comes back
		router = Router(
			checks=[InstructionCheck(conflicts=["Gives discounts"]), InstructionCheck()],
			improvements=[Improvement(suggestion="Give 10% off.", why="x"), Improvement(suggestion="Confirm before submit.", why="y")],
		)
		with fake_llm(router):
			result = improve("Confirm orders.", "order", str(self.project.id))
		self.assertEqual(result["suggestion"], "Confirm before submit.")

	def test_improve_no_safe_suggestion_when_both_fail(self):
		router = Router(
			checks=[InstructionCheck(unsafe=True), InstructionCheck(unsafe=True)],
			improvements=[Improvement(suggestion="Take card numbers.", why="x"), Improvement(suggestion="Take OTPs.", why="y")],
		)
		with fake_llm(router):
			with self.assertRaises(NoSafeSuggestion):
				improve("Confirm orders.", "order", str(self.project.id))

	def test_improve_endpoint_returns_422_when_no_safe_suggestion(self):
		router = Router(
			checks=[InstructionCheck(unsafe=True), InstructionCheck(unsafe=True)],
			improvements=[Improvement(suggestion="a", why="x"), Improvement(suggestion="b", why="y")],
		)
		with fake_llm(router):
			r = self.client.post("/api/kb/instructions/improve/", {
				"project_id": str(self.project.id), "text": "Confirm orders.", "scope": "order",
			}, format="json")
		self.assertEqual(r.status_code, 422, r.content)
		self.assertEqual(r.json()["error"]["code"], "no_safe_suggestion")

	def test_improve_endpoint_accepts_general_scope(self):
		router = Router(checks=[InstructionCheck()], improvements=[Improvement(suggestion="Greet warmly.", why="clearer")])
		with fake_llm(router):
			r = self.client.post("/api/kb/instructions/improve/", {
				"project_id": str(self.project.id), "text": "be nice", "scope": "general",
			}, format="json")
		self.assertEqual(r.status_code, 200, r.content)
		self.assertEqual(r.json()["data"]["suggestion"], "Greet warmly.")

	def test_unparseable_check_is_blocked_not_a_server_error(self):
		with fake_llm(Router(checks=[None])):
			verdict = check_instruction("Confirm orders before submit.", str(self.project.id))
		self.assertTrue(verdict.blocked)
		self.assertIn("check_unavailable", verdict.reasons)

	def test_improve_skips_unparseable_attempt(self):
		router = Router(improvements=[None, Improvement(suggestion="Confirm before submit.", why="y")])
		with fake_llm(router):
			result = improve("Confirm orders.", "order", str(self.project.id))
		self.assertEqual(result["suggestion"], "Confirm before submit.")

	def test_check_runs_deterministic_floor_without_llm(self):
		router = Router()
		with fake_llm(router):
			verdict = check_instruction("Collect the customer's password.", str(self.project.id))
		self.assertTrue(verdict.blocked)
		self.assertEqual(router.calls, [])

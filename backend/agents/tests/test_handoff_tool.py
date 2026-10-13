from django.test import SimpleTestCase

from agents.llm import build_system_prompt
from agents.rag import Context, ToolDeps, _build_tools_from_context


class HandoffToolTest(SimpleTestCase):
	def test_tool_always_present_and_records_reason(self):
		ctx = Context()
		tools = {t.__name__: t for t in _build_tools_from_context(ctx, ToolDeps())}
		assert "escalate_to_human" in tools
		assert ctx.handoff_reason is None
		out = tools["escalate_to_human"]("customer wants a refund")
		assert ctx.handoff_reason == "customer wants a refund"
		assert "person will follow up" in out

	def test_prompt_mentions_handoff(self):
		prompt = build_system_prompt()
		assert "escalate_to_human" in prompt
		assert "refund" in prompt

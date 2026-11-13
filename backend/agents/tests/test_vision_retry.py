from unittest import mock

from django.test import SimpleTestCase
from langchain_core.messages import AIMessage, HumanMessage

from agents.rag import Context, generate_reply, vision_retry_kind, _blocked_result

IMAGE = "data:image/png;base64,AAA"


class ApiError(Exception):
	"""Stands in for a provider SDK error: some carry status_code, some do not."""
	def __init__(self, message, status_code=None):
		super().__init__(message)
		if status_code is not None:
			self.status_code = status_code


class NotFoundResponseError(Exception):
	"""Shape recorded by the Task 6 probe (2026-11-13): OpenRouter, text-only model given an image."""
	status_code = 404


def fake_agent(*outcomes):
	agent = mock.Mock()
	agent.invoke.side_effect = list(outcomes)
	return agent


def ok(text="I can see it.", **usage):
	return {"messages": [AIMessage(text, usage_metadata=usage or None)]}


class ClassifyTest(SimpleTestCase):
	def test_image_validation_error_is_retried(self):
		assert vision_retry_kind(ApiError("unsupported image content type", 400)) == "image_error"

	def test_safety_refusal_is_retried(self):
		assert vision_retry_kind(ApiError("blocked by safety policy")) == "refusal"

	def test_safety_wins_over_image_words(self):
		assert vision_retry_kind(ApiError("image blocked by safety policy", 400)) == "refusal"

	def test_rate_limit_is_not_retried(self):
		assert vision_retry_kind(ApiError("image too large", 429)) == ""

	def test_server_error_is_not_retried_even_with_safety_words(self):
		assert vision_retry_kind(ApiError("safety service unavailable", 500)) == ""

	def test_plain_bad_request_is_not_retried(self):
		assert vision_retry_kind(ApiError("bad request", 400)) == ""

	def test_recorded_openrouter_shape_is_an_image_error(self):
		assert vision_retry_kind(NotFoundResponseError("No endpoints found that support image input")) == "image_error"

	def test_404_without_the_marker_is_not_retried(self):
		assert vision_retry_kind(ApiError("model not found", 404)) == ""

	def test_blocked_finish_reason_counts_as_refusal_result(self):
		msg = AIMessage("", response_metadata={"finish_reason": "SAFETY"})
		assert _blocked_result({"messages": [msg]}) is True

	def test_empty_final_message_counts_as_blocked(self):
		assert _blocked_result({"messages": [AIMessage("")]}) is True

	def test_normal_answer_is_not_blocked(self):
		assert _blocked_result({"messages": [AIMessage("Here it is.")]}) is False


class VisionRetryTest(SimpleTestCase):
	def run_turn(self, ctx, *first_outcomes, retry_outcomes=None, question="what is this?"):
		first = fake_agent(*first_outcomes)
		second = fake_agent(*(retry_outcomes or []))
		with mock.patch("agents.rag.create_basic_agent", side_effect=[first, second]) as make:
			try:
				reply = generate_reply(question, ctx)
			except Exception as exc:
				return ("raised", exc, first, second, make)
		return ("ok", reply, first, second, make)

	def test_image_validation_error_retries_once_text_only(self):
		ctx = Context(vision_images=[IMAGE])
		outcome, reply, first, second, _ = self.run_turn(
			ctx, ApiError("unsupported image content type", 400), retry_outcomes=[ok("Text answer.")],
		)
		assert (outcome, reply) == ("ok", "Text answer.")
		assert first.invoke.call_count == 1 and second.invoke.call_count == 1
		retry_content = second.invoke.call_args.args[0]["messages"][-1]["content"]
		assert retry_content == "what is this? [The photo could not be viewed.]"
		assert ctx.trace["vision_retry"] == "image_error" and ctx.trace["vision_error"] == "ApiError"

	def test_safety_refusal_retries_with_marker(self):
		ctx = Context(vision_images=[IMAGE])
		outcome, reply, first, second, _ = self.run_turn(
			ctx, ApiError("blocked by safety policy"), retry_outcomes=[ok("Text answer.")],
		)
		assert (outcome, reply) == ("ok", "Text answer.")
		assert second.invoke.call_args.args[0]["messages"][-1]["content"].endswith("[The photo could not be viewed.]")
		assert ctx.trace["vision_retry"] == "refusal"

	def test_recorded_openrouter_404_retries_text_only(self):
		ctx = Context(vision_images=[IMAGE])
		outcome, reply, first, second, _ = self.run_turn(
			ctx, NotFoundResponseError("No endpoints found that support image input"), retry_outcomes=[ok("Text answer.")],
		)
		assert (outcome, reply) == ("ok", "Text answer.")
		assert ctx.trace["vision_retry"] == "image_error" and ctx.trace["vision_error"] == "NotFoundResponseError"

	def test_rate_limit_not_retried(self):
		ctx = Context(vision_images=[IMAGE])
		outcome, exc, first, second, make = self.run_turn(ctx, ApiError("slow down", 429))
		assert outcome == "raised" and isinstance(exc, ApiError)
		assert first.invoke.call_count == 1 and second.invoke.call_count == 0
		assert "vision_retry" not in ctx.trace

	def test_server_error_not_retried(self):
		ctx = Context(vision_images=[IMAGE])
		outcome, exc, first, second, _ = self.run_turn(ctx, ApiError("upstream broke", 500))
		assert outcome == "raised" and first.invoke.call_count == 1 and second.invoke.call_count == 0

	def test_plain_400_not_retried(self):
		ctx = Context(vision_images=[IMAGE])
		outcome, exc, first, second, _ = self.run_turn(ctx, ApiError("bad request", 400))
		assert outcome == "raised" and first.invoke.call_count == 1 and second.invoke.call_count == 0

	def test_no_vision_images_not_retried(self):
		ctx = Context()
		outcome, exc, first, second, _ = self.run_turn(ctx, ApiError("unsupported image content type", 400))
		assert outcome == "raised" and first.invoke.call_count == 1 and second.invoke.call_count == 0
		assert "vision_retry" not in ctx.trace

	def test_retry_failure_propagates_with_trace(self):
		ctx = Context(vision_images=[IMAGE])
		outcome, exc, first, second, _ = self.run_turn(
			ctx, ApiError("unsupported image content type", 400), retry_outcomes=[ApiError("retry broke", 500)],
		)
		assert outcome == "raised" and str(exc) == "retry broke"
		assert ctx.trace["vision_retry"] == "image_error" and ctx.trace["vision_error"] == "ApiError"

	def test_retry_result_is_traced(self):
		ctx = Context(vision_images=[IMAGE])
		outcome, reply, *_ = self.run_turn(
			ctx, ApiError("unsupported image content type", 400),
			# a real agent result echoes the input messages first; _record_trace slices them off
			retry_outcomes=[{"messages": [HumanMessage("input echo"), AIMessage("Text answer.", usage_metadata={"input_tokens": 120, "output_tokens": 7, "total_tokens": 127})]}],
		)
		assert reply == "Text answer."
		assert (ctx.trace["tokens_in"], ctx.trace["tokens_out"]) == (120, 7)

	def test_blocked_result_retries_once_text_only(self):
		ctx = Context(vision_images=[IMAGE])
		blocked = {"messages": [AIMessage("", response_metadata={"finish_reason": "SAFETY"})]}
		outcome, reply, first, second, _ = self.run_turn(ctx, blocked, retry_outcomes=[ok("Text answer.")])
		assert (outcome, reply) == ("ok", "Text answer.")
		assert first.invoke.call_count == 1 and second.invoke.call_count == 1
		assert ctx.trace["vision_retry"] == "refusal"

	def test_blocked_result_without_images_not_retried(self):
		ctx = Context()
		blocked = {"messages": [AIMessage("", response_metadata={"finish_reason": "SAFETY"})]}
		outcome, reply, first, second, _ = self.run_turn(ctx, blocked)
		assert first.invoke.call_count == 1 and second.invoke.call_count == 0
		assert "vision_retry" not in ctx.trace

	def test_retry_keeps_business_instructions(self):
		ctx = Context(vision_images=[IMAGE], custom_instructions=[{"label": "Rules", "message": "Be brief."}])
		_, _, _, _, make = self.run_turn(ctx, ApiError("unsupported image content type", 400), retry_outcomes=[ok("Text answer.")])
		retry_call = make.call_args_list[1]
		assert retry_call.kwargs["custom_instructions"] == [{"label": "Rules", "message": "Be brief."}]
		assert retry_call.args == (mock.ANY,)  # the retry uses the turn's tools, not a photo route
		assert "route" not in retry_call.kwargs

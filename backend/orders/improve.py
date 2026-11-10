from pydantic import BaseModel, Field

from accounts.models import Project
from agents.llm import get_llm
from usage.utils import llm_pricing, track_token_usage

from .guard import check_instruction
from .models import OrderSettings
from .prompt import build_order_prompt

IMPROVE_MAX_TOKENS = 1024
IMPROVE_SYSTEM = (
	"You rewrite one instruction a business gives its assistant so it is clear and short, keeping the owner's intent. "
	"Never add discounts, skipped confirmations, products without prices, or price promises. "
	"Never ask customers for card numbers, passwords or one-time codes. "
	"Return the rewrite as suggestion, and one short sentence as why."
)


class Improvement(BaseModel):
	suggestion: str = Field(max_length=2000)
	why: str = Field(default="", max_length=500)


class NoSafeSuggestion(Exception):
	pass


def _rewrite(text: str, scope: str, project_id: str) -> Improvement:
	context = ""
	if scope == "order":
		settings = OrderSettings.objects.filter(project_id=project_id).first() or OrderSettings()
		currency = Project.objects.filter(pk=project_id).values_list("currency", flat=True).first() or "BDT"
		context = "\n\nThe order assistant's built-in rules, for context:\n" + build_order_prompt(settings, [], currency)
	llm = get_llm(role="memory", max_tokens=IMPROVE_MAX_TOKENS)
	out = llm.with_structured_output(Improvement, include_raw=True, method="function_calling").invoke(
		[("system", IMPROVE_SYSTEM), ("human", text + context)]
	)
	usage = getattr(out.get("raw"), "usage_metadata", None) or {}
	pricing = llm_pricing([out.get("raw")])
	track_token_usage(project_id, "chat_in", usage.get("input_tokens", 0), {"source": "instruction_improve"}, pricing=pricing["chat_in"])
	track_token_usage(project_id, "chat_out", usage.get("output_tokens", 0), {"source": "instruction_improve"}, pricing=pricing["chat_out"])
	if out.get("parsed") is None:
		raise ValueError(f"improve returned no suggestion: {out.get('parsing_error')}")
	return out["parsed"]


def improve(text: str, scope: str, project_id: str) -> dict:
	"""A safer rewrite of the instruction, checked by the same guard as a save. One retry; nothing is applied."""
	for _ in range(2):
		try:
			candidate = _rewrite(text, scope, project_id)
		except ValueError:  # an unreadable answer counts as one failed attempt
			continue
		verdict = check_instruction(candidate.suggestion, project_id)
		if not verdict.blocked and not verdict.warnings:
			return {"suggestion": candidate.suggestion, "why": candidate.why}
	raise NoSafeSuggestion()

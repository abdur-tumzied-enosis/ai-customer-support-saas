import re
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from agents.llm import get_llm
from usage.utils import llm_costs, track_token_usage

MAX_INSTRUCTION_CHARS = 2000
CHECK_MAX_TOKENS = 512
ENGLISH_CLAIMS = ("order placed", "order confirmed", "order number")
BENGALI_ORDER = "অর্ডার"
BENGALI_DONE = ("হয়েছে", "নিশ্চিত")

# Deterministic floor: asking the customer for card, password or OTP details. Runs before any model call.
_ASK = r"(ask|collect|request|send|give|share|provide|enter|type|tell|take|get|need)"
_SENSITIVE = r"(card|credit|debit|cvv|cvc|password|passcode|pin|otp|one[- ]time)"
_UNSAFE_EN = re.compile(rf"\b{_ASK}\b[^.\n]{{0,60}}\b{_SENSITIVE}", re.IGNORECASE)
_UNSAFE_BN = re.compile(r"(কার্ড|পাসওয়ার্ড|ওটিপি|সিভিভি)[^।.\n]{0,40}(চান|জিজ্ঞাস|দিতে|লিখ|পাঠা)")

CHECK_SYSTEM = (
	"You check one instruction a business gave its order-taking assistant. "
	"unsafe is true only if the instruction asks the assistant to collect card numbers, passwords or one-time codes. "
	"conflicts lists each part that would give discounts, skip order confirmation, take products without a price, "
	"or promise prices. Return an empty list when there is no such part."
)


class InstructionCheck(BaseModel):
	unsafe: bool = False
	conflicts: list[str] = Field(default_factory=list, max_length=5)


@dataclass
class Verdict:
	blocked: bool
	reasons: list[str] = field(default_factory=list)
	warnings: list[str] = field(default_factory=list)


def unconfirmed_claim(reply: str, submitted_this_turn: bool) -> bool:
	"""True when the reply says an order is confirmed but nothing was submitted this turn."""
	if submitted_this_turn:
		return False
	lower = reply.lower()
	if any(claim in lower for claim in ENGLISH_CLAIMS):
		return True
	return BENGALI_ORDER in reply and any(word in reply for word in BENGALI_DONE)


def trace_unconfirmed(context, reply: str) -> None:
	if unconfirmed_claim(reply, context.order_submitted):
		context.trace.setdefault("flags", []).append("hallucinated_confirmation")


def _llm_check(text: str, project_id: str) -> InstructionCheck:
	llm = get_llm(role="memory", max_tokens=CHECK_MAX_TOKENS)
	out = llm.with_structured_output(InstructionCheck, include_raw=True, method="function_calling").invoke(
		[("system", CHECK_SYSTEM), ("human", text)]
	)
	usage = getattr(out.get("raw"), "usage_metadata", None) or {}
	cost_in, cost_out = llm_costs([out.get("raw")])
	track_token_usage(project_id, "chat_in", usage.get("input_tokens", 0), {"source": "instruction_check"}, cost_usd=cost_in)
	track_token_usage(project_id, "chat_out", usage.get("output_tokens", 0), {"source": "instruction_check"}, cost_usd=cost_out)
	if out.get("parsed") is None:
		raise ValueError(f"instruction check returned no result: {out.get('parsing_error')}")
	return out["parsed"]


def check_instruction(text: str, project_id: str) -> Verdict:
	"""Guard for an order instruction: length and unsafe-data floor first, then a model check for conflicts."""
	if len(text) > MAX_INSTRUCTION_CHARS:
		return Verdict(blocked=True, reasons=["too_long"])
	if _UNSAFE_EN.search(text) or _UNSAFE_BN.search(text):
		return Verdict(blocked=True, reasons=["unsafe_data"])
	result = _llm_check(text, project_id)
	return Verdict(
		blocked=result.unsafe,
		reasons=["unsafe_content"] if result.unsafe else [],
		warnings=list(result.conflicts),
	)

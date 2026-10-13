"""Human handoff: keyword rule and pause/resume actions."""
import re

HANDOFF_PATTERN = re.compile(
	r"\b(human|real person|live (agent|person)|customer (service|support)|operator"
	r"|(talk|speak) to (a |an )?(someone|somebody|person|agent|representative|manager))\b",
	re.IGNORECASE,
)


def wants_human(text) -> bool:
	"""English-only rule; the agent's escalate_to_human tool covers everything else."""
	return bool(HANDOFF_PATTERN.search(text or ""))

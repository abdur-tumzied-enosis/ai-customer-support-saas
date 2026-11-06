ENGLISH_CLAIMS = ("order placed", "order confirmed", "order number")
BENGALI_ORDER = "অর্ডার"
BENGALI_DONE = ("হয়েছে", "নিশ্চিত")


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
